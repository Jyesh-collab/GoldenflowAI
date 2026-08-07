#!/usr/bin/env bash
# Builds the APK pointed at the local Docker Bagisto backend via
# 10.0.2.2 (the Android emulator's alias for this Mac's localhost).
#
# Only works on the Android emulator running on THIS machine, with
# bagisto-backend/ (docker compose up -d) running. Lowest latency for
# dev/testing here — no ngrok round trip.
#
# Usage: ./scripts/build_local.sh [debug|release]

set -euo pipefail
cd "$(dirname "$0")/.."

BUILD_MODE="${1:-debug}"

flutter build apk --"$BUILD_MODE" \
  --dart-define=BAGISTO_ENDPOINT=http://10.0.2.2:8080/api/graphql

OUT_DIR="build/app/outputs/flutter-apk"
SRC="$OUT_DIR/app-$BUILD_MODE.apk"
DEST="$OUT_DIR/app-$BUILD_MODE-local.apk"
cp "$SRC" "$DEST"

echo ""
echo "Built with LOCAL endpoint (http://10.0.2.2:8080) — emulator-on-this-Mac only."
echo "APK: $DEST"
