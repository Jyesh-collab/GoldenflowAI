#!/usr/bin/env bash
# Builds the APK pointed at the backend's public ngrok URL, so it works
# from a physical device, a different network, or anywhere else — not
# just this Mac's emulator.
#
# IMPORTANT: free ngrok URLs change every time the tunnel restarts
# (bagisto-backend/start-ngrok.sh). Update NGROK_URL below to match
# whatever ngrok is currently printing before running this.
#
# Usage: ./scripts/build_remote.sh [debug|release]

set -euo pipefail
cd "$(dirname "$0")/.."

NGROK_URL="https://fragility-veal-kisser.ngrok-free.dev"
BUILD_MODE="${1:-debug}"

if [[ "$NGROK_URL" == *"YOUR_NGROK"* ]]; then
  echo "❌ Set NGROK_URL in this script to your current ngrok forwarding URL first."
  echo "   Get it from: cd bagisto-backend && ./start-ngrok.sh"
  exit 1
fi

flutter build apk --"$BUILD_MODE" \
  --dart-define=BAGISTO_ENDPOINT="$NGROK_URL/api/graphql"

OUT_DIR="build/app/outputs/flutter-apk"
SRC="$OUT_DIR/app-$BUILD_MODE.apk"
DEST="$OUT_DIR/app-$BUILD_MODE-remote.apk"
cp "$SRC" "$DEST"

echo ""
echo "Built with REMOTE endpoint ($NGROK_URL) — reachable from anywhere the tunnel is up."
echo "APK: $DEST"
