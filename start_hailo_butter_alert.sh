
#!/usr/bin/env bash
set -euo pipefail

HEF_PATH="${HEF_PATH:-/home/gast/model.hef}"
SOURCE="${SOURCE:-auto}"
EV3_HOST="${EV3_HOST:-10.42.0.3}"
EV3_PORT="${EV3_PORT:-18812}"
BUTTER_THR="${BUTTER_THR:-0.75}"
SPEAK_TEXT="${SPEAK_TEXT:-butter found}"
LIFT_DOWN_SIGN="${LIFT_DOWN_SIGN:--1}"
LIFT_DOWN_MAX_ROT="${LIFT_DOWN_MAX_ROT:-2000.0}"
STALL_POS_DELTA="${STALL_POS_DELTA:-1.8}"
STALL_SPEED_THR="${STALL_SPEED_THR:-28.0}"
STALL_CONFIRM_CYCLES="${STALL_CONFIRM_CYCLES:-1}"
STALL_POLL_SEC="${STALL_POLL_SEC:-0.02}"
STALL_MIN_RUN_SEC="${STALL_MIN_RUN_SEC:-0.12}"
LIFT_STOP_PORT="${LIFT_STOP_PORT:-INPUT_1}"
LIFT_TOUCH_ACTIVE_STATE="${LIFT_TOUCH_ACTIVE_STATE:-pressed}"
LIFT_STOP_DEBOUNCE_MS="${LIFT_STOP_DEBOUNCE_MS:-20}"
LIFT_STOP_MAX_SEC="${LIFT_STOP_MAX_SEC:-12.0}"

# Keep camera runtime identical to the working webserver launcher.
LC_PREFIX="${LC_PREFIX:-/home/gast/.local/libcamera-rpi}"
if [[ -d "$LC_PREFIX/lib/aarch64-linux-gnu" ]]; then
  export LD_LIBRARY_PATH="$LC_PREFIX/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH:-}"
  export GST_PLUGIN_PATH="$LC_PREFIX/lib/aarch64-linux-gnu/gstreamer-1.0:${GST_PLUGIN_PATH:-}"
  export LIBCAMERA_IPA_MODULE_PATH="$LC_PREFIX/lib/aarch64-linux-gnu/libcamera/ipa"
  export LIBCAMERA_IPA_PROXY_PATH="$LC_PREFIX/libexec/libcamera"
fi

exec python3 /home/gast/hailo_butter_ev3_alert.py \
  --hef "$HEF_PATH" \
  --source "$SOURCE" \
  --butter-thr "$BUTTER_THR" \
  --lift-down-sign "$LIFT_DOWN_SIGN" \
  --lift-down-max-rotations "$LIFT_DOWN_MAX_ROT" \
  --lift-stop-sensor touch \
  --lift-stop-port "$LIFT_STOP_PORT" \
  --lift-touch-active-state "$LIFT_TOUCH_ACTIVE_STATE" \
  --lift-stop-debounce-ms "$LIFT_STOP_DEBOUNCE_MS" \
  --lift-stop-max-sec "$LIFT_STOP_MAX_SEC" \
  --stall-pos-delta "$STALL_POS_DELTA" \
  --stall-speed-thr "$STALL_SPEED_THR" \
  --stall-confirm-cycles "$STALL_CONFIRM_CYCLES" \
  --stall-poll-sec "$STALL_POLL_SEC" \
  --stall-min-run-sec "$STALL_MIN_RUN_SEC" \
  --lift-stop-required \
  --no-lift-software-fallback \
  --ev3-host "$EV3_HOST" \
  --ev3-port "$EV3_PORT" \
  --speak-text "$SPEAK_TEXT" \
  "$@"
