"""
EonOS 跨平台兼容层 - 万能系统
整合 WinBoat 的 Linux/Windows 兼容能力
支持: Windows / Linux / macOS / WSL
硬件: 多CPU/多GPU/单CPU/单GPU 自适应
"""

import asyncio
import logging
import os
import platform
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core import EventBus, bus
from ..core.command import executor

logger = logging.getLogger("eonos.compat")


@dataclass
class PlatformInfo:
    system: str  # windows / linux / darwin
    is_wsl: bool
    has_docker: bool
    has_podman: bool
    has_winboat: bool
    has_display: bool
    python_path: str
    shell: str


@dataclass
class HardwareResource:
    """硬件资源信息 - 万能系统核心"""
    cpu_count: int = 1
    cpu_model: str = "unknown"
    cpu_arch: str = "unknown"  # x86_64 / arm64 / etc.
    ram_total_gb: float = 0.0
    ram_available_gb: float = 0.0
    gpu_count: int = 0
    gpu_list: List[Dict[str, Any]] = field(default_factory=list)
    has_cuda: bool = False
    has_rocm: bool = False
    has_metal: bool = False
    has_vulkan: bool = False
    has_opencl: bool = False
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0

    @property
    def compute_backend(self) -> str:
        """推荐的计算后端"""
        if self.has_cuda:
            return "cuda"
        if self.has_rocm:
            return "rocm"
        if self.has_metal:
            return "metal"
        if self.has_vulkan:
            return "vulkan"
        return "cpu"

    @property
    def summary(self) -> str:
        gpu_str = f"{self.gpu_count}xGPU({self.compute_backend})" if self.gpu_count else "CPU-only"
        return f"{self.cpu_arch}/{self.cpu_count}C/{gpu_str}/{self.ram_total_gb:.0f}GB"


class CompatLayer:
    """
    跨平台兼容层 - 万能系统

    Windows: 原生运行，所有功能直接可用
    Linux: 原生运行 + 可选WinBoat运行Windows应用
    macOS: 原生运行 + Metal加速
    WSL: 通过WinBoat Guest Server操控Windows桌面

    硬件自适应:
    - 多GPU(NVIDIA/AMD): CUDA/ROCm并行
    - 单GPU: 全量GPU推理
    - 多CPU: 多线程推理
    - 单CPU: 纯CPU推理
    """

    def __init__(self, event_bus: EventBus = None):
        self.bus = event_bus or bus
        self._platform_info: Optional[PlatformInfo] = None
        self._hardware: Optional[HardwareResource] = None
        self._winboat_api_url: Optional[str] = None

    async def detect_platform(self) -> PlatformInfo:
        """检测当前平台"""
        system = platform.system().lower()
        is_wsl = self._check_wsl()
        has_docker = shutil.which("docker") is not None
        has_podman = shutil.which("podman") is not None
        has_winboat = self._check_winboat()
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

        python_path = shutil.which("python") or shutil.which("python3") or "python"
        shell = "powershell" if system == "windows" else "bash"

        self._platform_info = PlatformInfo(
            system=system, is_wsl=is_wsl,
            has_docker=has_docker, has_podman=has_podman,
            has_winboat=has_winboat, has_display=has_display,
            python_path=python_path, shell=shell,
        )

        logger.info(f"Platform detected: {system}, WSL={is_wsl}, "
                     f"Docker={has_docker}, WinBoat={has_winboat}")
        return self._platform_info

    async def detect_hardware(self) -> HardwareResource:
        """检测硬件资源 - 万能系统核心"""
        hw = HardwareResource()

        # CPU信息
        hw.cpu_count = os.cpu_count() or 1
        hw.cpu_arch = platform.machine() or "unknown"
        hw.cpu_model = platform.processor() or "unknown"

        # RAM
        try:
            import psutil
            mem = psutil.virtual_memory()
            hw.ram_total_gb = mem.total / (1024**3)
            hw.ram_available_gb = mem.available / (1024**3)
        except ImportError:
            hw.ram_total_gb, hw.ram_available_gb = await self._detect_ram_fallback()

        # 磁盘
        try:
            import psutil
            disk = psutil.disk_usage("/")
            hw.disk_total_gb = disk.total / (1024**3)
            hw.disk_free_gb = disk.free / (1024**3)
        except Exception:
            pass

        # GPU检测 - 多种方式
        await self._detect_gpus(hw)

        self._hardware = hw
        logger.info(f"Hardware: {hw.summary}")
        return hw

    async def _detect_ram_fallback(self) -> tuple:
        """RAM检测回退方案"""
        total, available = 8.0, 4.0
        if os.name == "nt":
            result = await executor.execute_powershell(
                "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB"
            )
            if result.success:
                try:
                    total = float(result.stdout.strip())
                except ValueError:
                    pass
        return total, available

    async def _detect_gpus(self, hw: HardwareResource):
        """GPU检测 - 支持NVIDIA/AMD/Intel/Apple"""
        # 方式1: PyTorch CUDA
        try:
            import torch
            if torch.cuda.is_available():
                hw.has_cuda = True
                hw.gpu_count = torch.cuda.device_count()
                for i in range(hw.gpu_count):
                    props = torch.cuda.get_device_properties(i)
                    mem_free, mem_total = torch.cuda.mem_get_info(i)
                    hw.gpu_list.append({
                        "index": i, "name": props.name,
                        "type": "nvidia",
                        "vram_total_mb": mem_total // (1024*1024),
                        "vram_free_mb": mem_free // (1024*1024),
                        "compute_capability": f"{props.major}.{props.minor}",
                    })
                return
        except ImportError:
            pass

        # 方式2: nvidia-smi (不依赖PyTorch)
        nvidia_result = await self._detect_nvidia_smi()
        if nvidia_result:
            hw.has_cuda = True
            hw.gpu_count = len(nvidia_result)
            hw.gpu_list = nvidia_result
            return

        # 方式3: ROCm (AMD)
        if await self._detect_rocm():
            hw.has_rocm = True

        # 方式4: Metal (macOS)
        if platform.system() == "Darwin":
            hw.has_metal = True
            hw.gpu_count = 1
            hw.gpu_list.append({"name": "Apple Metal", "type": "metal"})

        # 方式5: Vulkan (通用)
        hw.has_vulkan = shutil.which("vulkaninfo") is not None

        # 方式6: OpenCL
        hw.has_opencl = shutil.which("clinfo") is not None

        # 方式7: Windows GPU (WMI)
        if os.name == "nt" and not hw.gpu_list:
            wmi_gpus = await self._detect_windows_gpu()
            if wmi_gpus:
                hw.gpu_count = len(wmi_gpus)
                hw.gpu_list = wmi_gpus

    async def _detect_nvidia_smi(self) -> List[Dict]:
        """通过nvidia-smi检测NVIDIA GPU"""
        smi_path = shutil.which("nvidia-smi")
        if not smi_path:
            return []

        result = await executor.execute(
            f'"{smi_path}" --query-gpu=index,name,memory.total,memory.free --format=csv,noheader,nounits',
            timeout=5
        )
        if not result.success:
            return []

        gpus = []
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "type": "nvidia",
                    "vram_total_mb": int(float(parts[2])),
                    "vram_free_mb": int(float(parts[3])),
                })
        return gpus

    async def _detect_rocm(self) -> bool:
        """检测ROCm (AMD GPU)"""
        smi = shutil.which("rocm-smi")
        if smi:
            result = await executor.execute(f"{smi} --showproductname", timeout=3)
            if result.success and result.stdout.strip():
                return True
        # 检查ROCm库
        rocm_paths = ["/opt/rocm", "/usr/lib/rocm"]
        return any(os.path.exists(p) for p in rocm_paths)

    async def _detect_windows_gpu(self) -> List[Dict]:
        """Windows GPU检测 (WMI)"""
        result = await executor.execute_powershell(
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name, AdapterRAM, DriverVersion | ConvertTo-Json"
        )
        if not result.success:
            return []

        try:
            import json
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
            gpus = []
            for i, gpu in enumerate(data):
                gpus.append({
                    "index": i,
                    "name": gpu.get("Name", "Unknown"),
                    "type": "unknown",
                    "vram_total_mb": (gpu.get("AdapterRAM", 0) or 0) // (1024*1024),
                })
            return gpus
        except (json.JSONDecodeError, TypeError):
            return []

    def get_hardware_info(self) -> Dict[str, Any]:
        """获取硬件信息摘要"""
        if not self._hardware:
            return {"status": "not_detected"}

        hw = self._hardware
        return {
            "cpu_count": hw.cpu_count,
            "cpu_model": hw.cpu_model,
            "cpu_arch": hw.cpu_arch,
            "ram_total_gb": round(hw.ram_total_gb, 1),
            "ram_available_gb": round(hw.ram_available_gb, 1),
            "gpu_count": hw.gpu_count,
            "gpus": hw.gpu_list,
            "compute_backend": hw.compute_backend,
            "has_cuda": hw.has_cuda,
            "has_rocm": hw.has_rocm,
            "has_metal": hw.has_metal,
            "has_vulkan": hw.has_vulkan,
            "disk_total_gb": round(hw.disk_total_gb, 1),
            "disk_free_gb": round(hw.disk_free_gb, 1),
            "summary": hw.summary,
        }

    def _check_wsl(self) -> bool:
        try:
            with open("/proc/version", "r") as f:
                return "microsoft" in f.read().lower()
        except FileNotFoundError:
            return False

    def _check_winboat(self) -> bool:
        return shutil.which("winboat") is not None

    @property
    def platform_info(self) -> PlatformInfo:
        if not self._platform_info:
            import asyncio
            self._platform_info = asyncio.get_event_loop().run_until_complete(self.detect_platform())
        return self._platform_info

    @property
    def is_windows(self) -> bool:
        return platform.system() == "Windows"

    @property
    def is_linux(self) -> bool:
        return platform.system() == "Linux"

    # ==================== WinBoat集成 ====================

    def set_winboat_url(self, url: str):
        """设置WinBoat Guest Server API地址"""
        self._winboat_api_url = url

    async def start_winboat_app(self, app_name: str) -> Dict[str, Any]:
        """通过WinBoat启动Windows应用(Linux上)"""
        if not self._winboat_api_url:
            return {"error": "WinBoat API not configured"}

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._winboat_api_url}/api/apps/launch",
                    json={"app": app_name},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    return await resp.json()
        except Exception as e:
            return {"error": str(e)}

    async def list_winboat_apps(self) -> List[Dict]:
        """列出WinBoat可用的Windows应用"""
        if not self._winboat_api_url:
            return []

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._winboat_api_url}/api/apps",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    return data.get("apps", [])
        except Exception:
            return []

    # ==================== 统一命令执行 ====================

    async def run_command(self, command: str, timeout: float = 30.0) -> Dict[str, Any]:
        """
        跨平台命令执行
        Windows: PowerShell
        Linux: Bash
        """
        if self.is_windows:
            result = await executor.execute_powershell(command, timeout)
        else:
            result = await executor.execute(command, timeout)

        return {
            "success": result.success,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.return_code,
            "duration": result.duration,
        }

    async def run_in_windows(self, command: str, timeout: float = 30.0) -> Dict[str, Any]:
        """
        在Windows环境中执行命令
        如果在Linux上，通过WinBoat执行
        如果在Windows上，直接执行
        """
        if self.is_windows:
            return await self.run_command(command, timeout)
        elif self._winboat_api_url:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self._winboat_api_url}/api/shell/execute",
                        json={"command": command, "timeout": timeout},
                        timeout=aiohttp.ClientTimeout(total=timeout + 5),
                    ) as resp:
                        return await resp.json()
            except Exception as e:
                return {"error": str(e)}
        else:
            return {"error": "Not on Windows and WinBoat not configured"}


# 全局单例
compat = CompatLayer()
