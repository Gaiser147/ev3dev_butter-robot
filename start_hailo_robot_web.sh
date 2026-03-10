#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
CONFIG_PATH="${CONFIG_PATH:-/home/gast/.config/hailo_robot_web/config.json}"
ROBOT_DRY_RUN="${ROBOT_DRY_RUN:-0}"
EV3_HOST="${EV3_HOST:-10.42.0.3}"
EV3_PORT="${EV3_PORT:-18812}"
HEF_PATH="${HEF_PATH:-/home/gast/model.hef}"
SOURCE="${SOURCE:-auto}"
export ROBOT_DRY_RUN EV3_HOST EV3_PORT HEF_PATH SOURCE

# Prefer locally built Raspberry Pi libcamera (if present).
LC_PREFIX="${LC_PREFIX:-/home/gast/.local/libcamera-rpi}"
if [[ -d "$LC_PREFIX/lib/aarch64-linux-gnu" ]]; then
  export LD_LIBRARY_PATH="$LC_PREFIX/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH:-}"
  export GST_PLUGIN_PATH="$LC_PREFIX/lib/aarch64-linux-gnu/gstreamer-1.0:${GST_PLUGIN_PATH:-}"
  export LIBCAMERA_IPA_MODULE_PATH="$LC_PREFIX/lib/aarch64-linux-gnu/libcamera/ipa"
  export LIBCAMERA_IPA_PROXY_PATH="$LC_PREFIX/libexec/libcamera"
fi

exec python3 /home/gast/hailo_robot_web_control.py \
  --host "$HOST" \
  --port "$PORT" \
  --config "$CONFIG_PATH" \
  "$@"
