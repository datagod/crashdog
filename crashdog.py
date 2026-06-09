#!/usr/bin/env python3
"""CrashDog — lightweight crash-forensics snapshot daemon."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from collectors import (
    DmesgTracker,
    ForensicsDumper,
    RingPersister,
    collect_docker,
    collect_gpu,
    collect_pressure,
    collect_processes,
    collect_system,
)

DEFAULT_CONFIG = Path(__file__).resolve().parent / "crashdog.default.yaml"


class CrashDog:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.log_dir = Path(config["log_dir"])
        self.state_file = Path(config["state_file"])
        self.last_snapshot_file = Path(config["last_snapshot_file"])
        self.interval = int(config.get("interval_seconds", 60))
        self.top_n = int(config.get("top_processes", 5))
        self.keep_days = int(config.get("keep_days", 14))
        forensics_cfg = config.get("forensics", {})
        self.enabled = {
            "gpu": bool(config.get("collectors", {}).get("gpu", True)),
            "docker": bool(config.get("collectors", {}).get("docker", True)),
            "dmesg": bool(config.get("collectors", {}).get("dmesg", True)),
            "processes": bool(config.get("collectors", {}).get("processes", True)),
            "pressure": bool(config.get("collectors", {}).get("pressure", True)),
        }
        self.persist_dmesg = bool(forensics_cfg.get("persist_dmesg", True))
        self.save_forensics = bool(forensics_cfg.get("save_on_crash", True))
        self.dmesg = DmesgTracker()
        self.persister = RingPersister(config)
        self.forensics = ForensicsDumper(config)
        self._running = True
        self._current_log_date: str | None = None
        self._log_fp = None

    def _now_local(self) -> datetime:
        return datetime.now().astimezone()

    def _timestamp(self) -> str:
        return self._now_local().isoformat(timespec="seconds")

    def _log_path_for_date(self, date_str: str) -> Path:
        return self.log_dir / f"crashdog-{date_str}.log"

    def _current_log_path(self) -> Path:
        return self._log_path_for_date(self._now_local().strftime("%Y%m%d"))

    def _open_log(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        date_str = self._now_local().strftime("%Y%m%d")
        if self._log_fp and self._current_log_date == date_str:
            return
        if self._log_fp:
            self._log_fp.close()
        self._current_log_date = date_str
        self._log_fp = self._current_log_path().open("a", encoding="utf-8")

    def _fsync_file(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as fh:
            os.fsync(fh.fileno())

    def _write_line(self, line: str) -> None:
        self._open_log()
        assert self._log_fp is not None
        self._log_fp.write(line + "\n")
        self._log_fp.flush()
        os.fsync(self._log_fp.fileno())
        self.last_snapshot_file.parent.mkdir(parents=True, exist_ok=True)
        self.last_snapshot_file.write_text(line + "\n", encoding="utf-8")
        self._fsync_file(self.last_snapshot_file)

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_file)
        self._fsync_file(self.state_file)

    def _format_snapshot(self, fields: dict[str, str]) -> str:
        ordered = [
            ("uptime", "uptime"),
            ("load", "load"),
            ("mem", "mem"),
            ("swap", "swap"),
            ("psi", "psi"),
            ("gpu", "gpu"),
            ("docker_up", "docker_up"),
            ("docker_exit", "docker_exit"),
            ("top_cpu", "top_cpu"),
            ("top_mem", "top_mem"),
            ("dmesg_new", "dmesg_new"),
        ]
        parts = [f"{key}={fields[value]}" for key, value in ordered if value in fields and fields[value]]
        line = f"{self._timestamp()} SNAPSHOT {' '.join(parts)}"
        if fields.get("docker") and fields["docker"] != "-":
            line += f" docker={fields['docker']}"
        if fields.get("dmesg_lines"):
            line += f" dmesg={fields['dmesg_lines']}"
        return line

    def _atop_hint(self, snapshot_time: str | None) -> str:
        if not snapshot_time:
            return "-"
        try:
            dt = datetime.fromisoformat(snapshot_time)
        except ValueError:
            return "-"
        date_part = dt.strftime("%Y%m%d")
        time_part = dt.strftime("%H%M")
        atop_file = f"/var/log/atop/atop_{date_part}"
        if Path(atop_file).exists():
            return f"atop -r {atop_file} -b {time_part}"
        return f"atop file not found for {date_part}"

    def _write_crash_gap(self, previous: dict[str, Any], current_boot_id: str) -> None:
        last_snapshot = previous.get("last_snapshot")
        last_boot_id = previous.get("boot_id", "unknown")
        gap_est = "-"
        if last_snapshot:
            try:
                last_dt = datetime.fromisoformat(last_snapshot)
                delta = self._now_local() - last_dt
                gap_est = f"{int(delta.total_seconds() // 60)}m"
            except ValueError:
                pass

        line = (
            f"{self._timestamp()} CRASH_GAP "
            f"prev_boot={last_boot_id} "
            f"new_boot={current_boot_id} "
            f"last_snapshot={last_snapshot or '-'} "
            f"gap_est={gap_est} "
            f"atop_hint=\"{self._atop_hint(last_snapshot)}\""
        )
        self._write_line(line)
        self._write_boot_summary(previous)

    def _write_boot_summary(self, previous: dict[str, Any] | None = None) -> None:
        self._write_line(f"{self._timestamp()} BOOT_SUMMARY hostname={os.uname().nodename}")
        try:
            last_reboot = subprocess.run(
                ["last", "-x", "reboot", "crash", "-F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if last_reboot.stdout:
                for entry in last_reboot.stdout.splitlines()[:3]:
                    self._write_line(f"{self._timestamp()} BOOT_SUMMARY {entry.strip()}")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        for entry in self.forensics.boot_dmesg_errors(limit=10):
            self._write_line(f"{self._timestamp()} BOOT_DMESG {entry}")

        for entry in self.forensics.boot_panic_lines():
            self._write_line(f"{self._timestamp()} BOOT_PANIC {entry}")

        if self.save_forensics and previous is not None:
            last_snapshot = previous.get("last_snapshot")
            last_snapshot_line = ""
            if last_snapshot:
                last_snapshot_file = self.last_snapshot_file
                if last_snapshot_file.exists():
                    last_snapshot_line = last_snapshot_file.read_text(encoding="utf-8").strip()
            dump_dir = self.forensics.write_crash_dump(
                self._timestamp(),
                previous,
                last_snapshot_line or None,
            )
            self._write_line(
                f"{self._timestamp()} FORENSICS_DUMP dir={dump_dir} "
                f"files={len(list(dump_dir.iterdir()))}"
            )

    def _write_clean_shutdown(self, previous: dict[str, Any]) -> None:
        self._write_line(
            f"{self._timestamp()} CLEAN_SHUTDOWN prev_boot={previous.get('boot_id', '-')} "
            f"last_snapshot={previous.get('last_snapshot', '-')}"
        )

    def _check_boot_transition(self) -> None:
        system = collect_system()
        current_boot_id = system["boot_id"]
        previous = self._load_state()

        if previous.get("clean_shutdown"):
            self._write_clean_shutdown(previous)
        elif previous.get("boot_id") and previous["boot_id"] != current_boot_id:
            self._check_boot_transition_crash(previous, current_boot_id)

        state = {
            "boot_id": current_boot_id,
            "boot_time": self._timestamp(),
            "last_snapshot": previous.get("last_snapshot"),
            "clean_shutdown": False,
        }
        self._save_state(state)

    def _check_boot_transition_crash(self, previous: dict[str, Any], current_boot_id: str) -> None:
        self._write_crash_gap(previous, current_boot_id)

    def _collect_snapshot_fields(self) -> dict[str, str]:
        fields = collect_system()
        if self.enabled["processes"]:
            fields.update(collect_processes(self.top_n))
        if self.enabled["pressure"]:
            fields.update(collect_pressure())
        if self.enabled["dmesg"]:
            fields.update(self.dmesg.collect())
        if self.enabled["gpu"]:
            fields.update(collect_gpu())
        if self.enabled["docker"]:
            fields.update(collect_docker())
        return fields

    def _persist_runtime_artifacts(self, state: dict[str, Any]) -> None:
        if self.persist_dmesg:
            self.persister.persist_dmesg()
        self.persister.persist_state(state)

    def _rotate_old_logs(self) -> None:
        cutoff = time.time() - self.keep_days * 86400
        for path in self.log_dir.glob("crashdog-*.log"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        forensics_dir = Path(self.config.get("forensics", {}).get("dir", self.log_dir / "forensics"))
        if forensics_dir.is_dir():
            for path in forensics_dir.iterdir():
                if path.is_dir() and path.stat().st_mtime < cutoff:
                    for child in path.iterdir():
                        child.unlink(missing_ok=True)
                    path.rmdir()

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        if signum == signal.SIGTERM:
            state = self._load_state()
            state["clean_shutdown"] = True
            state["last_snapshot"] = self._timestamp()
            self._save_state(state)
            self._persist_runtime_artifacts(state)
            self._write_line(f"{self._timestamp()} SHUTDOWN signal=SIGTERM clean=true")
        self._running = False

    def run(self, foreground: bool = False) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        self._check_boot_transition()
        self._write_line(f"{self._timestamp()} STARTED interval={self.interval}s pid={os.getpid()}")

        if foreground:
            print(
                f"CrashDog running (pid {os.getpid()}, interval {self.interval}s)\n"
                f"  log:      {self._current_log_path()}\n"
                f"  snapshot: {self.last_snapshot_file}\n"
                "Press Ctrl+C to stop. (Use systemd in production — not this foreground mode.)",
                file=sys.stderr,
            )
            sys.stderr.flush()

        while self._running:
            fields = self._collect_snapshot_fields()
            line = self._format_snapshot(fields)
            self._write_line(line)

            state = self._load_state()
            state.update(
                {
                    "boot_id": fields["boot_id"],
                    "last_snapshot": self._timestamp(),
                    "last_epoch": fields.get("epoch"),
                    "clean_shutdown": False,
                    "last_psi": fields.get("psi"),
                    "last_load": fields.get("load"),
                    "last_mem": fields.get("mem"),
                    "last_swap": fields.get("swap"),
                }
            )
            self._save_state(state)
            self._persist_runtime_artifacts(state)
            self._rotate_old_logs()

            for _ in range(self.interval):
                if not self._running:
                    break
                time.sleep(1)

        if self._log_fp:
            self._log_fp.close()


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve_config_path(explicit: Path | None) -> Path:
    if explicit and explicit.exists():
        return explicit
    candidates = [
        Path("/etc/crashdog/config.yaml"),
        Path.home() / ".config/crashdog/config.yaml",
        DEFAULT_CONFIG,
    ]
    for path in candidates:
        if path.exists():
            return path
    return explicit or DEFAULT_CONFIG


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="crashdog", description="CrashDog crash-forensics tool")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config file",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show CrashDog status report (default)")
    subparsers.add_parser("run", help="Run the snapshot daemon")
    check_boot = subparsers.add_parser("check-boot", help="Evaluate boot transition and exit")
    check_boot.add_argument("--check-boot", action="store_true", help=argparse.SUPPRESS)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.command
    if command is None:
        command = "status"

    config_path = resolve_config_path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    config = load_config(config_path)

    if command == "status":
        from status import show_status

        return show_status(config)

    dog = CrashDog(config)
    if command == "check-boot":
        dog._check_boot_transition()
        return 0

    if command == "run":
        state_dir = dog.state_file.parent
        if not os.access(state_dir, os.W_OK):
            print(
                f"Cannot write state to {state_dir} (permission denied).\n"
                "The daemon must run via systemd as root, or use the user install.\n"
                "  crashdog status   # view status\n"
                f"  sudo {Path(__file__).resolve().parent / 'fix-install.sh'}",
                file=sys.stderr,
            )
            return 1
        dog.run(foreground=True)
        return 0

    print(f"Unknown command: {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())