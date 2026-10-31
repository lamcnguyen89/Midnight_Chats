"""Read GPU memory capacity without allocating a CUDA context."""

import logging
import math
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("midnight.app")


def memory_usage():
    try:
        try:
            device = Path("/proc/device-tree/model").read_text().rstrip("\0").lower()
        except OSError:
            device = ""
        if "jetson" in device:
            # Jetson CPU/GPU share physical RAM. Available memory includes
            # reclaimable caches, which must not be counted as unavailable VRAM.
            entries = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                name, _, value = line.partition(":")
                if name in {"MemTotal", "MemAvailable"}:
                    entries[name] = int(value.split()[0]) * 1024
            total = entries["MemTotal"]
            available = entries["MemAvailable"]
            if total <= 0 or not 0 <= available <= total:
                raise ValueError("Invalid shared-memory counters")
            used = total - available
            kind = "shared"
        else:
            executable = shutil.which("nvidia-smi")
            if not executable:
                raise ValueError("GPU memory counters unavailable")
            result = subprocess.run([
                executable, "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits",
            ], capture_output=True, text=True, check=True, timeout=2)
            total = used = 0
            lines = result.stdout.strip().splitlines()
            if not lines:
                raise ValueError("No GPUs reported")
            for line in lines:
                capacity, usage = (float(part.strip()) for part in line.split(","))
                if not math.isfinite(capacity) or not math.isfinite(usage) or capacity <= 0 or not 0 <= usage <= capacity:
                    raise ValueError("Invalid GPU counters")
                total += round(capacity * 1024 ** 2)
                used += round(usage * 1024 ** 2)
            kind = "dedicated"
        if total <= 0:
            raise ValueError("Invalid total memory")
        return {"available": True, "kind": kind, "used_bytes": used,
                "total_bytes": total, "used_percent": round(used / total * 100, 1)}
    except (OSError, ValueError, KeyError, IndexError, OverflowError, subprocess.SubprocessError) as error:
        logger.debug("memory_metrics_unavailable", extra={"fields": {"error_type": type(error).__name__}})
        return {"available": False, "reason": "GPU memory reporting is currently unavailable."}
