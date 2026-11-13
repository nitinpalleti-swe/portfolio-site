#!/usr/bin/env bash
# One-time (and safe to re-run) setup for a fresh Ubuntu 24.04 Lightsail instance:
# automatic security updates, Docker Engine + compose plugin from Docker's official repo,
# the ubuntu user in the docker group, and 1 GB of swap. No secrets.
# Usage: sudo bash deploy/setup.sh   (then log out and back in for the docker group)
set -euo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"
export DEBIAN_FRONTEND=noninteractive

echo "== packages + automatic security updates"
apt-get update -q
apt-get install -y -q ca-certificates curl git unattended-upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF
systemctl enable --now unattended-upgrades

echo "== Docker Engine + compose plugin (download.docker.com)"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker ubuntu

echo "== 1 GB swap"
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

docker --version
docker compose version
swapon --show
echo "== done. Log out and back in so 'ubuntu' can run docker without sudo."
