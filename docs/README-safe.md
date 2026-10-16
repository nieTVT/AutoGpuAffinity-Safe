# AutoGpuAffinity 修复版说明（安全模式）

修复版本号：`1.1.0-safe`（原版为 `1.0.0`）
位置：`D:\TOOL\AutoGpuAffinity\main.py`、`config.py`、`setupapi.py`、`framerate.py`、`consts.py`

> `AutoGpuAffinity.exe` 是**原版未修复的构建，请不要运行**。用下面的 `py main.py` 方式运行修复版。

---

## 一、原工具的测试原理

对每一个要测的 CPU 核心，原版做这几件事：

1. **写中断亲和策略**到注册表：

   `HKLM\SYSTEM\CurrentControlSet001\Enum\<显卡实例ID>\Device Parameters\Interrupt Management\Affinity Policy`

   - `DevicePolicy = 4`（DWORD，= IrqPolicySpecifiedProcessors）
   - `AssignmentSetOverride = 1 << cpu` 的 8 字节小端值（BINARY）

2. **禁用再启用显示适配器**（`DICS_DISABLE` → 等 2 秒 → `DICS_ENABLE` → 等 2 秒）。
   必须重启设备的原因：中断亲和性**只在设备启动时被读取**，用户态没有任何运行时接口可以改它。

3. 用 `/affinity <掩码>` 启动基准程序（`bin\liblava\lava-triangle.exe`），预热 `cache_duration` 秒。

4. 用 PresentMon 采集 `benchmark_duration` 秒的帧时间，算出 12 项指标。

5. 全部核心跑完后**删除策略并再次重启驱动**还原。

一句话：**把 GPU 中断固定到某个核心，测哪个核心的帧率/延迟表现最好**。

---

## 二、为什么你会黑屏（已定位并验证）

原版第 316 行：

```python
hwids_gpu: list[str] = [gpu.PnPDeviceID for gpu in wmi.WMI().Win32_VideoController()]
```

然后 `apply_affinity()` 会对这个列表里的**每一块**显示设备都执行一次「禁用 → 2 秒 → 启用」。

这台机器上 `Win32_VideoController` 返回了 **5 块**显示设备：

| 设备 | 实例 ID | 类型 |
|---|---|---|
| AskLinkIddDriver Device | `ROOT\DISPLAY\0001` | 虚拟 IDD |
| Intel(R) Graphics | `PCI\VEN_8086&DEV_7D67...` | 真实 GPU（核显） |
| NVIDIA GeForce RTX 5090 D v2 | `PCI\VEN_10DE&DEV_2B8C...` | 真实 GPU |
| MuMu Virtual Display Adapter | `ROOT\DISPLAY\0000` | 虚拟 IDD |
| GameViewer Virtual Display Adapter | `ROOT\DISPLAY\0002` | 虚拟 IDD |

**原版会把包括 5090 在内的全部 5 块适配器一次性禁用再启用**，中间只要有一环失败或超时，就黑屏。
更严重的是：如果在「已禁用」和「已启用」之间崩溃，设备会**永久停留在已禁用状态**。

而且这台机器现在就有这个痕迹 —— `ROOT\DISPLAY\0001 (AskLinkIddDriver)` 正处于
**已禁用（CM_PROB_DISABLED = 22）** 状态，这是上一次运行中途失败留下的。

（用只读命令 `py main.py --list-devices` 就能自己看到。）

---

## 三、修复内容

### 1. 默认进入安全模式，绝不碰显示设备

`config.ini` 新增 `restart_driver=false`（默认）。安全模式下：

- **完全不调用** `SetupDiCallClassInstaller` / 禁用 / 启用；
- 只用 `start "" /affinity <掩码>` 把基准进程**钉在目标核心**上跑；
- **从不接触显示设备**，因此不可能黑屏。（不过采集帧时间仍需提权，见第九节）

### 2. 即使开启重启模式，也只作用于筛选后的设备

- 自动跳过所有 `ROOT\DISPLAY\*` 虚拟 IDD（MuMu / GameViewer / AskLink 之类没有 GPU 中断可言）；
- 自动跳过已经处于禁用状态的设备（不把坏设备卷进重启循环）；
- `affinity_devices` 可再按实例 ID 子串过滤，例如只允许 `["PCI\VEN_10DE"]`（只动 5090）；
- 必须显式 `restart_driver=true` 或 `--restart-driver`，需要管理员权限；
- 启动前**打印目标设备并要求输入 `yes` 二次确认**；
- 改动前把原有亲和策略备份到 `affinity_backup.json`，可用 `--restore-display` 还原。

### 3. 其他

- 读写**当前控制集**（读 `HKLM\SYSTEM\Select\Current`），不再硬编码 `ControlSet001`；
- 去掉 WMI / psutil 依赖（改用 `setupapi.dll` / `cfgmgr32.dll` / `taskkill`），
  所以 `py main.py` 在裸 Python 3.14 上直接可跑，不需要 `pip install`；
- 修掉原版 `SetupDiGetClassDevsW` 返回值 `== -1` 的不可靠判断；
- 顺手修掉 `--apply-affinity 0` 因 `if args.apply_affinity:` 为假而失效的 bug。

---

## 四、怎么用

在 `D:\TOOL\AutoGpuAffinity` 目录下：

```bat
py main.py --list-devices     :: 只读：列出所有显示设备及其状态
py main.py --dry-run          :: 只读：打印本次基准计划，不启动任何东西
py main.py                    :: 安全模式跑基准（显示设备不动；采集需管理员）
```

可选 / 高级：

```bat
py main.py --restore-display  :: 需要管理员：重新启用被禁用的显示设备 + 还原备份的亲和策略
py main.py --restart-driver   :: 需要管理员：进入原版"重启驱动"模式（只动筛选出的设备，会二次确认）
py main.py --no-restart-driver:: 强制安全模式（覆盖 config.ini）
```

`config.ini` 相关项：

```ini
restart_driver=false          ; false=安全模式（推荐）；true=原版行为，需要管理员
affinity_devices=[]           ; 空=所有"真实的、未禁用的"显示适配器；虚拟 IDD 永远跳过
                              ; 例：["PCI\VEN_10DE"] 只允许动 NVIDIA（单个反斜杠）
```

---

## 五、必须知道的一点（重要）

安全模式**不修改 GPU 的中断亲和性**，所以它测到的是：

> 「这个核心跑 GPU 负载时能拿到多少帧 / 稳不稳」

而不是：

> 「GPU 中断绑定到这个核心后能带来多少收益」

后者在原理上必须写中断亲和策略 + 重启驱动（即 `restart_driver=true`）。
而且**只要重启作为主显示的那块适配器，就一定会短暂黑屏**，这是该功能固有的代价，
原版 README 自己也承认「重启测试期间显卡驱动可能无响应」。
所以：**先用安全模式拿到可用的核心排名**；只有在明确知道风险、并且可以接受黑屏时，才考虑要针对单块显卡开启重启模式。

---

## 六、本次做过的验证（全部只读 / 无需管理员）

| 验证 | 结果 |
|---|---|
| `--help` / `--version` | 正常，退出码 0 |
| `--list-devices` | 正确列出 5 块设备，并正确标出 `ROOT\DISPLAY\0001` 为 virtual + DISABLED |
| `--dry-run` | 正确打印计划（安全模式，不启动任何程序） |
| `--analyze <旧CSV目录>` | 正常解析 PresentMon CSV 并输出指标表 |
| `/affinity` 掩码钉核 | CPU 0/22/23 → 实测进程掩码 `0x1` / `0x400000` / `0x800000`，**全部 PASS**，且未使用管理员权限 |
| `py_compile` 全部源文件 | OK |

（辅助脚本：`_affinity_probe.py`、`_affinity_test.py`、`_probe_devices.py`，都是只读验证工具，可删。）
原 `config.ini` 已备份为 `config.ini.orig.bak`。
---

## 七、你现在的情况：显示器已经改由核显输出

这正好把「重启 5090」的最大风险消掉了 —— 因为 5090 不再承载画面，重启它不会让你失屏。

### 但有一个前提必须确认：基准程序要真的跑在 5090 上

输出切到核显后，Windows 很可能把**核显当成主适配器**，那样 lava-triangle 可能渲染在 Intel 上，
你就变成在测核显中断，测 5090 的亲和性就毫无意义了。

- **确认**：跑的时候打开任务管理器 → 性能 → 逐个看 GPU，哪个 GPU 的「3D」占用升高。
- **强制**：设置 → 系统 → 屏幕 → 显示卡（图形设置）→ 添加桌面应用
  `D:\TOOL\AutoGpuAffinity\bin\liblava\lava-triangle.exe` → 选项 → **高性能**（= 5090）。
- **建议**：顺便把 `config.ini` 里 `[liblava] fullscreen=false`，避免全屏独占在核显输出上切换分辨率。

### 推荐流程

**第 1 步：先用安全模式确认整条流水线能出表格（以管理员运行，但显示设备不动）**

```bat
cd /d D:\TOOL\AutoGpuAffinity
run-safe.bat
```

跑完后屏幕最后会打印 12 列指标的跑分表格，CSV 落在 `captures\AutoGpuAffinity-<时间戳>\CSVs\`。

**第 2 步：确认没问题后，再开真正的「GPU 中断亲和性」测试**

先改 `config.ini`：

```ini
restart_driver=true
affinity_devices=["PCI\VEN_10DE"]     ; 只允许动 NVIDIA 5090，核显和虚拟 IDD 一律不碰
```

然后**用管理员身份**打开 CMD 或 PowerShell：

```bat
cd /d D:\TOOL\AutoGpuAffinity
py main.py
```

预期表现：

1. 打印 `Driver Restart  True`、`Affinity Targets  1`；
2. 列出唯一的 5090 实例 ID，要求你输入 `yes` 二次确认；
3. 把原有亲和策略备份到 `affinity_backup.json`；
4. 每个核心：写策略 → 禁用/启用 5090 → 钉核启动基准程序 → PresentMon 采样；
5. 全部跑完 → 还原策略 → 打印跑分表格。

**出问题的恢复**（管理员）：`py main.py --restore-display`
它会重新启用所有处于「已禁用」状态的显示设备，并还原备份的亲和策略。

### 提醒

即使画面在核显上，如果 NV 驱动重初始化失败，5090 仍可能卡在「已禁用」，这时任务管理器/设备管理器里看不到它。
所以第 2 步之后建议顺手用 `py main.py --list-devices` 复查一次，确认 5090 状态还是 `ok`。

---

## 八、安全模式下的 GPU 选择（只跑独显，不碰当前显示设备）

安全模式本来就不动显示设备，但还有一个隐患：**Windows 可能把基准程序渲染在「当前正在输出画面的那块 GPU」上**，
也就是你的 Intel 核显。那样测出来的每核心差异跟 5090 没关系。现在补上了两件事。

### 1. 新增 `--list-gpus`（只读，随时可跑）

```bat
py main.py --list-gpus
```

本机实测输出：

```
  #0 Intel(R) Graphics [Intel, integrated]
  #1 NVIDIA GeForce RTX 5090 D v2 [NVIDIA, discrete]  <- discrete gpu

discrete gpu index 1 -> use [liblava] physical_device=1 in config.ini
```

它是用 `vulkan-1.dll` 现场枚举出来的（`gpuinfo.py`，纯 ctypes，无第三方依赖），
只创建一个临时 Vulkan 实例问一句「有哪些适配器」然后销毁，**不改任何驱动/设备状态**。

### 2. 新增 `[settings] force_discrete_gpu`（默认 `true`）

```ini
force_discrete_gpu=true
```

为 `true` 时，工具在开始跑分前会把基准程序登记进 Windows 的「每应用 GPU 首选项」——
就是 设置 → 系统 → 屏幕 → 显示卡 里那个「高性能 / 节能」设置，写的是同一个注册表键：

```
HKCU\Software\Microsoft\DirectX\UserGpuPreferences
  值名 = D:\TOOL\AutoGpuAffinity\bin\liblava\lava-triangle.exe
  值   = GpuPreference=2;      ; 2 = 高性能（独显）
```

- 只作用于**当前用户**，不需要管理员权限；
- 想撤销：在「显示卡」图形设置里把它改回「让 Windows 决定」，或删掉那条注册表值；
- `--dry-run` **不会**写这条注册表（已实测确认 dry run 无副作用）。

### 3. 新增 `[liblava] physical_device`（强制指定具体显卡）

```ini
physical_device=-1
```

- `-1`（默认）：不传参数，让 lava-triangle 自己选。liblava 内部本来就偏好独显，
  你观察到的「自动切进独显」就是这个机制；再加上上面的「高性能」登记，正常情况下就在 5090 上。
- `>= 0`：给基准程序附加 `--physical_device=<n>`，**强行**指定第 n 块 Vulkan 适配器。
  本机 `n=1` 就是 5090。已实测该分支输出：
  `Subject GPU  NVIDIA GeForce RTX 5090 D v2 (vulkan #1, forced)`

> ⚠️ 注意：`--physical_device` 的序号语义来自 liblava 自己（二进制里确实有这个参数），
> 我没有上游文档可以 100% 确认它用的就是这个枚举顺序。所以默认保持 `-1`（不强制、最保险），
> 只有在「任务管理器里确认渲染跑到了核显上」时才把它设成 1。

### 4. 跑分开始时的摘要多两行

```
        Subject GPU              NVIDIA GeForce RTX 5090 D v2 (auto, first discrete)
        GPU Preference           high performance (per-app preference)
```

**验证渲染到底落在哪块卡上**：跑的时候打开 任务管理器 → 性能 → 逐个 GPU 看哪一块的「3D」占用在动；
应看到 5090 的 3D 升高、Intel 的 3D 基本不动。

## 九、PresentMon 需要管理员权限（安全模式为什么也要提权）

安全模式**从不**禁用或启用显示设备，所以它不可能黑屏。但**采集帧时间**这一步仍然需要提权。

`bin\PresentMon\PresentMon-1.10.0-x64.exe` 要启动一个 ETW 跟踪会话才能记录 present 事件，而 Windows 只允许**管理员**、或 **`Performance Log Users` 组成员**这么做。不满足时 PresentMon 立刻失败：

```
error: failed to start trace session: access denied.
       PresentMon requires either administrative privileges or to be run by a user in the
       "Performance Log Users" user group.  View the readme for more details.
```

在 Python 侧表现为 `subprocess.CalledProcessError: ... returned non-zero exit status 6`。

结论：

- **提权本身不是黑屏的原因。** 黑屏来自「禁用 / 启用显示设备」，而安全模式从不做这件事，所以安全模式以管理员运行是安全的。
- `1-safe-mode.bat` 现在会和另外两个入口一样，自动请求 UAC。
- 想彻底不提权：用一次管理员权限执行 `net localgroup "Performance Log Users" %USERNAME% /add`，注销后重新登录，之后安全模式就能以普通用户运行。
- 程序现在会在开跑前自检（`has_capture_privilege()`：检查管理员身份，或 `whoami /groups` 里有没有 SID `S-1-5-32-559`），不满足就立刻报错退出，不再跑到一半抛 traceback。
- PresentMon 调用加了超时保护（`benchmark_duration + 120` 秒）；万一跟踪会话卡死，也不会再让整个跑分无限期挂起。
- 采集失败时会中止本次跑分、清理目录、还原亲和策略，并打印上面这段原因说明。
