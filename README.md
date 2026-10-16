# AutoGpuAffinity · 安全分支 / safe fork

> 在原版基础上修改的 AutoGpuAffinity：**在不关闭显示适配器的前提下**，测试每个 CPU 核心运行 GPU 负载的表现。

原项目 [valleyofdoom/AutoGpuAffinity](https://github.com/valleyofdoom/AutoGpuAffinity) 已停止维护。本仓库是在原作者许可下发布的修改版（fork），沿用 GPLv3 许可。

---

## 原版的问题

原版在测试每个核心之前，都会把 `Win32_VideoController` 列出的**所有**显示适配器依次「禁用 → 启用」，好让新的 GPU 中断亲和性生效 —— 它遍历的是全部适配器，包括正在输出画面的那一块。在多显卡 / 装有虚拟显示驱动的机器上（例如 RTX 5090 + Intel 核显 + MuMu / 向日葵之类的虚拟显示适配器），只要某次驱动重新启用失败，结果就是**黑屏**。原作者在自己的 README 里也提示过这个风险。

本分支保留了原有功能，但把它拆成三个互不干扰的入口。

## 三个入口

| 入口 | 需要管理员 | 重启显示设备 | 说明 |
| --- | --- | --- | --- |
| **1-safe-mode.bat** | 否 | **从不** | 完全不碰显示设备。用 `/affinity` 掩码把跑分程序绑定到目标核心，测的是「这个核心跑 GPU 负载表现如何」。**建议先用这个。** |
| **2-igpu-mode.bat** | 是 | 只重启独显 | 显示器接核显输出时使用。真正写入 GPU 中断亲和性策略，然后只重启 `PCI\VEN_10DE`（NVIDIA），不会黑屏。 |
| **3-original-mode.bat** | 是 | 全部非虚拟适配器 | 原版行为，用于复现 / 对比。有黑屏风险，需手动输入 `YES` 确认。 |
## 快速开始

不需要 `pip install`，没有任何第三方依赖，只要有 **Python 3.10+**：

```
双击 1-safe-mode.bat
```

跑完会在 `captures\AutoGpuAffinity-<日期时间>\CSVs\` 下为每个核心生成一个 `CPU-<n>.csv`，并在终端打印与原版完全一致的对比表格（Max / Avg / Min / STDEV / 各百分位 / 1%·0.1%·0.01%·0.005% Low）。

只想重新看某次结果的表格：

```
py main.py --analyze captures\AutoGpuAffinity-260101120000\CSVs
```

## 命令行

```
py main.py [--config <file>] [--analyze <csv dir>] [--apply-affinity <cpu>]
           [--list-devices] [--list-gpus] [--dry-run] [--restore-display]
           [--restart-driver | --no-restart-driver] [--version]
```

几个常用的：

- `--list-devices` — 列出显示适配器，标出虚拟 / 已禁用（只读）
- `--list-gpus` — 列出 Vulkan 适配器，标出独显（只读）
- `--dry-run` — 只打印本次会做什么，不做任何修改
- `--restore-display` — **黑屏救砖**：把处于 Disabled 状态的显示设备重新启用
## 相对原版的改动

- 不再依赖 `psutil` / `WMI`（`psutil` 在 Python 3.14 上装不上），改用 `taskkill` 和自写的 ctypes `setupapi` 封装
- 亲和性策略路径不再硬编码 `ControlSet001`，改为读取 `HKLM\SYSTEM\Select\Current`
- 写入前先备份原策略到 `affinity_backup.json`，可恢复
- 显示适配器只枚举一次、跳过虚拟显示驱动，并可用 `affinity_devices` 白名单精确指定重启哪块卡
- 新增安全模式：完全不重启显示设备
- 新增 GPU 选择：把跑分程序注册进 Windows 的「每应用 GPU 偏好」（`GpuPreference=2;`，写 HKCU，可在 设置 → 系统 → 屏幕 → 显示卡 里撤销），并可用 `[liblava] physical_device=<n>` 强制指定 Vulkan 适配器
- 开机横幅改为本分支说明；`.bat` 入口自动处理提权

原理分析、改动细节和全部只读验证记录见 **[docs/README-safe.md](docs/README-safe.md)**。

## 许可

GPLv3，与原项目一致。原项目作者 valleyofdoom，本分支由 nieTVT 维护。原项目已不再维护，本分支在原项目许可（GPLv3）下发布。

---
# AutoGpuAffinity · safe fork (English)

> A modified build of AutoGpuAffinity that benchmarks **every CPU core against a GPU workload without ever disabling your display adapter**.

Upstream [valleyofdoom/AutoGpuAffinity](https://github.com/valleyofdoom/AutoGpuAffinity) is no longer maintained. This is a modified fork, published with the original author's permission and under the same GPLv3 licence.

**The problem.** Upstream disables and re-enables *every* display adapter before testing each core. On a multi-adapter machine (RTX 5090 + Intel iGPU + virtual display drivers) a failed re-enable means a black screen — upstream's own README warns about it.

**The fix.** Three independent entry points instead of one risky loop.

| Entry point | Admin | Restarts display devices | What it does |
| --- | --- | --- | --- |
| `1-safe-mode.bat` (safe) | no | never | Pins the benchmark process to each core with an `/affinity` mask. **Start here.** |
| `2-igpu-mode.bat` (igpu) | yes | discrete GPU only | For a monitor plugged into the iGPU. Writes the real interrupt-affinity policy and restarts only `PCI\VEN_10DE`. |
| `3-original-mode.bat` (original) | yes | all non-virtual adapters | Upstream behaviour, for comparison. Black-screen risk; requires typing `YES`. |

No `pip install`, no third-party dependencies, Python 3.10+. Results land in `captures\AutoGpuAffinity-<timestamp>\CSVs\CPU-<n>.csv` and in the same comparison table as upstream. To recover from a black screen: `py main.py --restore-display` (as administrator). Full technical write-up: [docs/README-safe.md](docs/README-safe.md).

Licence: GPLv3. Upstream (c) valleyofdoom, fork (c) nieTVT. Upstream is no longer maintained; this fork is published with the original author's permission.