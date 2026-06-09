#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ATOP="${INSTALL_ATOP:-1}"
INSTALL_RASDAEMON="${INSTALL_RASDAEMON:-1}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

echo "Installing CrashDog dependencies..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-yaml

if [[ "${INSTALL_ATOP}" == "1" ]]; then
  echo "Installing atop..."
  DEBIAN_FRONTEND=noninteractive apt-get install -y atop
  systemctl enable --now atopacct atop-rotate
fi

if [[ "${INSTALL_RASDAEMON}" == "1" ]]; then
  echo "Installing rasdaemon..."
  DEBIAN_FRONTEND=noninteractive apt-get install -y rasdaemon
  systemctl enable --now rasdaemon
fi

install -d /etc/crashdog /var/log/crashdog /var/log/crashdog/forensics /var/lib/crashdog /usr/local/lib/crashdog
install -m 644 "${ROOT}/crashdog.default.yaml" /etc/crashdog/config.yaml
install -m 644 "${ROOT}/crashdog.default.yaml" /etc/crashdog/config.yaml.new

echo "Configuring persistent journald storage..."
install -d /etc/systemd/journald.conf.d
cat >/etc/systemd/journald.conf.d/crashdog.conf <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=1G
Compress=yes
EOF
systemctl restart systemd-journald
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
systemctl enable --now crashdog.service

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

echo "CrashDog installed."
echo "  Log dir:     /var/log/crashdog/"
echo "  Last snap:   /var/log/crashdog/last-snapshot.txt"
echo "  Service:     systemctl status crashdog"
echo "  Tail log:    tail -f /var/log/crashdog/crashdog-$(date +%Y%m%d).log"