#!/usr/bin/env bash
# Usage:
#   git clone <repo-url> && cd step-nwd-web-viewer/hosting
#   cp .env.example .env
#   # Edit .env (set DOMAIN, etc.)
#   sudo ./install.sh
# Note: SSH key setup is done manually BEFORE running this script (see README).

set -euo pipefail

# Step 1: Pre-flight checks
echo "== Проверка окружения и переменных"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Ошибка: скрипт должен запускаться от root (sudo ./install.sh)" >&2
    exit 1
fi

cd "$(dirname "$0")"

if [[ ! -f ".env" ]]; then
    echo "Ошибка: файл .env не найден. Скопируйте .env.example в .env и заполните значения." >&2
    exit 1
fi

set -a
. ./.env
set +a

: "${DOMAIN:?Переменная DOMAIN не задана в .env}"

if [[ "$DOMAIN" == *example* ]]; then
    echo "Ошибка: DOMAIN не должен содержать 'example'. Укажите реальный домен." >&2
    exit 1
fi

# Step 2: Install packages
echo "== Установка пакетов"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends docker.io docker-compose-v2 ufw dnsutils ca-certificates curl
systemctl enable --now docker

# Step 3: Swap setup
echo "== Настройка swap"

if [[ -z "$(swapon --show --noheadings)" ]]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    if ! grep -q "^/swapfile" /etc/fstab; then
        echo "/swapfile none swap sw 0 0" >> /etc/fstab
    fi
fi

# Step 4: Firewall
echo "== Настройка файрвола (UFW)"

ufw allow 22/tcp > /dev/null
ufw allow 80/tcp > /dev/null
ufw allow 443/tcp > /dev/null
ufw --force enable

# Step 5: DNS check
echo "== Проверка DNS"

MYIP=$(curl -fsS https://api.ipify.org || true)
DNSIP=$(dig +short "$DOMAIN" A | tail -n1)

echo "IP сервера: ${MYIP:-не определено}"
echo "IP из DNS: ${DNSIP:-не найден}"

if [[ -z "$DNSIP" ]] || { [[ -n "$MYIP" ]] && [[ "$MYIP" != "$DNSIP" ]]; }; then
    echo "Ошибка: A-запись для $DOMAIN должна указывать на IP этого сервера ($MYIP)." >&2
    echo "Обновите DNS-записи и перезапустите скрипт." >&2
    exit 1
fi

# Step 6: Start services
echo "== Запуск контейнеров"

docker compose up -d --build
docker compose ps

# Step 7: Final message
echo "== Готово"
echo "Для создания первой ссылки-приглашения выполните:"
echo 'docker compose exec app viewerctl invite add "Название группы"'
