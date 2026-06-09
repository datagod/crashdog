from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

PANIC_PATTERN = re.compile(
    r"panic|oops|BUG:|kernel BUG|RIP:|Call Trace|hard LOCKUP|soft lockup|hung_task|Out of memory|oom-kill|Machine check|MCE|watchdog:",
    re.IGNORECASE,
)


def _run(cmd: list[str], timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0 and not result.stdout:
        return result.stderr.strip()
    return result.stdout


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _copy_if_exists(src: Path, dest: Path) -> bool:
    if not src.exists() or src.stat().st_size == 0:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return True


def _panic_lines(text: str, limit: int = 50) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        if PANIC_PATTERN.search(line):
            lines.append(line.strip())
    return lines[:limit]


class ForensicsDumper:
    def __init__(self, config: dict[str, Any]) -> None:
        forensics = config.get("forensics", {})
        self.dir = Path(forensics.get("dir", Path(config["log_dir"]) / "forensics"))
        self.journal_lines = int(forensics.get("journal_lines", 200))
        self.kernel_journal_lines = int(forensics.get("kernel_journal_lines", 100))
        self.dmesg_levels = forensics.get("dmesg_levels", "err,warn,crit,alert,emerg")
        self.dmesg_tail_lines = int(forensics.get("dmesg_tail_lines", 400))
        self.dmesg_ring_file = Path(config.get("dmesg_ring_file", Path(config["log_dir"]) / "dmesg-ring.txt"))
        self.dmesg_tail_file = Path(config.get("dmesg_tail_file", Path(config["log_dir"]) / "dmesg-tail.txt"))
        self.last_state_file = Path(config.get("last_state_file", Path(config["log_dir"]) / "last-state.json"))

    def dump_dir_for(self, timestamp: str) -> Path:
        safe = timestamp.replace(":", "").replace("+", "")
        return self.dir / f"{safe}-crash"

    def write_crash_dump(
        self,
        timestamp: str,
        previous_state: dict[str, Any],
        last_snapshot: str | None,
    ) -> Path:
        dump_dir = self.dump_dir_for(timestamp)
        dump_dir.mkdir(parents=True, exist_ok=True)

        manifest: dict[str, Any] = {
            "timestamp": timestamp,
            "previous_state": previous_state,
            "last_snapshot": last_snapshot,
            "files": [],
        }

        def add_file(name: str, content: str) -> None:
            if not content.strip():
                return
            path = dump_dir / name
            _write_text(path, content.rstrip() + "\n")
            manifest["files"].append(name)

        def add_copy(name: str, src: Path) -> None:
            if _copy_if_exists(src, dump_dir / name):
                manifest["files"].append(name)

        add_copy("pre-crash-dmesg-ring.txt", self.dmesg_ring_file)
        add_copy("pre-crash-dmesg-tail.txt", self.dmesg_tail_file)
        add_copy("pre-crash-state.json", self.last_state_file)
        if last_snapshot:
            add_file("pre-crash-last-snapshot.txt", last_snapshot)

        boot_dmesg = _run(["dmesg", "--ctime"])
        add_file("boot-dmesg-full.txt", boot_dmesg)
        add_file(
            "boot-dmesg-errors.txt",
            _run(["dmesg", "--ctime", f"--level={self.dmesg_levels}"]),
        )

        panic_hits = _panic_lines(boot_dmesg)
        if panic_hits:
            add_file("boot-dmesg-panic-hits.txt", "\n".join(panic_hits))

        add_file(
            "journal-prev-boot.txt",
            _run(
                [
                    "journalctl",
                    "-b",
                    "-1",
                    "--no-pager",
                    "-n",
                    str(self.journal_lines),
                    "--output=short-iso-precise",
                ]
            ),
        )
        add_file(
            "journal-kernel-prev-boot.txt",
            _run(
                [
                    "journalctl",
                    "-b",
                    "-1",
                    "-k",
                    "--no-pager",
                    "-n",
                    str(self.kernel_journal_lines),
                    "--output=short-iso-precise",
                ]
            ),
        )
        add_file(
            "journal-prev-boot-warnings.txt",
            _run(
                [
                    "journalctl",
                    "-b",
                    "-1",
                    "--no-pager",
                    "-p",
                    "warning..alert",
                    "-n",
                    str(self.journal_lines),
                    "--output=short-iso-precise",
                ]
            ),
        )

        for src_name, dest_name in (
            ("/proc/meminfo", "boot-meminfo.txt"),
            ("/proc/vmstat", "boot-vmstat.txt"),
            ("/proc/loadavg", "boot-loadavg.txt"),
        ):
            src = Path(src_name)
            if src.exists():
                add_file(dest_name, src.read_text(encoding="utf-8"))

        add_file("nvidia-smi-query.txt", _run(["nvidia-smi", "-q"]))
        add_file("mce-ras-errors.txt", _run(["ras-mc-ctl", "--errors"]))
        add_file("mcelog-tail.txt", _run(["tail", "-n", "100", "/var/log/mcelog"]))

        pstore = Path("/sys/fs/pstore")
        try:
            if pstore.is_dir():
                pstore_files = sorted(path.name for path in pstore.iterdir() if path.is_file())
                if pstore_files:
                    add_file("pstore-index.txt", "\n".join(pstore_files))
                    for name in pstore_files[:5]:
                        try:
                            add_file(
                                f"pstore/{name}",
                                pstore.joinpath(name).read_text(encoding="utf-8", errors="replace"),
                            )
                        except OSError:
                            pass
        except OSError:
            pass

        _write_text(dump_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
        return dump_dir

    def boot_panic_lines(self) -> list[str]:
        boot_dmesg = _run(["dmesg", "--ctime"])
        return _panic_lines(boot_dmesg, limit=20)

    def boot_dmesg_errors(self, limit: int = 15) -> list[str]:
        text = _run(["dmesg", "--ctime", f"--level={self.dmesg_levels}"])
        return [line.strip() for line in text.splitlines() if line.strip()][:limit]