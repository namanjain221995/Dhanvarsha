#!/usr/bin/env sh
# One-time preparation of a fresh server (AWS EC2: Ubuntu 24.04 LTS or Amazon Linux 2023):
# Docker Engine with the Compose and Buildx plugins, git, container log rotation, and docker
# access for the user who ran sudo.
#
#   sudo scripts/server-setup.sh
#
# Safe to run again. Then log out and back in, and continue with scripts/deploy.sh.
set -eu
[ "$(id -u)" = 0 ] || { echo "Run it with sudo: sudo scripts/server-setup.sh" >&2; exit 1; }
. /etc/os-release

install_plugin() { # name, url, sha256
  dir=/usr/local/lib/docker/cli-plugins
  mkdir -p "$dir"
  curl -fsSL "$2" -o "$dir/$1.tmp"
  echo "$3  $dir/$1.tmp" | sha256sum -c - >/dev/null || { rm -f "$dir/$1.tmp"; echo "checksum mismatch for $1" >&2; exit 1; }
  chmod 0755 "$dir/$1.tmp"
  mv "$dir/$1.tmp" "$dir/$1"
}

case "${ID:-}" in
  ubuntu)
    echo "== installing Docker from Docker's apt repository"
    apt-get update -q
    apt-get install -y -q ca-certificates curl git python3
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ;;
  amzn)
    echo "== installing Docker from the Amazon Linux repository"
    dnf install -y -q docker git python3 tar
    # Amazon Linux does not package the Compose and Buildx plugins: install Docker's own
    # release binaries, checked against the checksums published with each release.
    case "$(uname -m)" in
      x86_64) c_arch=x86_64; b_arch=amd64 ;;
      aarch64) c_arch=aarch64; b_arch=arm64 ;;
      *) echo "unsupported CPU $(uname -m)" >&2; exit 1 ;;
    esac
    if ! docker compose version >/dev/null 2>&1; then
      base=https://github.com/docker/compose/releases/latest/download
      sum="$(curl -fsSL "$base/docker-compose-linux-$c_arch.sha256" | awk '{print $1}')"
      install_plugin docker-compose "$base/docker-compose-linux-$c_arch" "$sum"
    fi
    if ! docker buildx version >/dev/null 2>&1; then
      tag="$(curl -fsSL https://api.github.com/repos/docker/buildx/releases/latest | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')"
      base="https://github.com/docker/buildx/releases/download/$tag"
      file="buildx-$tag.linux-$b_arch"
      sum="$(curl -fsSL "$base/checksums.txt" | awk -v f="*$file" '$2 == f || $2 == substr(f, 2) {print $1}')"
      [ -n "$sum" ] || { echo "no checksum published for $file" >&2; exit 1; }
      install_plugin docker-buildx "$base/$file" "$sum"
    fi
    ;;
  *)
    echo "This script supports Ubuntu and Amazon Linux (found ${ID:-unknown})." >&2
    exit 1
    ;;
esac

echo "== container log rotation"
# A million-request load test writes about 300 MB of API logs; cap every container's logs.
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
  printf '{\n  "log-driver": "json-file",\n  "log-opts": { "max-size": "50m", "max-file": "3" }\n}\n' \
    > /etc/docker/daemon.json
else
  echo "/etc/docker/daemon.json already exists; left unchanged"
fi
systemctl enable docker >/dev/null 2>&1
systemctl restart docker

if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
  usermod -aG docker "$SUDO_USER"
  echo "== $SUDO_USER may now use docker (log out and back in first)"
fi
docker --version
docker compose version
docker buildx version
echo "Done. Next: log out and back in, then follow docs/deploy-ec2.md (scripts/deploy.sh)."
