import argparse
import csv
import ctypes
import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import textwrap
import time
import traceback
import winreg
from typing import NoReturn

import consts
import framerate
import gpuinfo
import setupapi
from config import Api, Config

LOG_CLI = logging.getLogger("CLI")

# where the previous interrupt-affinity policy is stored before it is overwritten
AFFINITY_BACKUP_FILE = "affinity_backup.json"

# per application gpu preference: the same key the windows graphics settings ui writes
GPU_PREFERENCE_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
GPU_PREFERENCE_HIGH = "GpuPreference=2;"  # high performance (discrete gpu)
GPU_PREFERENCE_POWER = "GpuPreference=1;"  # power saving (integrated gpu)


def start_afterburner(path: str, profile: int) -> None:
    with subprocess.Popen([path, f"/Profile{profile}", "/Q"]) as process:
        time.sleep(5)
        process.kill()


# ---------------------------------------------------------------------------
# interrupt affinity policy (registry)
# ---------------------------------------------------------------------------


def _current_control_set() -> str:
    # upstream hardcoded ControlSet001; read the real one instead
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            "SYSTEM\\Select",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            return f"ControlSet{winreg.QueryValueEx(key, 'Current')[0]:03d}"
    except OSError:
        return "ControlSet001"


def affinity_policy_path(hwid: str) -> str:
    return (
        f"SYSTEM\\{_current_control_set()}\\Enum\\{hwid}"
        "\\Device Parameters\\Interrupt Management\\Affinity Policy"
    )


def read_affinity_values(hwid: str) -> dict:
    values: dict = {}

    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            affinity_policy_path(hwid),
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        )
    except FileNotFoundError:
        return values

    with key:
        for name in ("DevicePolicy", "AssignmentSetOverride"):
            try:
                value, value_type = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                continue
            values[name] = {
                "type": value_type,
                "value": value.hex() if isinstance(value, bytes) else value,
            }

    return values


def write_affinity_values(hwid: str, values: dict) -> None:
    with winreg.CreateKey(
        winreg.HKEY_LOCAL_MACHINE,
        affinity_policy_path(hwid),
    ) as key:
        for name, entry in values.items():
            value = entry["value"]
            if entry["type"] == winreg.REG_BINARY and isinstance(value, str):
                value = bytes.fromhex(value)
            winreg.SetValueEx(key, name, 0, entry["type"], value)


def delete_affinity_values(hwid: str) -> None:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            affinity_policy_path(hwid),
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY,
        )
    except FileNotFoundError:
        LOG_CLI.debug("affinity policy already removed for %s", hwid)
        return

    with key:
        for name in ("DevicePolicy", "AssignmentSetOverride"):
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass


def backup_affinity(hwids: list[str]) -> None:
    data = {hwid: read_affinity_values(hwid) for hwid in hwids}

    with open(AFFINITY_BACKUP_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)

    LOG_CLI.info("saved the previous affinity policy to %s", AFFINITY_BACKUP_FILE)


def restore_affinity_backup() -> None:
    if not os.path.exists(AFFINITY_BACKUP_FILE):
        return

    with open(AFFINITY_BACKUP_FILE, encoding="utf-8") as file:
        data = json.load(file)

    for hwid, values in data.items():
        if values:
            write_affinity_values(hwid, values)
        else:
            delete_affinity_values(hwid)
        LOG_CLI.info("restored affinity policy for %s", hwid)


# ---------------------------------------------------------------------------
# device enable / disable
# ---------------------------------------------------------------------------


def set_driver_state(hwid: str, state: int) -> int:
    dev_set, dev_info = setupapi.open_device_instance(hwid)

    if dev_set is None:
        LOG_CLI.error("failed to open device instance %s", hwid)
        return 1

    try:
        params = setupapi.SP_PROPCHANGE_PARAMS()
        params.ClassInstallHeader.cbSize = ctypes.sizeof(
            params.ClassInstallHeader,
        )
        params.ClassInstallHeader.InstallFunction = setupapi.DIF_PROPERTYCHANGE
        params.StateChange = state
        params.Scope = setupapi.DICS_FLAG_GLOBAL
        params.HwProfile = 0

        if not setupapi.SetupDiSetClassInstallParamsA(
            dev_set,
            ctypes.byref(dev_info),
            ctypes.byref(params.ClassInstallHeader),
            ctypes.sizeof(params),
        ):
            LOG_CLI.error(
                "SetupDiSetClassInstallParamsA failed for %s (winerror %d)",
                hwid,
                ctypes.GetLastError(),
            )
            return 1

        if not setupapi.SetupDiCallClassInstaller(
            setupapi.DIF_PROPERTYCHANGE,
            dev_set,
            ctypes.byref(dev_info),
        ):
            LOG_CLI.error(
                "SetupDiCallClassInstaller failed for %s (winerror %d)",
                hwid,
                ctypes.GetLastError(),
            )
            return 1

        return 0
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(dev_set)


def restart_driver(hwid: str) -> int:
    if set_driver_state(hwid, setupapi.DICS_DISABLE) != 0:
        LOG_CLI.error("failed to disable driver while restarting %s", hwid)
        return 1

    time.sleep(2)

    if set_driver_state(hwid, setupapi.DICS_ENABLE) != 0:
        LOG_CLI.error("failed to enable driver while restarting %s", hwid)
        return 1

    time.sleep(2)
    return 0


def apply_affinity(hwids: list[str], cpu: int = -1) -> int:
    for hwid in hwids:
        if cpu > -1:
            mask = 1 << cpu
            little_endian_hex = mask.to_bytes(8, "little").rstrip(b"\x00")

            write_affinity_values(
                hwid,
                {
                    "DevicePolicy": {"type": winreg.REG_DWORD, "value": 4},
                    "AssignmentSetOverride": {
                        "type": winreg.REG_BINARY,
                        "value": little_endian_hex.hex(),
                    },
                },
            )
        else:
            delete_affinity_values(hwid)

        if restart_driver(hwid) != 0:
            LOG_CLI.error("failed to restart driver")
            return 1

    return 0


def reset_affinity(hwids: list[str]) -> int:
    # nothing was written in safe mode, so there is nothing to reset
    if not hwids:
        return 0

    try:
        return apply_affinity(hwids)
    except PermissionError:
        # cleanup only: never turn a missing elevation into a traceback
        LOG_CLI.warning("no permission to clear the affinity policy, skipped")
        return 0


# ---------------------------------------------------------------------------
# device discovery helpers
# ---------------------------------------------------------------------------


def get_basic_display_start() -> int | None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            "SYSTEM\\CurrentControlSet\\Services\\BasicDisplay",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            return winreg.QueryValueEx(key, "Start")[0]
    except FileNotFoundError:
        return None


def list_display_devices() -> None:
    print("display class devices:")

    for device in setupapi.enum_display_devices():
        notes = []
        if device.virtual:
            notes.append("virtual/idd")
        if device.disabled:
            notes.append("DISABLED")
        if not notes:
            notes.append("ok")

        print(f"  {device.description}")
        print(f"    instance id : {device.instance_id}")
        print(f"    state       : {', '.join(notes)}")


def select_affinity_targets(cfg: Config) -> list[setupapi.DisplayDevice]:
    filters = [pattern.lower() for pattern in cfg.settings.affinity_devices]
    targets: list[setupapi.DisplayDevice] = []

    for device in setupapi.enum_display_devices():
        # indirect display drivers (MuMu / GameViewer / AskLink...) expose no
        # gpu interrupt affinity worth restarting - leave them alone
        if device.virtual:
            continue

        # a device that is already disabled must not be part of a restart cycle
        if device.disabled:
            continue

        if filters and not any(
            pattern in device.instance_id.lower() for pattern in filters
        ):
            continue

        targets.append(device)

    return targets


def confirm_restart_targets(targets: list[setupapi.DisplayDevice]) -> bool:
    print()
    print(
        "driver restart mode disables and then re-enables every device below, "
        "once per benchmarked cpu.",
    )
    print(
        "if your only active display adapter is listed here an interrupted run "
        "can leave you with a black screen until you reboot.",
    )
    print()

    for device in targets:
        print(f"  - {device.description}")
        print(f"    {device.instance_id}")

    print()
    return input("type yes to continue: ").strip().lower() == "yes"


def restore_disabled_display_devices() -> int:
    restored = 0

    for device in setupapi.enum_display_devices():
        if not device.disabled:
            continue

        LOG_CLI.info("re-enabling %s", device.description)
        if set_driver_state(device.instance_id, setupapi.DICS_ENABLE) == 0:
            restored += 1

    return restored


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


def set_gpu_preference(exe_path: str, preference: str = "high") -> None:
    """Ask windows to run a program on the high performance (discrete) gpu.

    Writes exactly the per application preference the graphics settings ui
    writes, so it stays user scoped and is trivially reversible.
    """
    exe_path = os.path.abspath(exe_path)
    value = GPU_PREFERENCE_HIGH if preference == "high" else GPU_PREFERENCE_POWER

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        GPU_PREFERENCE_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, exe_path, 0, winreg.REG_SZ, value)


def read_gpu_preference(exe_path: str) -> str | None:
    exe_path = os.path.abspath(exe_path)

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, GPU_PREFERENCE_KEY) as key:
            return winreg.QueryValueEx(key, exe_path)[0]
    except FileNotFoundError:
        return None


def list_gpu_devices() -> None:
    devices = gpuinfo.list_vulkan_devices()

    if not devices:
        LOG_CLI.warning("no vulkan device found - is a graphics driver installed?")
        return

    for device in devices:
        marker = "  <- discrete gpu" if device.is_discrete else ""
        print(f"  {device}{marker}")

    index = gpuinfo.pick_discrete_index(devices)
    print()

    if index is None:
        print("no discrete gpu detected")
    else:
        print(f"discrete gpu index {index} -> use [liblava] physical_device={index} in config.ini")


def print_table(formatted_results: dict[str, dict[str, str]]):
    # print table headings
    print(f"{'CPU':<5}", end="")

    for metric in (
        "Max",
        "Avg",
        "Min",
        "STDEV",
        "1 %ile",
        "0.1 %ile",
        "0.01 %ile",
        "0.005 %ile",
        "1% Low",
        "0.1% Low",
        "0.01% Low",
        "0.005% Low",
    ):
        print(f"{metric:<12}", end="")

    print()  # new line

    # print values for each heading
    for _cpu, _results in formatted_results.items():
        print(f"{_cpu:<5}", end="")
        for metric_value in _results.values():
            # padding needs to be larger to compensate for color chars
            right_padding = 21 if "[" in metric_value else 12
            print(f"{metric_value:<{right_padding}}", end="")

        print()  # new line

    print()  # new line


def display_results(csv_directory: str, enable_color: bool) -> None:
    results: dict[str, dict[str, float]] = {}

    # each index represents the rank (e.g. index 0 is 1st)
    colors: list[str] = [
        "\x1b[92m",  # Green
        "\x1b[93m",  # Yellow
    ]

    if enable_color:
        default = "\x1b[0m"
        os.system("color")
    else:
        default = ""

    cpus = sorted([int(file.strip("CPU-.csv")) for file in os.listdir(csv_directory)])
    num_cpus = len(cpus)
    # 1 CPUs means no ranking will be done
    # 2 CPUs means only one metric will be ranked since it can be either or
    # always leave last place unranked

    top_n_values = num_cpus - 1 if num_cpus < 3 else len(colors)

    for cpu in cpus:
        csv_file = f"CPU-{cpu}.csv"

        frametimes: list[float] = []

        with open(f"{csv_directory}\\{csv_file}", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                # convert key names to lowercase because column names changed in a newer version of PresentMon
                row_lower = {key.lower(): value for key, value in row.items()}

                if (ms_between_presents := row_lower.get("msbetweenpresents")) is not None:
                    frametimes.append(float(ms_between_presents))

        fps = framerate.Fps(frametimes)

        # results of current CPU in results dict
        results[str(cpu)] = {
            "maximum": round(fps.maximum(), 2),
            "average": round(fps.average(), 2),
            "minimum": round(fps.minimum(), 2),
            # negate positive value so that highest negative value will be the lowest absolute value
            "stdev": round(-fps.stdev(), 2),
            **{
                f"{metric}{value}": round(getattr(fps, metric)(value), 2)
                for metric in ("percentile", "lows")
                for value in (1, 0.1, 0.01, 0.005)
            },
        }

    formatted_results: dict[str, dict[str, str]] = {cpu: {} for cpu in results}

    # analyze best values for each metric
    for metric in (
        "maximum",
        "average",
        "minimum",
        "stdev",
        # "percentile1", "percentile0.1" etc
        *(tuple(f"{metric}{value}" for metric in ("percentile", "lows") for value in (1, 0.1, 0.01, 0.005))),
    ):
        # set of all values within the metric
        values = {_results[metric] for _results in results.values()}

        # create ordered list without duplicates of top n values
        top_values = list(dict.fromkeys(sorted(values, reverse=True)[:top_n_values]))

        for _cpu, _results in results.items():
            metric_value = _results[metric]

            # abs is for negative values such as stdev
            # :.2f is for .00 numerical formatting
            new_value = f"{abs(metric_value):.2f}"

            # determine rank of value
            if enable_color:
                try:
                    nth_best = top_values.index(metric_value)
                    color = colors[nth_best]
                    new_value = f"{color}{new_value}{default}"
                except ValueError:
                    # don't highlight value as top n by leaving it unmodified
                    pass

            formatted_results[_cpu][metric] = new_value

    os.system("<nul set /p=\x1b[8;50;1000t")

    print_table(formatted_results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--version",
        action="version",
        version=f"AutoGpuAffinity v{consts.VERSION}",
    )
    parser.add_argument(
        "--config",
        metavar="<config>",
        type=str,
        help="path to config file",
    )
    parser.add_argument(
        "--analyze",
        metavar="<csv directory>",
        type=str,
        help="analyze csv files from a previous benchmark",
    )
    parser.add_argument(
        "--apply-affinity",
        metavar="<cpu>",
        type=int,
        help="assign a single core affinity to graphics drivers (requires --restart-driver)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="list display class devices and exit",
    )
    parser.add_argument(
        "--list-gpus",
        action="store_true",
        help="list vulkan gpu adapters (read only) and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the benchmark plan and exit without launching anything",
    )
    parser.add_argument(
        "--restore-display",
        action="store_true",
        help="re-enable disabled display devices and restore the saved affinity policy, then exit",
    )
    parser.add_argument(
        "--restart-driver",
        action="store_true",
        help="force driver restart mode (overrides config)",
    )
    parser.add_argument(
        "--no-restart-driver",
        action="store_true",
        help="force safe mode: never touch the display driver (overrides config)",
    )

    return parser.parse_args()


def is_admin() -> bool:
    return ctypes.windll.shell32.IsUserAnAdmin()

CAPTURE_PRIVILEGE_HELP = (
    "PresentMon needs to start an ETW trace session. That requires either\n"
    "administrative privileges, or membership of the Windows group\n"
    '"Performance Log Users" (S-1-5-32-559).\n'
    "\n"
    "How to fix:\n"
    "  * easiest: launch 1-safe-mode.bat and accept the UAC prompt. Safe mode never\n"
    "    touches the display device, so running it elevated is harmless.\n"
    '  * or, once, from an elevated prompt: net localgroup "Performance Log Users" %USERNAME% /add\n'
    "    then sign out and back in; after that safe mode works unelevated."
)


def has_capture_privilege() -> tuple[bool, str]:
    # capturing frametimes means starting an etw trace session, which windows
    # only allows for administrators and performance log users group members
    if is_admin():
        return True, "administrator"

    try:
        groups = subprocess.run(
            ["whoami", "/groups"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
        if "S-1-5-32-559" in (groups or ""):
            return True, "Performance Log Users"
    except Exception:
        pass

    return False, "none"

def kill_processes(*targets: str) -> None:
    # taskkill instead of psutil so the tool has no third party dependencies
    for target in {target for target in targets if target}:
        subprocess.run(
            ["taskkill", "/F", "/IM", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> int:
    logging.basicConfig(format="[%(name)s] %(levelname)s: %(message)s", level=logging.INFO)

    print(
        textwrap.dedent(
            f"""\
            AutoGpuAffinity Version {consts.VERSION} - GPLv3
            Safe fork of the now unmaintained AutoGpuAffinity, published with the original author's permission
            Benchmarks CPU core affinity for a GPU workload without disabling the display adapter
            Source: https://github.com/valleyofdoom/AutoGpuAffinity
            Fixed: https://github.com/nieTVT
            """,
        ),
    )

    full_program_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(__file__)
    os.chdir(full_program_dir)

    args = parse_args()

    winver = sys.getwindowsversion()

    if args.list_devices:
        list_display_devices()
        return 0

    if args.list_gpus:
        list_gpu_devices()
        return 0

    if args.analyze:
        display_results(args.analyze, winver.major >= 10)
        return 0

    if args.restore_display:
        if not is_admin():
            LOG_CLI.error("administrator privileges required")
            return 1

        restored = restore_disabled_display_devices()
        restore_affinity_backup()
        LOG_CLI.info("re-enabled %d display device(s)", restored)
        return 0

    config_path = args.config if args.config is not None else "config.ini"

    try:
        cfg = Config(config_path)
    except FileNotFoundError as e:
        LOG_CLI.exception(e)
        return 1

    if cfg.validate_config() != 0:
        LOG_CLI.error("failed to validate config")
        return 1

    restart_mode = cfg.settings.restart_driver
    if args.restart_driver:
        restart_mode = True
    if args.no_restart_driver:
        restart_mode = False

    if not restart_mode:
        LOG_CLI.info(
            "safe mode: the display driver is never disabled; each cpu is benchmarked "
            "by pinning the benchmark process instead",
        )

    if restart_mode:
        targets = select_affinity_targets(cfg)
        hwids_gpu = [device.instance_id for device in targets]
    else:
        # safe mode never writes the interrupt affinity policy, so there is
        # nothing to select and nothing to reset
        hwids_gpu = []

    if restart_mode:
        if not is_admin():
            LOG_CLI.error(
                "administrator privileges required when driver restart is enabled",
            )
            return 1

        bd_start = get_basic_display_start()

        if bd_start is None:
            LOG_CLI.error("unable to get BasicDisplay start type")
            return 1

        if bd_start == 4:
            LOG_CLI.error(
                "enable the BasicDisplay driver to prevent issues with restarting the GPU driver",
            )
            return 1

        if not hwids_gpu:
            LOG_CLI.error("no display devices available for driver restart")
            return 1

    cpu_count = os.cpu_count()

    if cpu_count is None:
        LOG_CLI.error("failed to get CPU cores count")
        return 1

    cpu_count -= 1  # adjust for zero-based indexing

    if args.apply_affinity is not None:
        if not restart_mode:
            LOG_CLI.error(
                "--apply-affinity requires driver restart mode, pass --restart-driver",
            )
            return 1

        if not 0 <= args.apply_affinity <= cpu_count:
            LOG_CLI.error("invalid affinity specified %d", args.apply_affinity)
            return 1

        if not hwids_gpu:
            LOG_CLI.error("no display devices available")
            return 1

        if not confirm_restart_targets(targets):
            LOG_CLI.error("aborted by user")
            return 1

        backup_affinity(hwids_gpu)

        if apply_affinity(hwids_gpu, args.apply_affinity) != 0:
            LOG_CLI.error("failed to apply affinity to CPU %d", args.apply_affinity)
            return 1

        LOG_CLI.info("set gpu driver affinity to: CPU %d", args.apply_affinity)
        return 0

    presentmon_version = "1.10.0" if winver.major >= 10 and winver.product_type != 3 else "1.6.0"
    presentmon_binary = f"PresentMon-{presentmon_version}-x64.exe"

    api_binpaths: dict[Api, str] = {
        Api.LIBLAVA: "bin\\liblava\\lava-triangle.exe",
        Api.D3D9: "bin\\D3D9-benchmark.exe",
    }

    api_binpath = api_binpaths[cfg.settings.api]
    api_binname = os.path.basename(api_binpath)
    api_binfile = os.path.abspath(api_binpath)

    # safe mode never touches the display adapter, it only pins the subject.
    # the subject must therefore be sent to the discrete gpu explicitly, otherwise
    # windows is free to render it on the adapter that currently drives the display.
    vulkan_devices = gpuinfo.list_vulkan_devices()
    discrete_index = gpuinfo.pick_discrete_index(vulkan_devices)

    if cfg.settings.force_discrete_gpu:
        gpu_preference = "high performance (per-app preference)"
    else:
        gpu_preference = "system default"

    if cfg.settings.api == Api.LIBLAVA:
        if cfg.liblava.physical_device >= 0:
            picked = next(
                (d for d in vulkan_devices if d.index == cfg.liblava.physical_device), None
            )
            if picked is None:
                subject_gpu = f"vulkan #{cfg.liblava.physical_device} (not present!)"
            else:
                subject_gpu = f"{picked.name} (vulkan #{picked.index}, forced)"
        elif discrete_index is not None:
            subject_gpu = f"{vulkan_devices[discrete_index].name} (auto, first discrete)"
        elif vulkan_devices:
            subject_gpu = f"{vulkan_devices[0].name} (auto, no discrete gpu)"
        else:
            subject_gpu = "unknown (vulkan loader unavailable)"
    else:
        subject_gpu = "d3d9 adapter (per-app preference)"

    if cfg.settings.custom_cpus:
        # remove duplicates and sort
        benchmark_cpus = sorted(set(cfg.settings.custom_cpus))

        if not all(0 <= cpu <= cpu_count for cpu in benchmark_cpus):
            LOG_CLI.error("invalid cpus in custom_cpus array")
            return 1
    else:
        benchmark_cpus = list(range(cpu_count + 1))

    if not restart_mode and not cfg.settings.sync_driver_affinity:
        LOG_CLI.warning(
            "safe mode needs sync_driver_affinity=true, otherwise every cpu is benchmarked identically",
        )

    session_directory = f"captures\\AutoGpuAffinity-{time.strftime('%d%m%y%H%M%S')}"

    estimated_time_seconds = (
        10
        + cfg.settings.cache_duration
        + cfg.settings.benchmark_duration
        + (5 if cfg.msi_afterburner.profile > 0 else 0)
    ) * len(benchmark_cpus)

    estimated_time = datetime.timedelta(seconds=estimated_time_seconds)
    finish_time = datetime.datetime.now() + estimated_time

    print(
        textwrap.dedent(
            f"""        Session Directory        {session_directory}
        Cache Duration           {cfg.settings.cache_duration}
        Benchmark Duration       {cfg.settings.benchmark_duration}
        Benchmark CPUs           {"All" if not cfg.settings.custom_cpus else ",".join([str(cpu) for cpu in benchmark_cpus])}
        Subject                  {os.path.splitext(api_binname)[0]}
        Estimated Time           {estimated_time}
        Estimated End Time       {finish_time.strftime("%H:%M:%S")}
        Load Afterburner         {cfg.msi_afterburner.profile > 0}
        DPC/ISR Logging          {cfg.xperf.enabled}
        Save ETLs                {cfg.xperf.save_etls}
        Sync Affinity            {cfg.settings.sync_driver_affinity}
        Driver Restart           {restart_mode}
        Affinity Targets         {len(hwids_gpu) if restart_mode else "none (safe mode)"}
        Subject GPU              {subject_gpu}
        GPU Preference           {gpu_preference}
        """,
        ),
    )

    if args.dry_run:
        LOG_CLI.info("dry run: nothing is launched and no display device is touched")
        for cpu in benchmark_cpus:
            print(f"  would benchmark CPU {cpu} (subject affinity mask {hex(1 << cpu)})")
        return 0

    if restart_mode and not confirm_restart_targets(targets):
        LOG_CLI.error("aborted by user")
        return 1

    if restart_mode:
        backup_affinity(hwids_gpu)

    if not cfg.settings.skip_confirmation:
        input("press enter to start benchmarking...")

    if cfg.settings.force_discrete_gpu:
        set_gpu_preference(api_binfile, "high")
        LOG_CLI.info(
            "requested the high performance (discrete) gpu for %s (%s)",
            api_binname,
            read_gpu_preference(api_binfile),
        )

    subject_args: list[str] = []

    if cfg.settings.api == Api.LIBLAVA:
        subject_args = [
            f"--fullscreen={int(cfg.liblava.fullscreen)}",
            f"--width={cfg.liblava.x_resolution}",
            f"--height={cfg.liblava.y_resolution}",
            f"--fps_cap={cfg.liblava.fps_cap}",
            f"--triple_buffering={int(cfg.liblava.triple_buffering)}",
        ]

        if cfg.liblava.physical_device >= 0:
            subject_args.append(f"--physical_device={cfg.liblava.physical_device}")

    # this will create all of the required folders
    os.makedirs(f"{session_directory}\\CSVs", exist_ok=True)

    # stop any existing trace sessions and processes
    if cfg.xperf.enabled:
        os.mkdir(f"{session_directory}\\xperf")

        try:
            subprocess.run(
                [cfg.xperf.location, "-stop"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            # ignore if already stopped
            if e.returncode != 2147946601:
                LOG_CLI.exception(e)
                raise

    kill_processes("xperf.exe", api_binname, presentmon_binary)

    capture_ok, capture_how = has_capture_privilege()
    if not capture_ok:
        LOG_CLI.error("no privilege to start an ETW trace session")
        print(CAPTURE_PRIVILEGE_HELP)
        reset_affinity(hwids_gpu)
        return 1

    LOG_CLI.info("frametime capture privilege: %s", capture_how)

    for cpu in benchmark_cpus:
        LOG_CLI.info("benchmarking CPU %d", cpu)

        if restart_mode:
            if apply_affinity(hwids_gpu, cpu) != 0:
                LOG_CLI.error("failed to apply affinity to CPU %d", cpu)
                reset_affinity(hwids_gpu)
                return 1

            time.sleep(5)
        else:
            LOG_CLI.info(
                "safe mode: display driver untouched, pinning the subject to CPU %d",
                cpu,
            )

        if (profile := cfg.msi_afterburner.profile) > 0:
            start_afterburner(cfg.msi_afterburner.location, profile)

        affinity_args: list[str] = []
        if cfg.settings.sync_driver_affinity:
            affinity_args.extend(["/affinity", hex(1 << cpu)])

        subprocess.run(
            ["start", "", *affinity_args, api_binpath, *subject_args],
            shell=True,
            check=True,
        )

        # 5s offset to allow subject to launch
        time.sleep(5 + cfg.settings.cache_duration)

        if cfg.xperf.enabled:
            subprocess.run(
                [cfg.xperf.location, "-on", "base+interrupt+dpc"],
                check=True,
            )

        capture_failed = False

        try:
            capture = subprocess.run(
                [
                    f"bin\\PresentMon\\{presentmon_binary}",
                    "-stop_existing_session",
                    "-no_top",
                    "-timed",
                    str(cfg.settings.benchmark_duration),
                    "-process_name",
                    api_binname,
                    "-output_file",
                    f"{session_directory}\\CSVs\\CPU-{cpu}.csv",
                    "-terminate_after_timed",
                ],
                capture_output=True,
                text=True,
                check=True,
                # a stuck trace session must never hang the whole run
                timeout=cfg.settings.benchmark_duration + 120,
            )

            for line in (capture.stderr or "").strip().splitlines():
                LOG_CLI.warning("presentmon: %s", line.strip())
        except subprocess.TimeoutExpired:
            LOG_CLI.error(
                "presentmon did not stop after %d seconds, killing it",
                cfg.settings.benchmark_duration + 120,
            )
            kill_processes(presentmon_binary)
            capture_failed = True
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")

            stderr = stderr or ""
            LOG_CLI.error("presentmon exited with status %d", exc.returncode)
            for line in stderr.strip().splitlines():
                LOG_CLI.error("presentmon: %s", line.strip())
            if "trace session" in stderr or "access denied" in stderr.lower():
                print(CAPTURE_PRIVILEGE_HELP)
            capture_failed = True

        if capture_failed or not os.path.exists(f"{session_directory}\\CSVs\\CPU-{cpu}.csv"):
            if not capture_failed:
                LOG_CLI.error(
                    "csv log unsuccessful, this may be due to a missing dependency or windows component",
                )
            kill_processes(api_binname)
            shutil.rmtree(session_directory, ignore_errors=True)
            if reset_affinity(hwids_gpu) != 0:
                LOG_CLI.error("failed to reset affinity")
                return 1

            return 1

        if cfg.xperf.enabled:
            subprocess.run(
                [
                    cfg.xperf.location,
                    "-d",
                    f"{session_directory}\\xperf\\CPU-{cpu}.etl",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            try:
                subprocess.run(
                    [
                        cfg.xperf.location,
                        "-quiet",
                        "-i",
                        f"{session_directory}\\xperf\\CPU-{cpu}.etl",
                        "-o",
                        f"{session_directory}\\xperf\\CPU-{cpu}.txt",
                        "-a",
                        "dpcisr",
                    ],
                    check=True,
                )
            except subprocess.CalledProcessError:
                LOG_CLI.error("unable to generate dpcisr report")
                shutil.rmtree(session_directory)
                if reset_affinity(hwids_gpu) != 0:
                    LOG_CLI.error("failed to reset affinity")
                    return 1  # return 1 after anyway
                return 1

            if not cfg.xperf.save_etls:
                os.remove(f"{session_directory}\\xperf\\CPU-{cpu}.etl")

        kill_processes("xperf.exe", api_binname, presentmon_binary)

    # cleanup
    if reset_affinity(hwids_gpu) != 0:
        LOG_CLI.error("failed to reset affinity")
        return 1

    if os.path.exists("C:\\kernel.etl"):
        os.remove("C:\\kernel.etl")

    print()  # new line
    display_results(f"{session_directory}\\CSVs", winver.major >= 10)

    return 0


def _main() -> NoReturn:
    exit_code = 0

    try:
        exit_code = main()
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception:
        print(traceback.format_exc())
        exit_code = 1
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        process_array = (ctypes.c_uint * 1)()
        num_processes = kernel32.GetConsoleProcessList(process_array, 1)

        # only pause if script was ran by double-clicking
        # the launcher .bat files set AUTOGPU_NO_PAUSE and pause themselves
        if num_processes < 3 and not os.environ.get("AUTOGPU_NO_PAUSE"):
            input("press enter to exit")

        sys.exit(exit_code)


if __name__ == "__main__":
    _main()