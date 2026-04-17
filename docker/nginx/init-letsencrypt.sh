#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# init-letsencrypt.sh
# Скрипт первичного получения SSL-сертификатов Let's Encrypt для всех поддоменов.
# Следует паттерну: https://github.com/wmnnd/nginx-certbot
#
# Запускать ОДИН РАЗ на новом сервере после того, как DNS-записи настроены
# и docker-compose.yml доступен:
#
#   bash docker/nginx/init-letsencrypt.sh
#
# Требования:
#   - Docker и Docker Compose установлены
#   - DNS A-записи для всех трёх поддоменов ведут на IP сервера
#   - В .env заполнены LETSENCRYPT_EMAIL и хосты доменов
# ─────────────────────────────────────────────────────────────────────────────

set -e

# ── Конфигурация ─────────────────────────────────────────────────────────────
source .env 2>/dev/null || true

EMAIL="${LETSENCRYPT_EMAIL:-nutakoe27@gmail.com}"
DOMAINS=(
    "${MINIAPP_HOST:-englishbot.krichigindocs.ru}"
    "${API_HOST:-api-english.krichigindocs.ru}"
    "${ADMIN_HOST:-admin-english.krichigindocs.ru}"
)
STAGING=0  # Изменить на 1 для тестирования (staging не засчитывается в лимиты LE)

RSA_KEY_SIZE=4096
DATA_PATH="./docker/certbot"

# ── Цвета для вывода ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Let's Encrypt — инициализация сертификатов ===${NC}"
echo "Email: $EMAIL"
echo "Домены: ${DOMAINS[*]}"
echo "Staging: $STAGING"
echo ""

# ── Загрузка рекомендуемых TLS-параметров от Certbot ────────────────────────
if [ ! -e "$DATA_PATH/conf/options-ssl-nginx.conf" ] \
   || [ ! -e "$DATA_PATH/conf/ssl-dhparams.pem" ]; then
    echo -e "${YELLOW}Загружаем рекомендуемые TLS-параметры...${NC}"
    mkdir -p "$DATA_PATH/conf"
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
        > "$DATA_PATH/conf/options-ssl-nginx.conf"
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem \
        > "$DATA_PATH/conf/ssl-dhparams.pem"
    echo -e "${GREEN}TLS-параметры загружены.${NC}"
fi

# ── Создаём временные самоподписанные сертификаты ────────────────────────────
# Это нужно чтобы nginx стартовал до получения настоящих сертификатов
for domain in "${DOMAINS[@]}"; do
    echo -e "${YELLOW}Создаём временный сертификат для $domain...${NC}"
    mkdir -p "$DATA_PATH/conf/live/$domain"
    docker compose run --rm --entrypoint \
        "openssl req -x509 -nodes -newkey rsa:$RSA_KEY_SIZE -days 1 \
         -keyout '/etc/letsencrypt/live/$domain/privkey.pem' \
         -out '/etc/letsencrypt/live/$domain/fullchain.pem' \
         -subj '/CN=localhost'" certbot
    echo -e "${GREEN}Временный сертификат для $domain создан.${NC}"
done

# ── Запускаем nginx ───────────────────────────────────────────────────────────
echo -e "${YELLOW}Запускаем nginx...${NC}"
docker compose up --force-recreate -d nginx
sleep 5

# ── Удаляем временные сертификаты ────────────────────────────────────────────
for domain in "${DOMAINS[@]}"; do
    echo -e "${YELLOW}Удаляем временный сертификат для $domain...${NC}"
    docker compose run --rm --entrypoint \
        "rm -Rf /etc/letsencrypt/live/$domain \
                /etc/letsencrypt/archive/$domain \
                /etc/letsencrypt/renewal/$domain.conf" certbot
done

# ── Получаем настоящие сертификаты ───────────────────────────────────────────
if [ "$STAGING" != "0" ]; then
    STAGING_FLAG="--staging"
    echo -e "${YELLOW}⚠ STAGING MODE: сертификаты не будут доверенными в браузерах${NC}"
else
    STAGING_FLAG=""
fi

for domain in "${DOMAINS[@]}"; do
    echo -e "${YELLOW}Получаем сертификат для $domain...${NC}"
    docker compose run --rm --entrypoint \
        "certbot certonly --webroot \
         -w /var/www/certbot \
         $STAGING_FLAG \
         --email $EMAIL \
         --agree-tos \
         --no-eff-email \
         --rsa-key-size $RSA_KEY_SIZE \
         -d $domain" certbot
    echo -e "${GREEN}Сертификат для $domain получен!${NC}"
done

# ── Перезапускаем nginx с настоящими сертификатами ───────────────────────────
echo -e "${YELLOW}Перезапускаем nginx с настоящими сертификатами...${NC}"
docker compose exec nginx nginx -s reload

echo ""
echo -e "${GREEN}=== Готово! Все сертификаты получены. ===${NC}"
echo "Автопродление работает через сервис certbot (каждые 12 часов)."
echo ""
echo -e "${YELLOW}Следующий шаг:${NC}"
echo "  docker compose up -d"
