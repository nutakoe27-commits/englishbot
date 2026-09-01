#!/usr/bin/env bash
# Создать assetlinks.json — файл, которым Android проверяет, что приложение
# из магазина принадлежит нашему домену.
#
# Без него TWA открывается как вкладка браузера, С АДРЕСНОЙ СТРОКОЙ, и со
# стороны выглядит обёрткой над сайтом. Именно на этом нас завернули на
# модерации RuStore.
#
# Отпечаток берётся из keystore, которым подписана сборка:
#   cd ~/twa && npx @bubblewrap/cli fingerprint list
#   # или: keytool -list -v -keystore android.keystore -alias android | grep SHA256
#
# Использование:
#   ./scripts/make-assetlinks.sh AA:BB:CC:...:99
#   ./scripts/make-assetlinks.sh AA:BB:...  ru.krichigindocs.englishbot
#
# После запуска — пересобрать miniapp и проверить, что файл отдаётся:
#   curl -s https://englishbot.krichigindocs.ru/.well-known/assetlinks.json

set -euo pipefail

FINGERPRINT="${1:-}"
PACKAGE="${2:-ru.krichigindocs.englishbot}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/miniapp/public/.well-known/assetlinks.json"

if [[ -z "$FINGERPRINT" ]]; then
  echo "Укажи SHA-256 отпечаток подписи первым аргументом." >&2
  echo "Взять его: cd ~/twa && npx @bubblewrap/cli fingerprint list" >&2
  exit 1
fi

# Приводим к виду AA:BB:..., как ждёт Android: заглавные, через двоеточие.
CLEAN="$(printf '%s' "$FINGERPRINT" | tr -d ' ' | tr '[:lower:]' '[:upper:]')"
if [[ ! "$CLEAN" =~ ^([0-9A-F]{2}:){31}[0-9A-F]{2}$ ]]; then
  echo "Отпечаток не похож на SHA-256: нужно 32 байта через двоеточие." >&2
  echo "Получено: $CLEAN" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT")"
cat > "$OUT" <<JSON
[
  {
    "relation": ["delegate_permission/common.handle_all_urls"],
    "target": {
      "namespace": "android_app",
      "package_name": "$PACKAGE",
      "sha256_cert_fingerprints": ["$CLEAN"]
    }
  }
]
JSON

echo "Записан $OUT"
echo "Пакет:     $PACKAGE"
echo "Отпечаток: $CLEAN"
echo
echo "Дальше: docker compose build miniapp && docker compose up -d miniapp"
