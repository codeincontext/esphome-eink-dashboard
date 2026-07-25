#!/usr/bin/env bash
# OTA-flash the reTerminal-E1003, retrying every 10s until the upload succeeds.
#
# Uses a pinned ESPHome in a local venv (see requirements.txt) rather than a
# global install: the display's official it8951 component / Seeed-reTerminal-E1003
# model preset needs ESPHome >= 2026.7, newer than many global installs. The venv
# is created on first run and reused thereafter.
#
# Usage: ./flash-ota.sh

set -eu
cd "$(dirname "$0")"

venv=".esphome-venv"
if [ ! -x "$venv/bin/esphome" ]; then
  echo "Bootstrapping pinned ESPHome venv ($venv) from requirements.txt..."
  python3 -m venv "$venv"
  "$venv/bin/pip" install --quiet --upgrade pip
  "$venv/bin/pip" install --quiet -r requirements.txt
fi
esphome="$venv/bin/esphome"

device="${ESPHOME_OTA_HOST:-reterminal-e1003.local}"
yaml="reterminal-e1003.yaml"

attempt=0
until "$esphome" run --device "$device" --no-logs "$yaml" 2>&1 | tail -3 | grep -q "OTA successful"; do
  attempt=$((attempt + 1))
  echo "Attempt $attempt failed at $(date +%H:%M:%S), retrying in 10s..."
  sleep 10
done
echo "OTA SUCCESS after $((attempt + 1)) attempt(s) at $(date +%H:%M:%S)"
