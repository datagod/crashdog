from pathlib import Path


def _parse_avg10(path: Path, kind: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == kind and parts[1].startswith("avg10="):
            return parts[1].split("=", 1)[1]
    return None


def collect_pressure() -> dict[str, str]:
    cpu = _parse_avg10(Path("/proc/pressure/cpu"), "some")
    mem = _parse_avg10(Path("/proc/pressure/memory"), "some")
    memfull = _parse_avg10(Path("/proc/pressure/memory"), "full")
    io = _parse_avg10(Path("/proc/pressure/io"), "some")

    if not any((cpu, mem, memfull, io)):
        return {"psi": "-"}

    parts = []
    if cpu is not None:
        parts.append(f"cpu:{cpu}")
    if mem is not None:
        parts.append(f"mem:{mem}")
    if memfull is not None:
        parts.append(f"memfull:{memfull}")
    if io is not None:
        parts.append(f"io:{io}")
    return {"psi": ",".join(parts)}