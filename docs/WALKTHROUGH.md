# MeshScan：运行、读代码、复写

MeshScan 把整数前缀和分配给一行 PE：各自计算局部结果，再从西向东传递 carry。本指南先说明运行入口，再沿着数据读代码，最后用三个小练习检查分块、通信与验证逻辑。

网页入口：在项目目录运行 `make tutorial`，打开本地教学页，再点“读代码与复写”。网页是固定概念图解，不读取私有 simulator 输出。

## 1. 这个 SDK 到底是什么？

SDK 是开发工具集合。这里用的是低层 Cerebras CSL SDK，不是 Cerebras 聊天模型 API。

- `cslc`：把 CSL 源码编译成设备程序。
- `cs_python` / `SdkRuntime`：在 SDK 环境运行 Python host，加载程序、复制数据、启动计算、读取结果。
- Fabric simulator：在没有真实 WSE 的本地环境执行和检查程序。它不能把 Mac 变成 WSE，也不能证明硬件性能。
- Lima / Rosetta / Apptainer：让本机 Apple Silicon 上的 Linux VM 能承载安装好的 SDK 工具链。它们是运行环境，不是我们写的 scan 算法。

官方工具角色与 Apple Silicon 安装路径参考 [Cerebras installation guide](https://sdk.cerebras.ai/installation-guide)。设备通信概念参考 [Routes and Fabric DSDs](https://sdk.cerebras.ai/csl/tutorials/gemv-06-routes-1)。本项目使用 SDK 2.10.1 接口；运行时应对照匹配该 release 的示例/API。

## 2. 三种运行不能混为一谈

在本项目根目录运行：

```sh
make test
./build/meshscan-reference --pes 2 --values 3,-1,4,2,5,-2,1,3
```

第二条是 C++ CPU 模型，会输出 JSON；其中 `output` 应为 `[3,2,6,8,13,11,12,15]`。输入被分成两段，PE 0 的 local total 为 8，PE 1 为自己的每个 local prefix 加上 carry 8。

- `make tutorial`：固定教学 trace，完全静态，不计算另一份 scan。
- `make viewer`：loopback Python server 调用真正的 C++ CLI，展示它的 logical-PE trace。
- `make simulator`：SDK 编译与模拟器验证。Mac 上的 runner 默认使用名为 `cs_sdk` 的 Lima VM；先运行 `limactl start cs_sdk`，结束后可运行 `limactl stop cs_sdk` 归还内存。

模拟器入口要求单独安装并配置 SDK 环境，并满足其许可和磁盘要求。其他 VM 名、SDK 路径及运行参数见 `python3 tools/run_simulator.py --help`。源码快照、编译输出、原始日志和验证报告保存在仓库外；`--prepare-only` 只准备文件，不执行 SDK。测试覆盖范围见 [VALIDATION.md](VALIDATION.md)。

## 3. 跟着数据读，而不是从第一行读到最后一行

```text
Mac: 输入 + C++ oracle → 验证域检查 / expected
                  │
                  ▼
Linux/SDK: Python host → H2D → PE local scan
                              │
                         west → east carry
                              │
                          所有 PE 完成
                              │
Mac: exact validation ← report / D2H output
```

阅读顺序：

1. [`src/reference.cpp`](../src/reference.cpp)：`serial_inclusive_scan` 回答数学结果是什么；`simulate_linear_mesh` 回答如何按 PE 分块与传 carry。先读 public interface [`include/meshscan/reference.hpp`](../include/meshscan/reference.hpp) 可快速了解 trace 字段。
2. [`tools/oracle_bridge.py`](../tools/oracle_bridge.py)：Python 调 C++ subprocess，并验证 JSON 数据契约，不另写一个 Python scan。
3. [`tools/run_simulator.py`](../tools/run_simulator.py)：Mac 侧调度器，预检、私有快照、compile/run、运行记录与最终比较。Mac 编译的 C++ 可执行文件不能直接在 Linux 容器内运行，因此对照计算留在 Mac。
4. [`host/device_run.py`](../host/device_run.py)：SDK 内的 Python host。找加载、H2D、blocking launch、D2H，以及 `finally` 清理；算法不应该偷偷在 host 里替设备完成。
5. [`src/device/layout.csl`](../src/device/layout.csl)：编译时决定 PE 矩形、每 PE 参数、相邻路由与导出符号。
6. [`src/device/pe.csl`](../src/device/pe.csl)：每个 PE 的局部程序。本地数组、有效长度、carry buffer、receive/apply/send 的任务衔接与退出路径。
7. `tests/` 和 host validation cases：输入域、错误分支、固定案例和随机 seed。测试结果必须指明是 CPU、host contract 还是实际 fabric simulator。

## 4. 三个关键设计点

### 固定传输槽位与有效长度分开

5 个输入、3 个 PE 的 balanced partition 是 `[2,2,1]`，不是随便填满一个 PE 再填下一个。每个 PE 在一次区域拷贝里使用相同的物理 slot 数；`valid_count` 决定真实输入长度。

```text
PE 0: [3,-1]        valid_count 2
PE 1: [4, 2]        valid_count 2
PE 2: [5] + padding valid_count 1
```

Padding 不是额外输入。计算和拼回输出都必须服从 valid count。一个输入分给 4 个 PE 时，后三个 PE 没有本地输入，却仍要参与 carry 的转发/完成路径。整个输入为空则由 host fast path 处理。

### 异步发送与完成通知不是同一时刻

`@mov32` 的异步通信发起后，由完成事件衔接后续任务。不能在最终数据移动尚未完成时，告诉 host 已经结束。

每个 PE 的完成路径都必须存在，包括空 PE、首 PE、尾 PE 和只有一个 PE 的布局。`unblock_cmd_stream()` 结束的是本 PE 的工作，不是凭空保证所有 PE 完成的全局 barrier。host 需要等待全矩形的 blocking launch 结束，之后才能 D2H。

反复 launch 可以抓出 carry 未重置、旧有效长度残留、queue 生命周期问题。第一次运行通过，并不能证明下一次输入仍然正确。

### 参考答案与设备实现保持独立

最终每个有效输出必须与 checked-int32 C++ oracle 相等，不能只看最后的 total。正负混合、非整除长度、空 PE、固定 seed 随机输入都能暴露不同错误。

int32 的范围契约是：serial prefix、partition-local prefix、carry 都可表示。分组之后的局部和也可能溢出，即使全局前缀能表示；这类输入应在 host 预检阶段拒绝，不叫 simulator correctness mismatch。设备程序不是通用带任意精度溢出恢复的库。

## 5. 把整条链路连起来

输入按原顺序分到一行 PE，每块先做局部累计，再将西侧传来的 carry 加到每个有效结果上。最后一个 PE 完成后，host 等待完整矩形的 blocking launch 返回，再读回输出。host 用 C++20 oracle 的输出逐项核对结果，并检查每个 PE 的 carry、padding 和完成标记。这样既能发现算错，也能发现读回过早或状态残留。

## 6. 三个练习

保留原版本用于对照。每次只改变一个条件，先预测，再运行，最后解释差异。

1. **一张手算记录**：输入 `[4,-2,7,0,-3]`，用 3 个 PE。写出每块 input/local prefix/carry/global output，再用 CLI 核对。记录预测错在哪里。
2. **一个新增回归测试**：选“一个值、八个 PE”或“同 runtime 的两次 launch 使用不同有效长度”，写出它会抓哪种 bug。保留精确输入/seed 和实际运行结果。
3. **一个可审查的代码 diff**：先关闭参考，复写 C++ balanced partition 或 local scan，保留通信外壳；熟悉之后再改 CSL 的局部累计。只改变一部分，跑测试，再解释修改。

记录格式：

```text
我修改了：
为什么这样设计：
我预测会发生：
实际验证结果：
我仍不确定：
```

最后在教学页回答关于分块、carry、不变量和瓶颈的四个问题，再将解释复制到自己的笔记。页面不自动保存或评分；记录尚未理解的地方，留作下一次运行时要检查的问题。

## 7. 这个项目的边界

它能展示一个窄范围、端到端的 accelerator 学习实现：CSL、显式 PE 通信、Python host、C++ oracle 和可复现正确性验证。它不等于 Linux driver、生产部署、硬件实测或高性能 scan；线性 carry chain 仍有随 PE 数增长的依赖深度。树式 scan 可以作为后续研究方向，但没有做过的比较不写成性能结论。
