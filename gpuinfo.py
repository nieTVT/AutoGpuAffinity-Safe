"""Read-only Vulkan physical device enumeration.

Pure ctypes against the system vulkan-1.dll loader, so the tool keeps its
"no third party dependencies" property. Nothing in this module changes driver
or device state: it only creates a throwaway VkInstance, asks which adapters
exist and destroys the instance again.

The index reported here is the index used by the Vulkan loader enumeration
order, which is also what liblava exposes through its --physical_device option.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

VK_SUCCESS = 0
VK_INCOMPLETE = 5

VK_STRUCTURE_TYPE_APPLICATION_INFO = 0
VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO = 1
VK_API_VERSION_1_0 = (1 << 22) | (0 << 12) | 0

VK_MAX_PHYSICAL_DEVICE_NAME_SIZE = 256

VK_PHYSICAL_DEVICE_TYPE_OTHER = 0
VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU = 1
VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU = 2
VK_PHYSICAL_DEVICE_TYPE_VIRTUAL_GPU = 3
VK_PHYSICAL_DEVICE_TYPE_CPU = 4

# offsets inside VkPhysicalDeviceProperties, everything before deviceName is uint32/enum
_OFFSET_VENDOR_ID = 8
_OFFSET_DEVICE_ID = 12
_OFFSET_DEVICE_TYPE = 16
_OFFSET_DEVICE_NAME = 20

# VkPhysicalDeviceProperties is ~800 bytes on 64 bit, 4096 is plenty
_PROPERTIES_BUFFER_SIZE = 4096

_TYPE_NAMES = {
    VK_PHYSICAL_DEVICE_TYPE_OTHER: "other",
    VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU: "integrated",
    VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU: "discrete",
    VK_PHYSICAL_DEVICE_TYPE_VIRTUAL_GPU: "virtual",
    VK_PHYSICAL_DEVICE_TYPE_CPU: "cpu",
}

_VENDOR_NAMES = {
    0x10DE: "NVIDIA",
    0x8086: "Intel",
    0x1002: "AMD",
    0x1022: "AMD",
    0x1414: "Microsoft",
    0x5143: "Qualcomm",
    0x13B5: "ARM",
}


class _VkApplicationInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_int32),
        ("pNext", ctypes.c_void_p),
        ("pApplicationName", ctypes.c_char_p),
        ("applicationVersion", ctypes.c_uint32),
        ("pEngineName", ctypes.c_char_p),
        ("engineVersion", ctypes.c_uint32),
        ("apiVersion", ctypes.c_uint32),
    ]


class _VkInstanceCreateInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_int32),
        ("pNext", ctypes.c_void_p),
        ("flags", ctypes.c_uint32),
        ("pApplicationInfo", ctypes.POINTER(_VkApplicationInfo)),
        ("enabledLayerCount", ctypes.c_uint32),
        ("ppEnabledLayerNames", ctypes.c_void_p),
        ("enabledExtensionCount", ctypes.c_uint32),
        ("ppEnabledExtensionNames", ctypes.c_void_p),
    ]


@dataclass
class VulkanDevice:
    index: int
    name: str
    device_type: int
    vendor_id: int
    device_id: int

    @property
    def type_name(self) -> str:
        return _TYPE_NAMES.get(self.device_type, f"unknown({self.device_type})")

    @property
    def vendor_name(self) -> str:
        return _VENDOR_NAMES.get(self.vendor_id, f"0x{self.vendor_id:04X}")

    @property
    def is_discrete(self) -> bool:
        return self.device_type == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU

    def __str__(self) -> str:
        return f"#{self.index} {self.name} [{self.vendor_name}, {self.type_name}]"


def _load_loader() -> ctypes.WinDLL | None:
    for name in ("vulkan-1.dll", "vulkan-1"):
        try:
            return ctypes.WinDLL(name)
        except OSError:
            continue

    return None


def list_vulkan_devices() -> list[VulkanDevice]:
    """Return every Vulkan physical device, in loader enumeration order."""
    loader = _load_loader()

    if loader is None:
        return []

    try:
        loader.vkCreateInstance.restype = ctypes.c_int32
        loader.vkCreateInstance.argtypes = [
            ctypes.POINTER(_VkInstanceCreateInfo),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        loader.vkEnumeratePhysicalDevices.restype = ctypes.c_int32
        loader.vkEnumeratePhysicalDevices.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        loader.vkGetPhysicalDeviceProperties.restype = None
        loader.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        loader.vkDestroyInstance.restype = None
        loader.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        app_info = _VkApplicationInfo(
            sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,
            pNext=None,
            pApplicationName=b"AutoGpuAffinity",
            applicationVersion=1,
            pEngineName=b"AutoGpuAffinity",
            engineVersion=1,
            apiVersion=VK_API_VERSION_1_0,
        )
        create_info = _VkInstanceCreateInfo(
            sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
            pNext=None,
            flags=0,
            pApplicationInfo=ctypes.pointer(app_info),
            enabledLayerCount=0,
            ppEnabledLayerNames=None,
            enabledExtensionCount=0,
            ppEnabledExtensionNames=None,
        )

        instance = ctypes.c_void_p()

        if loader.vkCreateInstance(ctypes.byref(create_info), None, ctypes.byref(instance)) != VK_SUCCESS:
            return []
    except OSError:
        return []

    try:
        count = ctypes.c_uint32(0)

        result = loader.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), None)

        if result not in (VK_SUCCESS, VK_INCOMPLETE) or count.value == 0:
            return []

        handles = (ctypes.c_void_p * count.value)()

        result = loader.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), handles)

        if result not in (VK_SUCCESS, VK_INCOMPLETE):
            return []

        buffer = ctypes.create_string_buffer(_PROPERTIES_BUFFER_SIZE)
        devices: list[VulkanDevice] = []

        for index in range(count.value):
            ctypes.memset(buffer, 0, _PROPERTIES_BUFFER_SIZE)
            loader.vkGetPhysicalDeviceProperties(handles[index], ctypes.byref(buffer))

            raw = bytes(buffer)
            name_bytes = raw[
                _OFFSET_DEVICE_NAME : _OFFSET_DEVICE_NAME + VK_MAX_PHYSICAL_DEVICE_NAME_SIZE
            ]

            devices.append(
                VulkanDevice(
                    index=index,
                    name=name_bytes.split(b"\x00", 1)[0].decode("utf-8", "replace"),
                    device_type=int.from_bytes(
                        raw[_OFFSET_DEVICE_TYPE : _OFFSET_DEVICE_TYPE + 4], "little"
                    ),
                    vendor_id=int.from_bytes(raw[_OFFSET_VENDOR_ID : _OFFSET_VENDOR_ID + 4], "little"),
                    device_id=int.from_bytes(raw[_OFFSET_DEVICE_ID : _OFFSET_DEVICE_ID + 4], "little"),
                ),
            )

        return devices
    finally:
        loader.vkDestroyInstance(instance, None)


def pick_discrete_index(devices: list[VulkanDevice]) -> int | None:
    """Index of the first discrete gpu, or None when there is none."""
    for device in devices:
        if device.is_discrete:
            return device.index

    return None
