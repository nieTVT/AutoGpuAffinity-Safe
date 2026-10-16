## v1.1.1-safe — 修复「安全模式无法采集帧时间」

**现象**：安全模式下 PresentMon 直接失败，Python 侧抛

```
subprocess.CalledProcessError: Command '[...PresentMon-1.10.0-x64.exe', ...]' returned non-zero exit status 6
```

**原因**：PresentMon 要靠启动一个 ETW 跟踪会话来采集帧时间，而 Windows 只允许**管理员**或 **`Performance Log Users` 组成员**这么做。它不是黑屏风险 —— 黑屏来自「禁用 / 启用显示设备」，而安全模式从不碰显示设备。

**本次修复**

- `1-safe-mode.bat` 现在会自动请求 UAC，和另外两个入口一致
- 开跑前增加权限自检（管理员身份，或 `whoami /groups` 中的 SID `S-1-5-32-559`）：不满足就立刻打印原因和处理办法并退出，不再跑到一半抛 traceback
- PresentMon 增加超时保护（`benchmark_duration + 120` 秒）：万一跟踪会话卡死，不会再让整个跑分无限期挂起
- 修复安全模式下仍会尝试清除 HKLM 中断亲和性策略、导致 `PermissionError: [WinError 5] Access is denied` 的问题
- 补上默认 `config.ini`（内容等同安全模式），裸跑 `py main.py` 不再报 `config file not found`

**不想每次提权？** 用一次管理员权限执行 `net localgroup "Performance Log Users" %USERNAME% /add`，注销后重新登录，之后安全模式就能以普通用户运行。

---

在原版基础上修改的安全分支：**在不关闭显示适配器的前提下**测试每个 CPU 核心运行 GPU 负载的表现。

## 三个入口

| 入口 | 需要管理员 | 重启显示设备 |
| --- | --- | --- |
| `1-safe-mode.bat` | 否 | **从不** —— 推荐先用这个 |
| `2-igpu-mode.bat` | 是 | 只重启独显 `PCI\VEN_10DE` |
| `3-original-mode.bat` | 是 | 全部非虚拟适配器（原版行为，有黑屏风险） |

## 相比原版 1.0.0

- 不再遍历并重启所有显示适配器 —— 这是原版黑屏的根因
- 移除 `psutil` / `WMI` 依赖（`psutil` 在 Python 3.14 上装不上），改用 `taskkill` 与自写的 ctypes `setupapi` 封装
- 亲和性策略路径从硬编码 `ControlSet001` 改为读取 `HKLM\SYSTEM\Select\Current`，写入前备份到 `affinity_backup.json`
- 新增 `gpuinfo.py`：ctypes 枚举 Vulkan 适配器，可把跑分程序注册进 Windows「每应用 GPU 偏好」，也可用 `[liblava] physical_device=<n>` 强制指定独显
- 新增只读参数 `--list-devices` / `--list-gpus` / `--dry-run`；`--restore-display` 用于黑屏救砖
- 启动横幅改为本分支说明

## 环境要求

Python 3.10+，无第三方依赖。双击 `1-safe-mode.bat` 即可开跑。
---

## English

A safe fork that benchmarks every CPU core against a GPU workload **without ever disabling your display adapter**.

- **`1-safe-mode.bat`** — no admin, recommended. Never touches the display device; pins the benchmark process to each core with an `/affinity` mask.
- **`2-igpu-mode.bat`** — admin. Writes the real GPU interrupt-affinity policy and restarts only the discrete adapter.
- **`3-original-mode.bat`** — admin. Upstream behaviour, kept for comparison; black-screen risk.

### Changes since upstream 1.0.0

- no longer disables/re-enables every display adapter (the root cause of upstream's black screens)
- `psutil` / `WMI` dependencies dropped (`psutil` does not build on Python 3.14); `taskkill` and a self-contained ctypes `setupapi` wrapper instead
- affinity policy path read from `HKLM\SYSTEM\Select\Current` instead of a hardcoded `ControlSet001`, with a JSON backup before any write
- new `gpuinfo.py`: ctypes Vulkan enumeration, per-app Windows GPU preference registration, optional `[liblava] physical_device=<n>` override
- new read-only flags `--list-devices` / `--list-gpus` / `--dry-run`; `--restore-display` to recover from a black screen

No `pip install`, no third-party dependencies, Python 3.10+.

Upstream [valleyofdoom/AutoGpuAffinity](https://github.com/valleyofdoom/AutoGpuAffinity) is no longer maintained; this fork is published with the original author's permission under the same GPLv3 licence.