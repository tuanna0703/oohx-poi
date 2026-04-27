#!/usr/bin/env bash
# First-time bring-up on a fresh Ubuntu 22.04+ / Debian 12+ VPS.
#
# Usage (as root or with sudo):
#   curl -fsSL https://raw.githubusercontent.com/<your-org>/oohx-poi/main/scripts/bootstrap_vps.sh | sudo bash
# or, after cloning:
#   sudo ./scripts/bootstrap_vps.sh
#
# Idempotent — safe to re-run. Installs:
#   * docker engine + compose plugin
#   * ufw firewall (22, 80, 443 only)
#   * unattended security upgrades
#   * a dedicated ``poi`` user that owns /opt/poi-lake
#   * cron entry for the daily backup
#
# After this script finishes:
#   1. cp .env.production.example .env  &&  edit secrets
#   2. point DNS A records at this server
#   3. ./scripts/deploy.sh --first-run

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/oohx-matrix/oohx-poi.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/poi-lake}"
SERVICE_USER="${SERVICE_USER:-poi}"

# ---- preflight ------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    echo "must run as root (or via sudo)" >&2
    exit 1
fi

if ! grep -qE '^(Ubuntu|Debian)' /etc/os-release; then
    echo "WARN: tested only on Ubuntu/Debian; continuing anyway" >&2
fi

apt-get update -y
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release \
    ufw fail2ban unattended-upgrades \
    git cron jq

# ---- docker engine + compose plugin --------------------------------------

if ! command -v docker >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$(. /etc/os-release; echo "$ID")/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/$(. /etc/os-release; echo "$ID") \
        $(. /etc/os-release; echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io \
                       docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

# ---- service user --------------------------------------------------------

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
fi
usermod -aG docker "$SERVICE_USER"

# ---- repo checkout -------------------------------------------------------

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    git clone --depth=20 "$REPO_URL" "$INSTALL_DIR"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ---- firewall ------------------------------------------------------------

if ufw status | grep -q inactive; then
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp        comment 'ssh'
    ufw allow 80/tcp        comment 'http (caddy acme + redirect)'
    ufw allow 443/tcp       comment 'https'
    ufw allow 443/udp       comment 'http3'
    ufw --force enable
fi

# ---- unattended security upgrades ---------------------------------------

cat > /etc/apt/apt.conf.d/20auto-upgrades <<EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

# ---- backup cron (3 AM UTC daily) ---------------------------------------

CRON_LINE="0 3 * * * cd $INSTALL_DIR && ./scripts/backup_postgres.sh >> /var/log/poi-backup.log 2>&1"
( crontab -u "$SERVICE_USER" -l 2>/dev/null | grep -v 'backup_postgres.sh' ; echo "$CRON_LINE" ) \
    | crontab -u "$SERVICE_USER" -

touch /var/log/poi-backup.log
chown "$SERVICE_USER:$SERVICE_USER" /var/log/poi-backup.log

# ---- swap (2G) — small VPS often has none and embedding model loads OOM --

if ! swapon --show | grep -q '/swapfile'; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ---- docker daemon hardening ---------------------------------------------

cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" },
  "live-restore": true,
  "userland-proxy": false
}
EOF
systemctl restart docker

cat <<'EOM'

============================================================================
  bootstrap done

  next steps (as the ``poi`` user):
    sudo -iu poi
    cd /opt/poi-lake
    cp .env.production.example .env
    nano .env                    # fill in secrets, hostnames
    ./scripts/deploy.sh --first-run

  point DNS A records at this server:
    api.poi.oohx.net    → $(curl -s -4 ifconfig.me 2>/dev/null || echo "<server IP>")
    admin.poi.oohx.net  → same

============================================================================
EOM
