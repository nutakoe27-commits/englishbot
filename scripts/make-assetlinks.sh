#!/usr/bin/env bash
# Создать assetlinks.json — файл, которым Android проверяет, что приложение
# из магазина принадлежит нашему домену.
#
# Без него TWA открывается как вкладка браузера, С АДРЕСНОЙ СТРОКОЙ, и со
# стороны выглядит обёрткой над сайтом. Именно на этом нас завернули на
# модерации RuStore.
#
# ОТПЕЧАТКОВ МОЖЕТ БЫТЬ НЕСКОЛЬКО, и это важно. Если ключ подписи передан
# магазину (RuStore и Google Play делают это через PEPK), готовое
# приложение подписывает магазин своим сертификатом — с другим отпечатком.
# Тогда локального отпечатка мало: проверка домена не сойдётся, и адресная
# строка вернётся. Безопасно указать оба: локальный ключ загрузки и
# сертификат подписи из консоли магазина. Лишний отпечаток ничего не ломает,
# недостающий ломает всё.
#
# Где брать:
#   локальный:  keytool -list -v -keystore android.keystore -alias android
#   магазинный: консоль RuStore, раздел с подписью приложения
#
# Использование:
#   ./scripts/make-assetlinks.sh <отпечаток> [ещё отпечатки...]
#   PACKAGE=ru.example.app ./scripts/make-assetlinks.sh <отпечаток>
#
# После запуска — пересобрать miniapp и проверить, что файл отдаётся:
#   curl -s https://englishbot.krichigindocs.ru/.well-known/assetlinks.json

set -euo pipefail

PACKAGE="${PACKAGE:-ru.krichigindocs.englishbot.twa}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/miniapp/public/.well-known/assetlinks.json"

if [[ $# -eq 0 ]]; then
  cat >&2 <<'USAGE'
Укажи хотя бы один SHA-256 отпечаток подписи.

  локальный ключ:
    keytool -list -v -keystore ~/twa/android.keystore -alias android
  сертификат магазина (если ключ передан через PEPK):
    консоль RuStore → подпись приложения

  ./scripts/make-assetlinks.sh AA:BB:.. CC:DD:..
USAGE
  exit 1
fi

FPS=()
for raw in "$@"; do
  clean="$(printf '%s' "$raw" | tr -d ' ' | tr '[:lower:]' '[:upper:]')"
  if [[ ! "$clean" =~ ^([0-9A-F]{2}:){31}[0-9A-F]{2}$ ]]; then
    echo "Отпечаток не похож на SHA-256: нужно 32 байта через двоеточие." >&2
    echo "Получено: $clean" >&2
    exit 1
  fi
  FPS+=("$clean")
done

# Собираем JSON-массив отпечатков.
JOINED=""
for fp in "${FPS[@]}"; do
  [[ -n "$JOINED" ]] && JOINED+=",
        "
  JOINED+="\"$fp\""
done

mkdir -p "$(dirname "$OUT")"
cat > "$OUT" <<JSON
[
  {
    "relation": ["delegate_permission/common.handle_all_urls"],
    "target": {
      "namespace": "android_app",
      "package_name": "$PACKAGE",
      "sha256_cert_fingerprints": [
        $JOINED
      ]
    }
  }
]
JSON

echo "Записан $OUT"
echo "Пакет:      $PACKAGE"
echo "Отпечатков: ${#FPS[@]}"
for fp in "${FPS[@]}"; do echo "  $fp"; done
echo
echo "Дальше: docker compose build miniapp && docker compose up -d miniapp"
