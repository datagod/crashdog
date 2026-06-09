from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def _fsync_file(path: Path) -> None:
    with path.open("r", encoding="utf-8") as fh:
        os.fsync(fh.fileno())


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    _fsync_file(path)


def _run(cmd: list[str], timeout: int = 15) -> str:
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
        return ""
    return result.stdout


class RingPersister:
    def __init__(self, config: dict[str, Any]) -> None:
        self.dmesg_ring_file = Path(config.get("dmesg_ring_file", Path(config["log_dir"]) / "dmesg-ring.txt"))
        self.dmesg_tail_file = Path(config.get("dmesg_tail_file", Path(config["log_dir"]) / "dmesg-tail.txt"))
        self.last_state_file = Path(config.get("last_state_file", Path(config["log_dir"]) / "last-state.json"))
        forensics = config.get("forensics", {})
        self.dmesg_levels = forensics.get("dmesg_levels", "err,warn,crit,alert,emerg")
        self.dmesg_tail_lines = int(forensics.get("dmesg_tail_lines", 400))

    def persist_dmesg(self) -> None:
        ring = _run(["dmesg", "--ctime", f"--level={self.dmesg_levels}"])
        if ring.strip():
            _atomic_write(self.dmesg_ring_file, ring.rstrip() + "\n")

        full = _run(["dmesg", "--ctime"])
        if full.strip():
            lines = full.splitlines()
            tail = "\n".join(lines[-self.dmesg_tail_lines :])
            _atomic_write(self.dmesg_tail_file, tail.rstrip() + "\n")

    def persist_state(self, state: dict[str, Any]) -> None:
        _atomic_write(self.last_state_file, json.dumps(state, indent=2) + "\n")