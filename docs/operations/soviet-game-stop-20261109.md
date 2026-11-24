# 配信基盤: ソ連ゲーム（sorengame）を試合終了後に停止する手順（2026-11-10）

## 背景

半熟英雄のコーナーが動いている裏で、**ソ連ゲーム（`soviet_local.mjs`）が動き続けて**いました。
プロセスを `kill` しても、配信基盤のスーパーバイザ（`/home/ubuntu/soren/start_all.sh --supervisor`）が
`soviet_watchdog.sh` 経由で**約1分で再起動**します。個別 kill では止まりません。

## 正規の停止手順（試合終了を待つ）

ソ連ゲームは「試合の境界（GAMEOVER）」を確認しないと停止できません。`game_lifecycle.py` を使います。

```bash
cd /home/ubuntu/soren
REQ=$(python3 -c "import uuid;print(uuid.uuid4())")
python3 lib/game_lifecycle.py request --request-id "$REQ" --game sorengame
# 試合が終わるまで status を確認（ack.snapshot.state == GAMEOVER / terminal: true）
python3 lib/game_lifecycle.py status
python3 lib/game_lifecycle.py boundary --request-id "$REQ"   # 境界を確認
python3 lib/game_lifecycle.py stop     --request-id "$REQ"   # 停止要求
python3 lib/game_lifecycle.py finish   --request-id "$REQ"   # 完了（ack=stopped / resource=stopped）
```

## 実測（2026-11-10 21:40〜21:44）

- 試合終了を検知（`state: GAMEOVER, terminal: true, runner_alive: false`）→ `boundary` → `stop` → `finish`。
- 結果: `ack: stopped` / `resource: stopped`、**`soviet_local.mjs` のプロセスは 0**。
- ウォッチドッグは残りますが、停止状態を尊重して**再起動しません**（60秒以上のアイドルを確認）。

## 注意

- `stop` は境界の acknowledge が必須です（試合中の `stop` は `waiting` のまま）。
- 期限（既定15分）を過ぎると失敗するため、長い試合では request を作り直します。
- 基盤のスーパーバイザ自体は再起動していません（オーバーレイ・音声・配信は継続）。
