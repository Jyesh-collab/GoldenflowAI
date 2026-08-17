#!/usr/bin/env bash
# Exposes the local Bagisto backend (bagisto-backend/docker-compose.yml, port 8080)
# to the internet via ngrok, so the Flutter app can reach it from a physical
# device or anywhere else — not just this Mac's Android emulator.
#
# Requires a free ngrok account + authtoken:
#   https://dashboard.ngrok.com/get-started/your-authtoken
# Set it once with:
#   ngrok config add-authtoken YOUR_TOKEN
# or by editing the placeholder in:
#   ~/Library/Application Support/ngrok/ngrok.yml

set -euo pipefail

CONFIG_FILE="$HOME/Library/Application Support/ngrok/ngrok.yml"

if grep -q "YOUR_NGROK_AUTHTOKEN_HERE" "$CONFIG_FILE" 2>/dev/null; then
  echo "❌ ngrok authtoken is still a placeholder."
  echo "   Get a free token at https://dashboard.ngrok.com/get-started/your-authtoken"
  echo "   then run: ngrok config add-authtoken YOUR_TOKEN"
  exit 1
fi

echo "Starting ngrok tunnel to localhost:8080 ..."
echo "Once it's up, copy the https://*.ngrok-free.app URL it prints and set:"
echo "  bagistoEndpoint = 'https://<that-url>/api/graphql'"
echo "in lib/core/constants/api_constants.dart"
echo ""

ngrok http 8080
