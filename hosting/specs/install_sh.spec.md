Write a complete bash script `install.sh` for a clean Ubuntu 24.04 server (no markdown fences, no explanations, output ONLY the script, starting with `#!/usr/bin/env bash`). Comments and messages are in Russian. Use `set -euo pipefail`.

The script is run by root from the directory `hosting/` of a git clone (it contains `.env`, `docker-compose.yml`). Steps, each announced with an `echo "== <step>"` line:
1. Refuse with a Russian message (stderr, exit 1) if `id -u` is not 0; `cd "$(dirname "$0")"`; refuse if `.env` is missing (message: copy `.env.example` and fill it); load `.env` with `set -a; . ./.env; set +a`; require non-empty `DOMAIN` (`${DOMAIN:?...}`); refuse if `DOMAIN` contains the substring `example`.
2. Packages (`DEBIAN_FRONTEND=noninteractive`): `apt-get update -y` then `apt-get install -y --no-install-recommends docker.io docker-compose-v2 ufw dnsutils ca-certificates curl`; `systemctl enable --now docker`.
3. Swap: if `swapon --show --noheadings` prints nothing: create a 2 GB `/swapfile` (`fallocate -l 2G`, `chmod 600`, `mkswap`, `swapon`) and append `/swapfile none swap sw 0 0` to `/etc/fstab` unless a line starting with `/swapfile` already exists.
4. Firewall: `ufw allow 22/tcp`, `80/tcp`, `443/tcp` (output to /dev/null) and `ufw --force enable`. The script MUST NOT touch any SSH daemon configuration (no sshd_config edits, no restart of ssh).
5. DNS check: `MYIP=$(curl -fsS https://api.ipify.org || true)`, `DNSIP=$(dig +short "$DOMAIN" A | tail -n1)`; print both; if `DNSIP` is empty, or MYIP is non-empty and differs from DNSIP: print to stderr that the A record must point to this server and the script must be re-run after DNS updates, exit 1 (so Let's Encrypt is not hit before DNS is ready).
6. `docker compose up -d --build` then `docker compose ps`.
7. Final message: how to create the first invite link: `docker compose exec app viewerctl invite add "Название группы"`.
Header comment (3-4 lines) shows the usage: `git clone ... && cd step-nwd-web-viewer/hosting`, `cp .env.example .env`, edit, `sudo ./install.sh`, and states that SSH key setup is done manually BEFORE running (see README).
