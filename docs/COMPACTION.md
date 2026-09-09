# MeshCompact: 从前缀和到分布式稳定筛选

MeshCompact 在 MeshScan 的前缀和基础上实现稳定筛选与跨 PE 打包。
本文用一个四 PE 例子说明输出位置、变长记录包和完成条件。

## 它解决什么问题

从一批记录中挑出符合条件的记录，去掉空洞，同时保持原始顺序。
这类操作叫 **stable stream compaction（稳定流压缩/筛选）**。
可以把它当作传感器阈值筛选、稀疏数据预处理或活跃任务列表的一个小型基础部件；
本项目没有声称已经接入真实传感器、数据库或生产任务调度器。

本版条件固定为 `value >= threshold`，阈值可在每次运行时改变。

```text
原始值：  [2, -1, 5, 5, -4, 9, 0, 7]
原始下标：[0,  1, 2, 3,  4, 5, 6, 7]
阈值：5
结果值：  [5, 5, 9, 7]
结果下标：[2, 3, 5, 7]
```

两个 5 来自不同位置。只检查数值会漏掉交换顺序、重复输出等错误，所以结果保留原始下标。

## 与扫描的区别

| 扫描基线 | 本次扩展 |
| --- | --- |
| 一个值进来，一个前缀和出去 | 输出数量取决于数据，可能从 0 到全部保留 |
| 向东传一个 carry | 向东传计数，再向西发送变长记录包 |
| 原地修改每个 PE 的输入 | 计算全局输出位置，把记录搬到另一 PE 的输出槽 |
| 主要检查数值和 carry | 同时检查稳定顺序、无遗漏/重复、路由、包计数、padding 与完成状态 |

前缀和没有被丢掉，而是变成筛选算法内部的地址分配步骤。

## 三个阶段，只有一次设备调用

1. **Local select**：每个 PE 只看自己的有效输入，统计选中多少条。
2. **Count prefix → east**：累计选中数量。每个 PE 得到前面已选中多少条 `offset`。
   本地第 `j` 条选中记录的全局输出位置是 `offset + j`（j 从 0 开始）。
3. **Pack/scatter ← west**：最右 PE 开始回传记录。每个包先传记录数量，再传
   `(输出位置, 值, 原始下标)` 三元组。每个 PE 留下属于自己输出槽的记录，其他继续向西。

```text
本地筛选 → PE0 ──累计计数──► PE1 ──► … ──► PE(P−1)
           PE0 ◄──变长记录── PE1 ◄── … ◄── PE(P−1)
            │                │              │
            └────各自连续输出槽 + blocking D2H────┘
```

Python 只负责 H2D、一次阻塞 `compute`、D2H，不在主机上替设备筛选或搬运答案。
计数和打包之间没有主机往返；设备任务的接收/发送完成事件推动下一阶段。
完整矩形的阻塞 launch 和每 PE 的完成标记共同支持回读；单个发送完成不是全局硬件屏障。

## 手算一次四 PE 的例子

每个 PE 有三个物理输出槽（`chunk_size=3`），但上面八条输入按四个 PE 均分，每 PE 两条。

| PE | 输入 | 本地选中数 | offset | prefix_count | 最终持有的输出 |
| --- | --- | ---: | ---: | ---: | --- |
| 0 | 2, −1 | 0 | 0 | 0 | 5@2, 5@3, 9@5 |
| 1 | 5, 5 | 2 | 0 | 2 | 7@7 |
| 2 | −4, 9 | 1 | 2 | 3 | 空 |
| 3 | 0, 7 | 1 | 3 | 4 | 空 |

`5@2` 表示“值为 5，原始下标为 2”。PE1 的两个 5 最后要搬到 PE0，
PE3 的 7 最后要搬到 PE1。这就是实际的跨 PE 数据重分布，不是最后由 Python 拼好。

对应的向西发送记录数是 `[0,3,2,1]`，从东边接收记录数是 `[3,2,1,0]`。
这些数字是本例的逻辑计数，不是硬件性能测量。

## 为什么只需要向西搬

令每个 PE 的物理容量为 C。平衡连续输入分块的第 p 个 PE，其原始下标 i 小于 `(p+1)C`。
筛选不会增加记录，因此保留记录的新下标 r 满足 `r <= i`。
输出归属 PE 是 `floor(r/C)`，所以一定不大于 p：只能留在本地或向西移动。

这个性质依赖当前输入布局、稳定筛选和固定输出槽布局，不是任意分布式 scatter 都成立。
如果以后改成排序、扩张或任意哈希分区，需要重新设计路由，不能直接套用。

## 协议与不变量

- 计数使用颜色 0/1，记录返回使用颜色 2/3；输入/输出队列 2/3 固定绑定，不在运行中换色。
- 包头是记录数，payload 长度是 `3 * count` 个 32-bit words。零条记录只发包头。
- 每个全局输出 rank 恰好写一次，原始下标严格递增。
- `prefix_count = offset + local_count`；最右 PE 的 prefix_count 是全局结果条数。
- `local_count + received_count = output_count + forwarded_count`。
- PE0 不再向西发送；最右 PE 不从东边接收。
- 输入（包括非零 poison padding）不能被修改；未使用输出槽保留初始化 poison / −1。
- 每次 launch 清空内部状态；不依赖前一次输出恰好为零。
- 受检查的非法包头、rank、index 或重复写槽会设置 `protocol_error`；
  任意 payload 值损坏不一定触发设备标记，仍须依靠 host 与 C++ 的逐项比较。
  这不是对损坏 fabric 的容错实现。
  传输损坏可能导致超时，主机必须判定失败，不得产出成功报告。

## 运行与读代码

```bash
make compaction
./build/meshcompact-reference --pes 4 --chunk-size 3 --threshold 5 --values 2,-1,5,5,-4,9,0,7
make test-compaction

# 真实 SDK simulator；使用已有安装，不是浏览器动画
limactl start cs_sdk
make simulator-compaction
limactl stop cs_sdk
```

短验证：

```bash
python3 -B tools/run_compaction.py --widths 2 --random-cases 0
```

读代码顺序：

1. [C++ oracle](../src/compaction.cpp)：简单串行筛选与独立路由模型。
2. [CSL layout](../src/compaction/layout.csl)：双方向颜色与队列。
3. [CSL PE](../src/compaction/pe.csl)：计数、包头、payload、接收/发送任务。
4. [host contract](../host/compaction_contract.py)：packing 和精确验算。
5. [SDK runner](../host/compaction_device_run.py)：传输与生命周期。
6. [private orchestrator](../tools/run_compaction.py)：源快照、哈希、超时、失败记录。

## 验证范围与限制

标准布局是 1/2/3/4/8 PE，每 PE 16 槽；接口允许 1–8 PE、1–64 槽。
接口范围不等于每种组合都跑过 SDK；每次运行的具体布局与结果由运行报告记录。
空输入、没有选中、全部选中、重复数值、阈值相等、负数和完整 int32 范围都应可处理。
不同于求和，这个算法不累加输入值，因此不受扫描基线的局部和溢出约束。

每个 PE 的转发缓冲区按全批容量界定：这仍是 bounded batch，不是无限实时数据流服务。
链式 count 和逐 PE 转发有顺序依赖，最坏数据转发量为 O(P×N)，不是优化过的集体通信库。
这里只验证正确性，不提供硬件延迟、吞吐、speedup 或生产部署结论。
SDK、生成物和原始运行记录保存在仓库外。

遇到停机、低于 4 GiB 可用空间、编译错误、超时或 mismatch，保留私有 run 目录。
`--prepare-only` 只准备输入，不代表 simulator 通过。

官方 API 背景：[DSD 长度与异步操作](https://sdk.cerebras.ai/csl/language/dsds)、
[路由教程](https://sdk.cerebras.ai/csl/tutorials/gemv-06-routes-1)。实际编译以本机匹配 SDK 为准。

## 练习

1. 用 `[1,9,2,8,3,7]`、阈值 7、三个 PE、每 PE 三槽手算所有 offset 和最终归属。
2. 独立加一个“只有最后一个 PE 选中”的测试，预测每条返回链路传几条记录。
3. 自己把谓词扩成一个明确的新规则（例如闭区间筛选），同时改 C++、CSL 和测试。

修改谓词前先保存当前测试结果，再比较新旧实现处理同一输入的差异。
