# 用官方 SDK GUI 看一次 PE 通信

这份指南用于 SDK 2.10.1 的四 PE 示例。GUI 查看运行结束后保存的 trace，
可以沿时间轴回看通信。它不暂停正在运行的程序，也不是逐指令调试器。
项目自带的浏览器 viewer 展示 C++ 逻辑模型；官方 GUI 读取 CSL simulator trace。

## 生成 trace 并打开 GUI

先按 [SDK_RUN.md](SDK_RUN.md) 配好 SDK 和 Lima 环境。在项目目录中运行：

```bash
make all
limactl start cs_sdk
make trace-scan
# 或者运行稳定筛选示例：
make trace-compaction
```

每条 trace 命令只运行对应固定示例的一次 launch，并用 C++ oracle 校验。
成功后会打印 `GUI_COMMAND`。复制该命令到终端，GUI 会读取刚生成的
`sdk-run` 目录。Scan 默认用端口 8000，Compaction 默认用 8001。
命令在前台运行，按 `Ctrl-C` 会停止 GUI。

当前 GUI helper 使用默认 `cs_sdk` VM、默认 SDK 路径及默认外部运行根目录；
它没有 suite runner 的路径覆盖选项。使用自定义环境时，应在对应 guest 中
运行该 SDK 自带的 GUI。默认路径见 [SDK_RUN.md](SDK_RUN.md)。

浏览器访问 [Scan GUI](http://127.0.0.1:8000/sdk-gui) 或
[Compaction GUI](http://127.0.0.1:8001/sdk-gui)。后端绑定 guest 回环地址，
需由 Lima 转发到主机回环地址。端口占用时先检查已有页面或停止旧后端。

## 在 GUI 中找到第一条消息

1. 初次出现非必要 cookie 选项时选 **Decline All**。
2. **Work directory** 填入 `GUI_COMMAND` 中该例子的绝对 `sdk-run` 路径。
3. **Settings** 中确认 compile 为 `out`、run 为 `.`（该目录包含 `simfab_traces/`），通常会自动发现。等待加载完成。
4. 打开 **Timeline** 开关，colors 会变成单选。先选一个 color，再**双击物理 PE**；每次切换后等当前请求完成。
5. 先点 **Zoom to fit**，再用 `+` 放大，拖动红色时间窗口到有事件的区间。空白位置不等于没有通信，可能只是当前窗口没有覆盖事件。
6. 点击时间线条目，再点 **Debug**，查看 `direction` 和 `data`。先用 Scan 的 **color 0、物理 PE (4,1)** 开始，查看东向发送事件。

应用只有一排四个 PE。编译偏移为 `(4,1)`，所以逻辑 PE0–3 对应 GUI 的 **(4,1)、(5,1)、(6,1)、(7,1)**。其余区域包括 SDK memcpy 所用设施，第一遍先看这四个 PE。Color 是路由通道编号，不是输入数据的数值。

### Compaction 西向时间轴为空时

如果西向 color 2/3 的时间轴为空，先检查原始 wavelet 表。空白时间轴本身不能证明没有发送；也可能是时间窗口或 GUI 转换问题。可以这样查看：

1. 关闭左侧 **Timeline**，双击物理 PE **(7,1)**，等下面的表加载。
2. 在 **Wavelet Trace** 面板的 **Color Filter** 选择 **3**。左侧路由颜色勾选与这个表格筛选是两项独立设置。
3. 本例应有四个 Data：`1, 3, 7, 7`，分别是一个 count header，以及 `(rank=3, value=7, original_index=7)`。这与上方路由图及下面的手算示例相配。

方向以 CSL 路由和相邻 PE 的匹配记录核对。这里的表格用于检查通信内容，
不能作为硬件性能测量。

## 两个固定输入各看什么

**Scan：观察 carry 从左向右接力。** 输入 `[2,-1,3,4,5,-2,0,1]`，每 PE 两个数。

| 逻辑 PE | 物理坐标 | 本地输入 | 本地和 | 收到的 carry | 本 PE 最终输出 |
| --- | --- | --- | ---: | ---: | --- |
| 0 | (4,1) | 2, −1 | 1 | 0 | 2, 1 |
| 1 | (5,1) | 3, 4 | 7 | 1 | 4, 8 |
| 2 | (6,1) | 5, −2 | 3 | 8 | 13, 11 |
| 3 | (7,1) | 0, 1 | 1 | 11 | 11, 12 |

这些是输入的手算结果。完整输出为 `[2,1,4,8,13,11,11,12]`；应用通信向东使用交替的 **color 0/1**：PE0→1 用 0，PE1→2 用 1，PE2→3 用 0。结合 [Scan PE 源码](../src/device/pe.csl)，看本地扫描如何接上 `apply_carry` 和下一次发送。

**Compaction：先看计数向东，再看记录向西。** 输入 `[2,-1,5,5,-4,9,0,7]`，阈值为 `>=5`，每 PE 两条有效输入、三个物理输出槽。

| 逻辑 PE | 本地选中数 | 全局输出 offset | 最终持有的输出（值@原始下标） |
| --- | ---: | ---: | --- |
| 0 | 0 | 0 | 5@2, 5@3, 9@5 |
| 1 | 2 | 0 | 7@7 |
| 2 | 1 | 2 | 空 |
| 3 | 1 | 3 | 空 |

手算输出为 `[5,5,9,7]`，原始下标为 `[2,3,5,7]`。**Colors 0/1** 运送东向累计计数；**colors 2/3** 运送西向记录包：PE3→2 用 3，PE2→1 用 2，PE1→0 用 3。记录包先发 count header，再发 `(rank, value, original_index)` 三元组；本例向西经过三条链路的记录数依次是 1、2、3。可对照 [Compaction 手算说明](COMPACTION.md) 和 [PE 任务源码](../src/compaction/pe.csl)。

## 重新运行一次

在项目目录、VM 已启动的情况下，任选一条：

```bash
make trace-scan
make trace-compaction
```

每条命令只跑对应固定示例的一次 launch，复用现有 C++ oracle 和 SDK transport。成功后输出新的私有运行目录及可复制的 GUI 命令。先停止旧 GUI，再执行新命令并刷新页面，使用新的 `sdk-run` 路径；每个后端只绑定它自己的运行目录。生成物保存在 `~/.local/share/cerebras-lab/private-runs/`；oracle 单独放在 `sdk-run` 外。

原 `make simulator` / `make simulator-compaction` 仍是抑制 trace 的正确性套件。需要 GUI 时使用这里的 `trace-*` 命令。原始 traces、SDK 生成物及 GUI 截图保持私有，本页不保存运行周期或性能数据。

用完后，在对应终端按 `Ctrl-C` 停止 GUI；结束全部 SDK 工作时：

```bash
limactl stop cs_sdk
```

## 参考

[SDK GUI 文档](https://cerebras-sdk-docs-140.netlify.app/debug/sdk-gui)和
[Debugging Guide](https://cerebras-sdk-docs-140.netlify.app/debug/debugging)
位于版本化站点；其他 SDK 版本的控件或命令可能不同。本指南主要查看
wavelet；完整指令时间轴和源码关联不在验证范围内。
