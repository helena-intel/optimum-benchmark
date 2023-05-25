from dataclasses import MISSING, dataclass, field
from contextlib import contextmanager
from logging import getLogger
from typing import List

import torch
import time


LOGGER = getLogger("tracker")


@dataclass
class Tracker:
    device: str = MISSING  # type: ignore
    tracked_latencies: List[float] = field(default_factory=list)

    @contextmanager
    def track_latency(self):
        if self.device == "cuda":
            yield from self._cuda_inference_latency()
        else:
            yield from self._cpu_inference_latency()

    def _cuda_inference_latency(self):
        start_event = torch.cuda.Event(enable_timing=True)
        end_event = torch.cuda.Event(enable_timing=True)
        torch.cuda.synchronize()
        start_event.record(stream=torch.cuda.current_stream())
        yield
        end_event.record(stream=torch.cuda.current_stream())
        torch.cuda.synchronize()
        latency_ms = start_event.elapsed_time(end_event)
        latency = latency_ms / 1e3

        LOGGER.debug(f"Tracked CUDA latency took: {latency}s)")
        self.tracked_latencies.append(latency)

    def _cpu_inference_latency(self):
        start = time.perf_counter_ns()
        yield
        end = time.perf_counter_ns()
        latency_ns = end - start
        latency = latency_ns / 1e9

        LOGGER.debug(f"Tracked CPU latency took: {latency}s)")
        self.tracked_latencies.append(latency)
