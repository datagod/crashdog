from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from banner import banner_text
from collectors import collect_system

# ANSI colors
RST = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"


def _load_config_data() -> tuple[dict[str, Any], Path]:
    import yaml

    candidates = [
        Path("/etc/crashdog/config.yaml"),
        Path.home() / ".config/crashdog/config.yaml",
        Path(__file__).resolve().parent / "crashdog.default.yaml",
    ]
    for path in candidates:
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                return yaml.safe_load(fh), path
    raise FileNotFoundError("No CrashDog config found")


def _service_status(scope: str) -> tuple[str, str]:
    unit = "crashdog.service"
    cmd = ["systemctl", "--user", "is-active", unit] if scope == "user" else ["systemctl", "is-active", unit]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return scope, "unknown"
    state = result.stdout.strip() or result.stderr.strip() or "unknown"
    return scope, state


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _parse_snapshot(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    if " SNAPSHOT " not in line:
        return fields
    _, rest = line.split(" SNAPSHOT ", 1)
    for token in rest.split():
        if "=" in token and not token.startswith("docker="):
            key, value = token.split("=", 1)
            fields[key] = value
    if "docker=" in rest:
        fields["docker"] = rest.split("docker=", 1)[1].split(" dmesg=", 1)[0]
    return fields


def _find_last_event(log_path: Path, prefix: str) -> str | None:
    if not log_path.exists():
        return None
    last = None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if f" {prefix} " in line:
            last = line
    return last


def _recent_reboots() -> list[str]:
    try:
        result = subprocess.run(
            ["last", "-x", "reboot", "crash", "-F"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in result.stdout.splitlines()[:3] if line.strip()]


def _print_header() -> None:
    use_color = sys.stdout.isatty()
    banner = banner_text(use_color=use_color)

    print(banner)
    subtitle = "crash forensics · headless watchdog"
    if use_color:
        print(f"{DIM}{CYAN}{subtitle}{RST}")
        print(f"{DIM}{'─' * 60}{RST}")
    else:
        print(subtitle)
        print("=" * 60)


def _format_age(timestamp: str | None) -> str:
    if not timestamp:
        return "-"
    try:
        dt = datetime.fromisoformat(timestamp)
    except ValueError:
        return "-"
    seconds = int((datetime.now().astimezone() - dt).total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"


def _resolve_config_path() -> Path:
    for path in (
        Path("/etc/crashdog/config.yaml"),
        Path.home() / ".config/crashdog/config.yaml",
    ):
        if path.exists():
            return path
    return Path("(unknown)")


def show_status(config: dict[str, Any] | None = None) -> int:
    if config is None:
        config, config_path = _load_config_data()
    else:
        config_path = _resolve_config_path()
    log_dir = Path(config["log_dir"])
    state_file = Path(config["state_file"])
    last_snapshot_file = Path(config["last_snapshot_file"])
    today_log = log_dir / f"crashdog-{datetime.now().strftime('%Y%m%d')}.log"

    state = _read_state(state_file)
    live = collect_system()
    user_state, user_active = _service_status("user")
    sys_state, sys_active = _service_status("system")

    last_line = last_snapshot_file.read_text(encoding="utf-8").strip() if last_snapshot_file.exists() else ""
    snapshot = _parse_snapshot(last_line)
    crash_gap = _find_last_event(today_log, "CRASH_GAP")
    clean_shutdown = _find_last_event(today_log, "CLEAN_SHUTDOWN")
    forensics_dump = _find_last_event(today_log, "FORENSICS_DUMP")
    forensics_dir = Path(config.get("forensics", {}).get("dir", log_dir / "forensics"))
    latest_forensics = None
    if forensics_dir.is_dir():
        candidates = sorted(forensics_dir.glob("*-crash"), key=lambda p: p.stat().st_mtime, reverse=True)
        latest_forensics = candidates[0] if candidates else None

    running = user_active == "active" or sys_active == "active"
    service_label = "running" if running else "stopped"
    if user_active == "active" and sys_active == "active":
        service_detail = "user + system services active (duplicate — pick one)"
    elif user_active == "active":
        service_detail = "user service active"
    elif sys_active == "active":
        service_detail = "system service active"
    elif user_active == "inactive" and sys_active in ("inactive", "unknown"):
        service_detail = "not running"
    else:
        service_detail = f"user={user_active}, system={sys_active}"

    _print_header()
    print(f"Service:        {service_label} ({service_detail})")
    print(f"Config:         {config_path}")
    print(f"Interval:       {config.get('interval_seconds', 60)}s")
    print(f"Hostname:       {Path('/proc/sys/kernel/hostname').read_text().strip()}")
    print()
    print("Boot")
    print("-" * 60)
    print(f"Boot ID:        {live.get('boot_id', '-')}")
    print(f"Uptime:         {live.get('uptime', '-')}")
    print(f"Load:           {live.get('load', '-')}")
    print(f"Memory:         {live.get('mem', '-')}")
    print(f"Swap:           {live.get('swap', '-')}")
    if state.get("boot_time"):
        print(f"Tracked since:  {state['boot_time']}")
    print()
    print("Last Snapshot")
    print("-" * 60)
    if last_line:
        print(last_line)
        print(f"Age:            {_format_age(state.get('last_snapshot'))}")
        if snapshot.get("gpu"):
            print(f"GPU:            {snapshot['gpu']}")
        if snapshot.get("docker_up"):
            print(f"Docker:         {snapshot['docker_up']} up, {snapshot.get('docker_exit', '0')} exited")
        if snapshot.get("top_cpu"):
            print(f"Top CPU:        {snapshot['top_cpu']}")
        if snapshot.get("top_mem"):
            print(f"Top MEM:        {snapshot['top_mem']}")
        if snapshot.get("psi"):
            print(f"PSI:            {snapshot['psi']}")
        if snapshot.get("pwr"):
            print(f"Power:          {snapshot['pwr']}")
    else:
        print("(no snapshot yet)")
    print()
    print("Crash History")
    print("-" * 60)
    if crash_gap:
        print(crash_gap)
    elif clean_shutdown:
        print(clean_shutdown)
    else:
        print("No crash gap recorded today.")
    if forensics_dump:
        print(forensics_dump)
    for line in _recent_reboots():
        print(f"  {line}")
    print()
    print("Files")
    print("-" * 60)
    print(f"Today's log:    {today_log}")
    print(f"Last snapshot:  {last_snapshot_file}")
    print(f"State:          {state_file}")
    dmesg_ring = Path(config.get("dmesg_ring_file", log_dir / "dmesg-ring.txt"))
    dmesg_tail = Path(config.get("dmesg_tail_file", log_dir / "dmesg-tail.txt"))
    if dmesg_ring.exists():
        print(f"Dmesg ring:     {dmesg_ring}")
    if dmesg_tail.exists():
        print(f"Dmesg tail:     {dmesg_tail}")
    if latest_forensics:
        print(f"Forensics:      {latest_forensics}")
    print()
    print("Commands")
    print("-" * 60)
    print(f"  tail -f {today_log}")
    if today_log.exists():
        match = re.search(r'atop_hint="([^"]+)"', today_log.read_text(encoding="utf-8"))
        if match:
            print(f"  {match.group(1)}")
    return 0