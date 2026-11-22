#!/usr/bin/env bash
# Démarrage Render : migrations Neon puis Daphne (HTTP + WebSocket, ASGI).
set -o errexit -o nounset -o pipefail
cd "$(dirname "$0")/.."

# Une seule instance : la migration au démarrage est sûre (URL Neon directe).
python manage.py migrate --noinput

# Le CLI Daphne ignore DAPHNE_WEBSOCKET_MAX_* (lus seulement par runserver) :
# on reprend la limite calculée dans settings (image base64 + marge).
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-moramora_api.settings}"
WS_MAX_SIZE="$(python -c 'import django; django.setup(); from django.conf import settings; print(settings.DAPHNE_WEBSOCKET_MAX_MESSAGE_SIZE)')"

# Un seul processus : le channel layer en mémoire et le verrou d'inférence
# ne sont pas partagés entre processus.
exec daphne \
  --bind 0.0.0.0 \
  --port "${PORT:-10000}" \
  --proxy-headers \
  --websocket-max-message-size "${WS_MAX_SIZE:-7000000}" \
  --websocket-max-frame-size "${WS_MAX_SIZE:-7000000}" \
  moramora_api.asgi:application
