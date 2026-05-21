#!/usr/bin/env bash
# OTA-flash the reTerminal-E1003, retrying every 10s until the device is
# awake and accepts the upload. Run from the firmware/esphome directory.
#
# Usage: ./flash-ota.sh

set -u
cd "$(dirname "$0")"

device="${ESPHOME_OTA_HOST:-reterminal-e1003.local}"
yaml="reterminal-e1003.yaml"

attempt=0
until esphome run --device "$device" --no-logs "$yaml" 2>&1 | tail -3 | grep -q "OTA successful"; do
  attempt=$((attempt + 1))
  echo "Attempt $attempt failed at $(date +%H:%M:%S), retrying in 10s..."
  sleep 10
done
echo "OTA SUCCESS after $((attempt + 1)) attempt(s) at $(date +%H:%M:%S)"
