import re
import subprocess
from pathlib import Path


ERROR_PATTERN = re.compile(
    r"error|fail|fault|panic|oom|out of memory|watchdog|mce|thermal|bug:|oops|hung_task|reset",
    re.IGNORECASE,
)


class DmesgTracker:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._boot_id = self._read_boot_id()

    @staticmethod
    def _read_boot_id() -> str:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    def _read_dmesg(self) -> list[str]:
        try:
            result = subprocess.run(
                ["dmesg", "--ctime", "--level=err,warn,crit,alert,emerg"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def collect(self) -> dict[str, str]:
        current_boot = self._read_boot_id()
        if current_boot != self._boot_id:
            self._seen.clear()
            self._boot_id = current_boot

        new_errors: list[str] = []
        for line in self._read_dmesg():
            if line in self._seen:
                continue
            self._seen.add(line)
            if ERROR_PATTERN.search(line):
                new_errors.append(line)

        return {
            "dmesg_new": str(len(new_errors)),
            "dmesg_lines": " | ".join(new_errors[:3]),
        }