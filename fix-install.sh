#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

install -d /etc/crashdog /var/log/crashdog /var/log/crashdog/forensics /var/lib/crashdog /usr/local/lib/crashdog
install -m 644 "${ROOT}/crashdog.default.yaml" /etc/crashdog/config.yaml.new
if [[ ! -f /etc/crashdog/config.yaml ]]; then
  install -m 644 "${ROOT}/crashdog.default.yaml" /etc/crashdog/config.yaml
else
  python3 - "${ROOT}/crashdog.default.yaml" /etc/crashdog/config.yaml <<'PY'
import sys
from pathlib import Path
import yaml

default = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
path = Path(sys.argv[2])
existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

def merge(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if key in dst and isinstance(dst[key], dict) and isinstance(value, dict):
            merge(dst[key], value)
        elif key not in dst:
            dst[key] = value

merge(existing, default)
path.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")
PY
  echo "Merged new config keys into /etc/crashdog/config.yaml"
fi

if [[ ! -f /etc/systemd/journald.conf.d/crashdog.conf ]]; then
  install -d /etc/systemd/journald.conf.d
  cat >/etc/systemd/journald.conf.d/crashdog.conf <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=1G
Compress=yes
EOF
  systemctl restart systemd-journald
fi
cp -r "${ROOT}/collectors" /usr/local/lib/crashdog/
install -m 644 "${ROOT}/crashdog.py" /usr/local/lib/crashdog/crashdog.py
install -m 644 "${ROOT}/status.py" /usr/local/lib/crashdog/status.py
install -m 644 "${ROOT}/banner.py" /usr/local/lib/crashdog/banner.py
install -m 644 "${ROOT}/README.md" /usr/local/lib/crashdog/README.md

cat >/usr/local/bin/crashdog <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="/usr/local/lib/crashdog${PYTHONPATH:+:${PYTHONPATH}}"
if [[ $# -eq 0 ]]; then
  exec python3 /usr/local/lib/crashdog/crashdog.py status
fi
exec python3 /usr/local/lib/crashdog/crashdog.py "$@"
EOF
chmod 755 /usr/local/bin/crashdog

install -m 644 "${ROOT}/crashdog.service" /etc/systemd/system/crashdog.service
systemctl daemon-reload
systemctl enable crashdog.service
systemctl restart crashdog.service

# Disable user install if present (only one CrashDog should run)
for user_home in /home/*; do
  user="$(basename "${user_home}")"
  id "${user}" >/dev/null 2>&1 || continue
  sudo -u "${user}" systemctl --user disable --now crashdog.service 2>/dev/null || true
  rm -f "${user_home}/bin/crashdog" \
        "${user_home}/.config/systemd/user/crashdog.service" \
        "${user_home}/.config/systemd/user/default.target.wants/crashdog.service" 2>/dev/null || true
  sudo -u "${user}" systemctl --user daemon-reload 2>/dev/null || true
done

echo "CrashDog system install fixed."
echo "  crashdog        # status report"
echo "  crashdog run    # daemon (used by systemd)"
systemctl --no-pager --full status crashdog.service