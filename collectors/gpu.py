import subprocess


def collect_gpu() -> dict[str, str]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,temperature.gpu,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"gpu": "-"}

    if result.returncode != 0 or not result.stdout.strip():
        return {"gpu": "-"}

    parts = []
    for line in result.stdout.strip().splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 5:
            continue
        idx, temp, mem_used, mem_total, util = fields[:5]
        parts.append(f"gpu{idx}={temp}C/{mem_used}MiB/{util}%")

    return {"gpu": ",".join(parts) if parts else "-"}