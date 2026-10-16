#!/usr/bin/env bash
# 毎日の自動実行用。cron 例（毎朝7時）:
#   0 7 * * * /path/to/hp-autosales/scripts/daily.sh
# 送信は行わない。届いた下書きを確認して approve → send --live してください。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
python3 -m hpsales auto >> "logs/$(date +%Y-%m-%d).log" 2>&1
