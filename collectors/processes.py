from pathlib import Path


def _read_process_stats() -> list[tuple[float, float, str]]:
    processes: list[tuple[float, float, str]] = []
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            pid = stat_path.parent.name
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            fields = stat_path.read_text().split()
            utime = int(fields[13])
            stime = int(fields[14])
            rss_pages = int(fields[23])
        except (OSError, IndexError, ValueError):
            continue
        cpu_ticks = utime + stime
        rss_mb = rss_pages * 4096 / 1024 / 1024
        processes.append((cpu_ticks, rss_mb, comm))

    return processes


def collect_processes(top_n: int = 5) -> dict[str, str]:
    processes = _read_process_stats()
    if not processes:
        return {"top_cpu": "-", "top_mem": "-"}

    by_cpu = sorted(processes, key=lambda item: item[0], reverse=True)[:top_n]
    by_mem = sorted(processes, key=lambda item: item[1], reverse=True)[:top_n]

    top_cpu = ",".join(f"{name}" for _, _, name in by_cpu)
    top_mem = ",".join(f"{name}({rss:.0f}M)" for _, rss, name in by_mem)
    return {"top_cpu": top_cpu, "top_mem": top_mem}