import subprocess


def collect_docker() -> dict[str, str]:
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--format",
                "{{.Names}}:{{.Status}}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"docker": "-", "docker_up": "0", "docker_exit": "0"}

    if result.returncode != 0 or not result.stdout.strip():
        return {"docker": "-", "docker_up": "0", "docker_exit": "0"}

    up = exit_count = 0
    summaries: list[str] = []
    for line in result.stdout.strip().splitlines():
        if ":" not in line:
            continue
        name, status = line.split(":", 1)
        if status.lower().startswith("up"):
            up += 1
            summaries.append(f"{name}:up")
        else:
            exit_count += 1
            summaries.append(f"{name}:exit")

    return {
        "docker": ",".join(summaries[:12]),
        "docker_up": str(up),
        "docker_exit": str(exit_count),
    }