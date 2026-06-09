import time
from pathlib import Path


def _read_boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _uptime_seconds() -> float:
    return float(Path("/proc/uptime").read_text().split()[0])


def _format_uptime(seconds: float) -> str:
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return "".join(parts)


def _load_avg() -> str:
    one, five, fifteen = Path("/proc/loadavg").read_text().split()[:3]
    return f"{one}/{five}/{fifteen}"


def _mem_swap() -> tuple[str, str]:
    mem_total_kb = mem_avail_kb = swap_total_kb = swap_free_kb = 0
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            mem_total_kb = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            mem_avail_kb = int(line.split()[1])
        elif line.startswith("SwapTotal:"):
            swap_total_kb = int(line.split()[1])
        elif line.startswith("SwapFree:"):
            swap_free_kb = int(line.split()[1])

    mem_used_gb = (mem_total_kb - mem_avail_kb) / 1024 / 1024
    mem_total_gb = mem_total_kb / 1024 / 1024
    swap_used_gb = (swap_total_kb - swap_free_kb) / 1024 / 1024
    swap_total_gb = swap_total_kb / 1024 / 1024
    return (
        f"{mem_used_gb:.1f}/{mem_total_gb:.0f}G",
        f"{swap_used_gb:.1f}/{swap_total_gb:.0f}G",
    )


def collect_system() -> dict[str, str]:
    uptime = _uptime_seconds()
    mem, swap = _mem_swap()
    return {
        "boot_id": _read_boot_id(),
        "uptime": _format_uptime(uptime),
        "uptime_s": str(int(uptime)),
        "load": _load_avg(),
        "mem": mem,
        "swap": swap,
        "epoch": str(int(time.time())),
    }