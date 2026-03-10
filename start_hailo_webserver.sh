#!/usr/bin/env bash
set -euo pipefail

# Unified launcher: Web stream + robot control + config UI
exec /home/gast/start_hailo_robot_web.sh "$@"
