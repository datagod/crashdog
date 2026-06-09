from __future__ import annotations

import subprocess
import time
from pathlib import Path


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _discover_rapl_domains() -> list[tuple[str, Path]]:
    base = Path("/sys/class/powercap")
    if not base.is_dir():
        return []

    domains: list[tuple[str, Path]] = []
    for energy_path in sorted(base.glob("**/energy_uj")):
        cap_dir = energy_path.parent
        name_path = cap_dir / "name"
        if name_path.exists():
            name = name_path.read_text(encoding="utf-8").strip()
        else:
            name = cap_dir.name
        domains.append((name, energy_path))
    return domains


def _format_watts(watts: float) -> str:
    if watts < 10:
        return f"{watts:.1f}W"
    return f"{round(watts)}W"


class PowerTracker:
    """Track RAPL energy counters and compute average watts between samples."""

    def __init__(
        self,
        *,
        rapl: bool = True,
        nvidia: bool = True,
        nvme: bool = True,
    ) -> None:
        self._rapl = rapl
        self._nvidia = nvidia
        self._nvme = nvme
        self._rapl_prev: dict[str, tuple[int, float, int]] = {}
        self._rapl_max: dict[str, int] = {}

    def _rapl_watts(self, name: str, energy_path: Path) -> str | None:
        energy_uj = _read_int(energy_path)
        if energy_uj is None:
            return None

        max_path = energy_path.parent / "max_energy_range_uj"
        max_uj = _read_int(max_path)
        if max_uj:
            self._rapl_max[name] = max_uj

        now = time.monotonic()
        prev = self._rapl_prev.get(name)
        self._rapl_prev[name] = (energy_uj, now, max_uj or self._rapl_max.get(name, 0))

        if prev is None:
            return None

        prev_energy, prev_time, prev_max = prev
        elapsed = now - prev_time
        if elapsed <= 0:
            return None

        delta_uj = energy_uj - prev_energy
        wrap = prev_max or self._rapl_max.get(name, 0)
        if delta_uj < 0 and wrap:
            delta_uj += wrap

        if delta_uj < 0:
            return None

        watts = delta_uj / elapsed / 1_000_000
        return _format_watts(watts)

    def collect_cpu(self) -> dict[str, str]:
        parts: list[str] = []
        for name, energy_path in _discover_rapl_domains():
            watts = self._rapl_watts(name, energy_path)
            if watts:
                parts.append(f"{name}={watts}")

        if not parts:
            return {"pwr_cpu": "-"}
        return {"pwr_cpu": ",".join(parts)}

    @staticmethod
    def collect_nvidia() -> dict[str, str]:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,power.draw,power.limit",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {"pwr_gpu": "-"}

        if result.returncode != 0 or not result.stdout.strip():
            return {"pwr_gpu": "-"}

        parts: list[str] = []
        total_w = 0.0
        have_total = False
        for line in result.stdout.strip().splitlines():
            fields = [field.strip() for field in line.split(",")]
            if len(fields) < 2:
                continue
            idx, draw = fields[0], fields[1]
            if draw in {"", "[N/A]"}:
                continue
            try:
                watts = float(draw)
            except ValueError:
                continue
            parts.append(f"gpu{idx}={_format_watts(watts)}")
            total_w += watts
            have_total = True

        if not parts:
            return {"pwr_gpu": "-"}

        out: dict[str, str] = {"pwr_gpu": ",".join(parts)}
        if have_total:
            out["pwr_gpu_sum"] = _format_watts(total_w)
        return out

    @staticmethod
    def collect_nvme() -> dict[str, str]:
        base = Path("/sys/class/hwmon")
        if not base.is_dir():
            return {}

        parts: list[str] = []
        for name_path in sorted(base.glob("hwmon*/name")):
            hwmon_dir = name_path.parent
            if name_path.read_text(encoding="utf-8").strip() != "nvme":
                continue
            for power_path in sorted(hwmon_dir.glob("power*_input")):
                microwatts = _read_int(power_path)
                if microwatts is None:
                    continue
                label_path = hwmon_dir / power_path.name.replace("_input", "_label")
                label = (
                    label_path.read_text(encoding="utf-8").strip().lower().replace(" ", "_")
                    if label_path.exists()
                    else power_path.stem
                )
                parts.append(f"{label}={_format_watts(microwatts / 1_000_000)}")

        if not parts:
            return {}
        return {"pwr_nvme": ",".join(parts)}

    @staticmethod
    def _parse_watts(value: str) -> float | None:
        try:
            return float(value.rstrip("W"))
        except ValueError:
            return None

    def collect(self) -> dict[str, str]:
        fields: dict[str, str] = {}
        if self._rapl:
            fields.update(self.collect_cpu())
        if self._nvidia:
            fields.update(self.collect_nvidia())
        if self._nvme:
            fields.update(self.collect_nvme())

        total_w = 0.0
        have_total = False

        cpu_val = fields.get("pwr_cpu", "-")
        cpu_tokens: dict[str, str] = {}
        if cpu_val != "-":
            cpu_tokens = dict(
                token.split("=", 1) for token in cpu_val.split(",") if "=" in token
            )
            cpu_for_total = cpu_tokens.get("package-0") or next(iter(cpu_tokens.values()), None)
            if cpu_for_total:
                watts = self._parse_watts(cpu_for_total)
                if watts is not None:
                    total_w += watts
                    have_total = True

        if "pwr_gpu_sum" in fields:
            watts = self._parse_watts(fields["pwr_gpu_sum"])
            if watts is not None:
                total_w += watts
                have_total = True
        elif fields.get("pwr_gpu", "-") != "-":
            for token in fields["pwr_gpu"].split(","):
                if "=" not in token:
                    continue
                watts = self._parse_watts(token.split("=", 1)[1])
                if watts is not None:
                    total_w += watts
                    have_total = True

        nvme_val = fields.get("pwr_nvme")
        if nvme_val:
            for token in nvme_val.split(","):
                if "=" not in token:
                    continue
                watts = self._parse_watts(token.split("=", 1)[1])
                if watts is not None:
                    total_w += watts
                    have_total = True

        if have_total:
            fields["pwr_sum"] = _format_watts(total_w)

        compact: list[str] = []
        if cpu_tokens:
            cpu_compact = cpu_tokens.get("package-0") or next(iter(cpu_tokens.values()), None)
            if cpu_compact:
                compact.append(f"cpu={cpu_compact}")
        if fields.get("pwr_gpu", "-") != "-":
            for token in fields["pwr_gpu"].split(","):
                if "=" in token:
                    compact.append(token.replace("=", ":"))
        if fields.get("pwr_sum"):
            compact.append(f"sum={fields['pwr_sum']}")

        fields["pwr"] = ",".join(compact) if compact else "-"
        return fields


def collect_power(tracker: PowerTracker | None = None) -> dict[str, str]:
    if tracker is None:
        tracker = PowerTracker()
    return tracker.collect()