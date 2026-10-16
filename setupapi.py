"""SetupAPI / CfgMgr32 bindings for AutoGpuAffinity.

Deliberately dependency-free (ctypes only) so the tool runs on a bare CPython
install without pywin32 / WMI.
"""

import ctypes
import ctypes.wintypes
import dataclasses

SETUPAPI = ctypes.windll.setupapi
CFGMGR32 = ctypes.windll.cfgmgr32

DIGCF_PRESENT = 0x2
DIGCF_ALLCLASSES = 0x4
DIGCF_DEVICEINTERFACE = 0x10

DIF_PROPERTYCHANGE = 0x12

DICS_FLAG_GLOBAL = 0x1

DICS_ENABLE = 0x1
DICS_DISABLE = 0x2

SPDRP_DEVICEDESC = 0x0
SPDRP_CONFIGFLAGS = 0xA
SPDRP_FRIENDLYNAME = 0xC
CONFIGFLAG_DISABLED = 0x1

CR_SUCCESS = 0x0
CM_PROB_DISABLED = 22

# ctypes converts a c_void_p restype to a plain python int; INVALID_HANDLE_VALUE
# can therefore show up as -1 or as 0xFFFFFFFFFFFFFFFF depending on the platform.
INVALID_HANDLES = (None, 0, -1, 0xFFFFFFFFFFFFFFFF)


class SP_CLASSINSTALL_HEADER(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("InstallFunction", ctypes.c_int32),
    ]


class SP_PROPCHANGE_PARAMS(ctypes.Structure):
    _fields_ = [
        ("ClassInstallHeader", SP_CLASSINSTALL_HEADER),
        ("StateChange", ctypes.c_ulong),
        ("Scope", ctypes.c_ulong),
        ("HwProfile", ctypes.c_ulong),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("ClassGuid", GUID),
        ("DevInst", ctypes.c_ulong),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


# {4d36e968-e325-11ce-bfc1-08002be10318} = Display class GUID
DISPLAY_CLASS_GUID = GUID(
    0x4D36E968,
    0xE325,
    0x11CE,
    (ctypes.c_ubyte * 8)(0xBF, 0xC1, 0x08, 0x00, 0x2B, 0xE1, 0x03, 0x18),
)


@dataclasses.dataclass
class DisplayDevice:
    instance_id: str
    description: str
    problem: int = -1

    @property
    def disabled(self) -> bool:
        return self.problem == CM_PROB_DISABLED

    @property
    def virtual(self) -> bool:
        r"""ROOT\DISPLAY\* covers indirect display drivers (IDD), e.g. MuMu/GameViewer."""
        return self.instance_id.upper().startswith("ROOT\\DISPLAY\\")


SetupDiGetClassDevsW = SETUPAPI.SetupDiGetClassDevsW
SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID),
    ctypes.c_wchar_p,
    ctypes.wintypes.HWND,
    ctypes.wintypes.DWORD,
]
SetupDiGetClassDevsW.restype = ctypes.wintypes.HWND

SetupDiEnumDeviceInfo = SETUPAPI.SetupDiEnumDeviceInfo
SetupDiEnumDeviceInfo.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.wintypes.DWORD,
    ctypes.POINTER(SP_DEVINFO_DATA),
]
SetupDiEnumDeviceInfo.restype = ctypes.wintypes.BOOL

SetupDiSetClassInstallParamsA = SETUPAPI.SetupDiSetClassInstallParamsA
SetupDiSetClassInstallParamsA.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.POINTER(SP_DEVINFO_DATA),
    ctypes.POINTER(SP_CLASSINSTALL_HEADER),
    ctypes.wintypes.DWORD,
]
SetupDiSetClassInstallParamsA.restype = ctypes.wintypes.BOOL

SetupDiCallClassInstaller = SETUPAPI.SetupDiCallClassInstaller
SetupDiCallClassInstaller.argtypes = [
    ctypes.c_int32,
    ctypes.c_ssize_t,
    ctypes.POINTER(SP_DEVINFO_DATA),
]
SetupDiCallClassInstaller.restype = ctypes.wintypes.BOOL

SetupDiGetDeviceInstanceIdW = SETUPAPI.SetupDiGetDeviceInstanceIdW
SetupDiGetDeviceInstanceIdW.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.POINTER(SP_DEVINFO_DATA),
    ctypes.c_wchar_p,
    ctypes.wintypes.DWORD,
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
SetupDiGetDeviceInstanceIdW.restype = ctypes.wintypes.BOOL

SetupDiGetDeviceRegistryPropertyW = SETUPAPI.SetupDiGetDeviceRegistryPropertyW
SetupDiGetDeviceRegistryPropertyW.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.POINTER(SP_DEVINFO_DATA),
    ctypes.wintypes.DWORD,
    ctypes.POINTER(ctypes.wintypes.DWORD),
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.wintypes.DWORD,
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
SetupDiGetDeviceRegistryPropertyW.restype = ctypes.wintypes.BOOL

SetupDiDestroyDeviceInfoList = SETUPAPI.SetupDiDestroyDeviceInfoList
SetupDiDestroyDeviceInfoList.argtypes = [ctypes.wintypes.HANDLE]
SetupDiDestroyDeviceInfoList.restype = ctypes.wintypes.BOOL

CM_Get_DevNode_Status = CFGMGR32.CM_Get_DevNode_Status
CM_Get_DevNode_Status.argtypes = [
    ctypes.POINTER(ctypes.wintypes.ULONG),
    ctypes.POINTER(ctypes.wintypes.ULONG),
    ctypes.wintypes.DWORD,
    ctypes.wintypes.ULONG,
]
CM_Get_DevNode_Status.restype = ctypes.wintypes.ULONG


def _new_devinfo() -> SP_DEVINFO_DATA:
    dev_info = SP_DEVINFO_DATA()
    dev_info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
    return dev_info


def _instance_id(dev_set, dev_info) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    needed = ctypes.wintypes.DWORD()
    ok = SetupDiGetDeviceInstanceIdW(
        dev_set, ctypes.byref(dev_info), buf, len(buf), ctypes.byref(needed)
    )
    return buf.value if ok else ""


def _device_property(dev_set, dev_info, prop) -> str:
    prop_type = ctypes.wintypes.DWORD()
    needed = ctypes.wintypes.DWORD()
    buf = ctypes.create_unicode_buffer(1024)
    ok = SetupDiGetDeviceRegistryPropertyW(
        dev_set,
        ctypes.byref(dev_info),
        prop,
        ctypes.byref(prop_type),
        ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
        ctypes.sizeof(buf),
        ctypes.byref(needed),
    )
    return buf.value if ok else ""


def _problem_code(dev_inst) -> int:
    status = ctypes.wintypes.ULONG()
    problem = ctypes.wintypes.ULONG()
    rc = CM_Get_DevNode_Status(
        ctypes.byref(status), ctypes.byref(problem), dev_inst, 0
    )
    return problem.value if rc == CR_SUCCESS else -1


def enum_display_devices(present_only: bool = True) -> list[DisplayDevice]:
    """Every device in the Display class (virtual IDD adapters included)."""
    flags = DIGCF_PRESENT if present_only else 0
    dev_set = SetupDiGetClassDevsW(
        ctypes.byref(DISPLAY_CLASS_GUID), None, None, flags
    )
    if dev_set in INVALID_HANDLES:
        raise OSError(
            f"SetupDiGetClassDevsW failed (winerror {ctypes.GetLastError()})"
        )

    devices: list[DisplayDevice] = []
    try:
        dev_info = _new_devinfo()
        index = 0
        while SetupDiEnumDeviceInfo(dev_set, index, ctypes.byref(dev_info)):
            index += 1
            instance_id = _instance_id(dev_set, dev_info)
            description = _device_property(dev_set, dev_info, SPDRP_DEVICEDESC)
            devices.append(
                DisplayDevice(
                    instance_id,
                    description or instance_id,
                    _problem_code(dev_info.DevInst),
                )
            )
    finally:
        SetupDiDestroyDeviceInfoList(dev_set)

    return devices


def open_device_instance(instance_id: str):
    """Resolve one device instance id -> (device info set, SP_DEVINFO_DATA).

    The original upstream code passed the full instance id as the Enumerator
    argument of SetupDiGetClassDevsW.  This helper keeps that behaviour so the
    proven disable/enable path is unchanged, but returns None on bad handles.
    """
    dev_set = SetupDiGetClassDevsW(
        None,
        ctypes.c_wchar_p(instance_id),
        None,
        DIGCF_ALLCLASSES | DIGCF_DEVICEINTERFACE,
    )
    if dev_set in INVALID_HANDLES:
        return None, None

    dev_info = _new_devinfo()
    if not SetupDiEnumDeviceInfo(dev_set, 0, ctypes.byref(dev_info)):
        SetupDiDestroyDeviceInfoList(dev_set)
        return None, None

    return dev_set, dev_info