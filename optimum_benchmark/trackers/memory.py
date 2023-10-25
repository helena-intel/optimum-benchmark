import os
from contextlib import contextmanager
from logging import getLogger
from multiprocessing import Pipe, Process
from multiprocessing.connection import Connection
from typing import Union

import psutil
import torch

from ..backends.base import Device
from ..env_utils import bytes_to_mega_bytes, is_nvidia_system, is_rocm_system
from ..import_utils import is_py3nvml_available, is_pyrsmi_available

LOGGER = getLogger("memory_tracker")


class MemoryTracker:
    def __init__(self, device: Union[torch.device, Device]):
        self.device = device
        self.peak_memory: int = 0

        if self.device.type == "cuda":
            CUDA_VISIBLE_DEVICES = os.environ.get("CUDA_VISIBLE_DEVICES", None)
            if CUDA_VISIBLE_DEVICES is not None:
                # if CUDA_VISIBLE_DEVICES is set, only the visible devices' memory is tracked
                self.device_ids = list(map(int, CUDA_VISIBLE_DEVICES.split(",")))
            else:
                # if CUDA_VISIBLE_DEVICES is not set, only the main device's memory is tracked
                # which is 0 because otherwise experiment would've raised an error
                self.device_ids = [self.device.index if self.device.index is not None else 0]
            LOGGER.info(f"Tracked CUDA devices: {self.device_ids}")

    @contextmanager
    def track(self, interval: float = 0.01):
        if self.device.type == "cuda":
            yield from self._cuda_peak_memory()
        else:
            yield from self._cpu_peak_memory(interval)

    def get_peak_memory(self):
        return bytes_to_mega_bytes(self.peak_memory)

    def _cuda_peak_memory(self):
        if is_nvidia_system():
            if is_py3nvml_available():
                import py3nvml.py3nvml as nvml
            else:
                raise ValueError(
                    "The library py3nvml is required to run memory benchmark on NVIDIA GPUs, but is not installed. Please install it through `pip install py3nvml`."
                )

            handles = []
            nvml.nvmlInit()
            for device_index in self.device_ids:
                handle = nvml.nvmlDeviceGetHandleByIndex(device_index)
                handles.append(handle)
            yield
            for handle in handles:
                meminfo = nvml.nvmlDeviceGetMemoryInfo(handle)
                self.peak_memory += meminfo.used

            nvml.nvmlShutdown()
        elif is_rocm_system():
            if is_pyrsmi_available():
                from pyrsmi import rocml
            else:
                raise ValueError(
                    "The library pyrsmi is required to run memory benchmark on RoCm-powered GPUs, but is not installed. Please install it following the instructions https://github.com/RadeonOpenCompute/pyrsmi."
                )
            rocml.smi_initialize()

            yield

            for device_index in self.device_ids:
                meminfo_used = rocml.smi_get_device_memory_used(device_index)
                self.peak_memory += meminfo_used
            rocml.smi_shutdown()
        else:
            raise ValueError("Could not measure GPU memory usage for a system different than NVIDIA or AMD RoCm.")

        LOGGER.debug(f"Peak memory usage: {self.get_peak_memory()} MB")

    def _cpu_peak_memory(self, interval: float):
        child_connection, parent_connection = Pipe()
        # instantiate process
        mem_process: Process = PeakMemoryMeasureProcess(os.getpid(), child_connection, interval)
        mem_process.start()
        # wait until we get memory
        parent_connection.recv()
        yield
        # start parent connection
        parent_connection.send(0)
        # receive peak memory
        self.peak_memory = parent_connection.recv()
        LOGGER.debug(f"Peak memory usage: {self.get_peak_memory()} MB")


class PeakMemoryMeasureProcess(Process):
    def __init__(self, process_id: int, child_connection: Connection, interval: float):
        super().__init__()
        self.process_id = process_id
        self.interval = interval
        self.connection = child_connection
        self.mem_usage = 0

    def run(self):
        self.connection.send(0)
        stop = False

        while True:
            process = psutil.Process(self.process_id)
            meminfo_attr = "memory_info" if hasattr(process, "memory_info") else "get_memory_info"
            memory = getattr(process, meminfo_attr)()[0]
            self.mem_usage = max(self.mem_usage, memory)

            if stop:
                break
            stop = self.connection.poll(self.interval)

        # send results to parent pipe
        self.connection.send(self.mem_usage)
        self.connection.close()
