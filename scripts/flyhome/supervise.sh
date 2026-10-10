#!/bin/bash
# campaign.py を落ちても自動で再起動しながら連続プレイする (リポジトリのルートで実行)。
#   bash scripts/flyhome/supervise.sh <表示用の最初のレベル番号> [最大レベル数] [秒/レベル]
# 進捗: run/campaign.log / run/campaign_results.txt / FLYHOME_STATE_FILE (既定は下の STATE)
export PYTHONPATH=src
first=${1:-1}; count=${2:-50}; per=${3:-900}
while true; do
  py scripts/flyhome/campaign.py "$first" "$count" "$per" >> run/campaign.log 2>&1
  tail -1 run/campaign.log | grep -q "^done" && break
  echo "[supervisor] restart" >> run/campaign.log
  sleep 3
done
