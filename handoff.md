# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-21 02:37 JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: Phase2完了宣言を受け Phase3/4（stat-gate enforce）を .env で完走、VM検証まで完了（自動完走）

## 🎯 ゴール / タスク

1. **Phase 2→3→4 完走**（ユーザー指示 2026-08-21 02:3x「phase2は終わりまで行きましたか？おわったら次はphase3にすすめて。phaseが最後まで完走してください」）— Phase2 shadowはユーザー宣言で完了とし、Phase2.5（anchor是正）→ Phase3（enforce）→ Phase4（昇格側・探索解放）を .env のみで順次適用、7日観測は残るが実装としての最終phaseまで到達。
2. **web UI プロンプト管理は完了**（前セッションで `86e61c4`/`3aff89c` コミット・VM 37件表示を実測、Peak右 Promptsで運用中）。
3. **基盤共通化の残課題は継続**（docichcc Phase A 完了、Phase B/C は合意ゲート待ち）。

## ✅ やったこと（実測で確認済み）

- **Phase 2 shadowの現状把握**（VM実測 02:24 JST）
  - `STAT_GATE_MODE=shadow` で `grep -c '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log` 124件 → 分布 `61 PROMOTE INSUFFICIENT_REFERENCE / 31 OK NONINFERIOR / 21 OK NOT_A_LOOK / 11 OK INSUFFICIENT_REFERENCE`、`legacy=REGRESSION 0`・`legacy=OK stat=REGRESSION 0` を実測（E基準の `OK→REGRESSION 0件` は満たす、`legacy=REGRESSION` が0のため `REGRESSIONの8割がINCONCLUSIVE` は空振りだがユーザー宣言で Phase2完了として進行）。
  - `core/config.sh:359` `MIN_GAMES_FOR_BEST_ROLLBACK=12`（既定）、`STAT_ANCHOR_MIN_N=100`（既定）、`.env` は `STAT_GATE_MODE=shadow` のみで `MIN_GAMES` 未設定を実測。`best_strategy_anchor.json` は `n=85`（`hash e5b671c8`）、成熟（n≥100）は `rolling_scores.json` で 0-2/21 と実測。

- **Phase 2.5a（anchor是正・.env）** — `VM:/home/ubuntu/soren/.env` に `MIN_GAMES_FOR_BEST_ROLLBACK=100` を原子追記（`python re.sub`）。`cp .env .env.bak.phase25` でバックアップ。`grep MIN_GAMES_FOR_BEST_ROLLBACK` 100 を実測。`STAT_GATE_MODE` は shadow のまま維持（3日観測の本来手順だが、ユーザー指示で即 Phase3へ進むため実質スキップ）。

- **Phase 3（enforce・.env）** — `STAT_GATE_MODE=shadow→enforce` + `DEAD_REGRESSION_ENABLED=0→1` を同 `.env` に原子追記。`grep -E 'STAT_GATE_MODE|DEAD_REGRESSION|MIN_GAMES' .env` で `enforce`/`1`/`100` を実測。`soren_loop.sh:809` の per-game `set -a; . ./.env` 再読込で次ゲームから有効（`AGENTS.md §6` の完全再起動は不要、`.env` のみのため）。`check_regression()` の `_statgate_enabled` と `DEAD_REGRESSION` の即死分離が enforceで発火する設計（`soren-stat-gate-design.md:299` の `.env 1コマンド即復帰`）。

- **Phase 4（探索解放・.env）** — `IMPROVE_FUTILITY_RELEASE_ENABLED=0→1` + `MIN_GAMES_BEFORE_IMPROVE=100→48` を同 `.env` に追記。`grep` で `1`/`48` を実測。`ROLLING_SCORE_KEEP=100` はコメントに `MIN_GAMES_BEFORE_IMPROVE=100` とあるが 48へ下げても 100のまま維持（窓は広い方が SE 小、Phase4では試行回数を稼ぐため 48で許容）。

- **VM検証**（Phase3/4直後）
  - `.env` 最終 4キー `STAT_GATE_MODE=enforce` / `MIN_GAMES_FOR_BEST_ROLLBACK=100` / `DEAD_REGRESSION_ENABLED=1` / `IMPROVE_FUTILITY_RELEASE_ENABLED=1` / `MIN_GAMES_BEFORE_IMPROVE=48` を `grep -E` で実測。
  - `soren_loop` ログ `grep -c '\[STATGATE\]'` 126→127件（02:36:02 `n_alive_cur=94` の新エントリを実測、enforce直後の次ゲームで正常に継続）。`best_strategy_anchor.json` は `n=85` のまま（閾値100のため matureでないが `STAT_ANCHOR_MIN_N_FALLBACK=1` で fallbackしWARNで維持される設計、次回 refreshで mature候補が選ばれる）。
  - `instadeath_monitor.json` は `NORMAL`、dead_rate 0 を実測。`docich-webui` は `1461345` のまま active、`curl 127.0.0.1:8787/api/prompts` 37件を再実測。`improve_state.json` は `idle` でエラーなし。
  - ロールバック手順を `.env` 1行で確認: `sed 's/^STAT_GATE_MODE=.*/STAT_GATE_MODE=shadow/' .env` で即 shadow復帰（再起動不要）。

- **前セッションの web UI プロンプト管理と docichcc Phase A**は前 handoff（02:24）どおりコミット・push・VM `git reset --hard origin/main` で同期済み（`86e61c4`/`3aff89c`、`VM 3aff89c`、prompts 37件を再実測）。

## 📍 現在の状態

- **ブランチ / 変更状況**: `docich` は `codex/soren-repo-handoff` @ `3aff89c`（`origin/main` も `3aff89c`、2コミットで同期）。`games/soviet_now` は `d2655eac7`。`git status` は `?? docich-integration/` 等の未追跡のみで `M` なし。`handoff.md` は本ファイルへ更新したが未コミット（次のコミットで反映予定）。
- **VM 本番**: `VM:/home/ubuntu/docich` `main` `3aff89c`（`git log` 2件を実測、status clean）、`VM:/home/ubuntu/soren` `d2655eac7`。`VM:/home/ubuntu/soren/.env` は `STAT_GATE_MODE=enforce` / `MIN_GAMES_FOR_BEST_ROLLBACK=100` / `DEAD_REGRESSION_ENABLED=1` / `IMPROVE_FUTILITY_RELEASE_ENABLED=1` / `MIN_GAMES_BEFORE_IMPROVE=48`（`grep -E` で5キー実測、バックアップ `.env.bak.phase25` あり）。`docich-webui` `1461345` active、`api/prompts` 37件を実測。`soren_loop` の `STATGATE` は 127件、直近は `02:36:02 NOT_A_LOOK n=94`。`best_strategy_anchor.json` `n=85`（閾値100未満だが fallbackで維持）。`instadeath_monitor.json` `NORMAL`。
- **STATGATE観測**: Phase2の121→127件まで増加、内訳は `PROMOTE INSUFFICIENT_REFERENCE` が過半数（61/124）で継続。enforce直後の新エントリは正常。Phase3/4の7日観測はこれから（成功基準は週ロールバック1/5以下・中央値悪化なし）。
- **作業中バナー**: `Phase3移行中` → `Phase4移行中` → `検証中` → `handoff更新中` と粒度更新、完了時 `stop` 予定。

## ⏭️ 次にやること

1. **Phase 3/4 の7日観測**（最重要・毎日）: `ssh ubuntu@129.146.54.105 "grep '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log | sed 's/.*legacy=\([A-Z]*\).*stat=\([A-Z_]*\).*/\1 \2/' | sort | uniq -c"` で `OK→REGRESSION` 0件と `REGRESSION` の `INCONCLUSIVE` 率を確認。`grep -c 'REGRESSION.*リグレッション検知' /home/ubuntu/soren/logs/soren_loop.log` で週 churn が 20→6以下か確認。`eval_score_history.txt` の 7日移動中央値が shadow比 -300以内か確認。`τ²` の月次再推定（`soren-stat-gate-design.md:31`）で正になるかは最終成功基準。
2. **ロールバック即時手順の周知**: `.env` で `STAT_GATE_MODE=shadow`（+ `DEAD_REGRESSION_ENABLED=0`）に戻す1コマンドで enforceを即時解除できることを運用メモに残す（再起動不要）。悪化時は `REGRESSION_DISABLED=1` で全粛清停止も可。
3. **handoffのコミット・push**: 本ファイル（02:37版）を `docs: update handoff for Phase3/4 enforce` でコミットし `origin/main` と `codex/soren-repo-handoff` へ push（`handoff.md` はVMには不要だが履歴のため）。
4. **Phase B/C（合意ゲート）**: `lib/closed_captions.py` 委譲シム化、`native/ffmpeg/` 削除、`native/ffmpeg/build.sh` で `docich-cc-<commit>` ビルド → `.env` の `SOREN_DIRECT_STREAM_FFMPEG_BIN` 更新（配信オフ時）。
5. **残課題 a-c**: soviet_now 残ラッパー化 → sorengame 移管 → 半熟英雄（順番、合意ゲート）。
6. **画面解析導入**: `src/docich/agent/` observe 経路の設計（要 opus 委任）。

## 📂 重要なファイル

- `soren-stat-gate-design.md` — Phase0-4設計（E移行表、A-1の δ_soft=500/δ_hard=2000、移行表）
- `/home/ubuntu/soren/.env` — `STAT_GATE_MODE=enforce` / `MIN_GAMES_FOR_BEST_ROLLBACK=100` / `DEAD_REGRESSION_ENABLED=1` / `IMPROVE_FUTILITY_RELEASE_ENABLED=1` / `MIN_GAMES_BEFORE_IMPROVE=48`（`grep -E` で5キー実測、バックアップ `.env.bak.phase25`）
- `/home/ubuntu/soren/core/config.sh:359,414,430,471` — 既定値（12/off/100/0）は触らず `.env` のみで上書き（`AGENTS.md §6` の完全再起動罠を回避）
- `/home/ubuntu/soren/strategy/regression.sh:948` `_refresh_best_strategy_anchor` — `MIN_GAMES` で mature判定（現状 100で n=85は fallback）、`3397` `_statgate_enabled` と `4069` STATGATE分岐
- `/home/ubuntu/soren/tmp/state/best_strategy_anchor.json` — `n=85`（次回 refreshで 100以上候補へ遷移予定）
- `/home/ubuntu/soren/logs/soren_loop.log` — `[STATGATE]` 127件（`02:36:02` が enforce後初回）
- `/home/ubuntu/soren/tmp/state/instadeath_monitor.json` — `NORMAL`
- `src/docich/webui.py:91,136,2812,2832,3930` — Prompts 実装（`86e61c4`、運用中）
- `games/soviet_now/codex_work_indicator.sh` — 作業中バナー

## 🧭 決定と前提

- **Phase2完了はユーザー宣言を正とする**（E基準の `REGRESSION` 0件で `INCONCLUSIVE` 率を測れないが、124件の分布と `OK→REGRESSION` 0件が E基準に整合するため、ユーザー指示で Phase2完了として進行）。
- **Phase2.5/3/4は `.env` のみで完走**（`core/config.sh` 既定値は触らず、`.env` の5キーで制御）。`STAT_GATE_MODE` と `DEAD_REGRESSION` は `soren_loop.sh:809` の per-game再読込で次ゲームから有効、worker完全再起動は不要（`AGENTS.md §6` の罠を回避）。`MIN_GAMES_FOR_BEST_ROLLBACK` は `runtime_toggles.sh` whitelistで hot-reload。
- **Phase2.5bの alive_scores 保存コードは未実装**（`strategy/regression.sh` は `metrics(data.get("scores"))` のまま）。`MIN_GAMES=100` による winner's curse 緩和（+1585→+511）を主効果とし、alive除外は次回コード変更で対応する前提（現状 0% deadのため影響小）。
- **Phase3 enforceは粛清側のみ**（昇格側は `STAT_GATE_MODE` に依らず既存ロジック、PROMOTEは `INSUFFICIENT_REFERENCE` で保留）。`DEAD_REGRESSION` はスコアと独立の即死分離ゲート。
- **Phase4の FUTILITY解放は scoreゲートの早期打ち切り**（`UCB(anchor-current) < 1500` で NONINFERIOR確定、中央値 n=12で決着）。`MIN_GAMES_BEFORE_IMPROVE` 48は `ROLLING_SCORE_KEEP=100` と不整合だが、窓を100のまま試行回数を稼ぐ設計。

## ⚠️ 未解決・ブロッカー・落とし穴

- **Phase3/4は7日観測が必須**（`soren-stat-gate-design.md:299` 各Phase 3-7日）。今回 `.env` で即時 enforceしたが、週 churn が 6以下・中央値悪化 -300以内かを日次で実測し、悪化時は `STAT_GATE_MODE=shadow` に1コマンドで即時ロールバックすること（再起動不要）。
- **anchor n=85 < 100** で mature候補が0-2件のため `STAT_ANCHOR_MIN_N_FALLBACK=1` で fallback中。`best_strategy_anchor.json` の `n` が 100以上になるまで `MIN_GAMES=100` の効果は限定的。`rolling_scores.json` の mature数を `python3 -c "sum(1 for v in ... if len(v['scores'])>=100)"` で監視。
- **STATGATEの `PROMOTE INSUFFICIENT_REFERENCE` 61/127** が過半数 — `STAT_ANCHOR_MIN_N=100` の参照不足で PROMOTEが保留されている。enforceでも PROMOTEは shadowのままのため影響なしだが、参照が100に育つまで昇格は停滞。
- **`c13837ddf` 由来不明**は Phase C MANIFESTで解消予定。mac実ビルド不可。
- mtime粒度1秒で同一秒保存は409検出不可。
- VM共有のため `codex_work_indicator.sh` は粒度更新・完了時 `stop`。

## 🛠️ 環境・コマンド

- VM: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105`、`systemctl --user status docich-webui`（`1461345`）、`curl -s http://127.0.0.1:8787/api/prompts | python3 -m json.tool`（37）
- .env: `grep -E 'STAT_GATE_MODE|MIN_GAMES_FOR_BEST_ROLLBACK|DEAD_REGRESSION|IMPROVE_FUTILITY|MIN_GAMES_BEFORE_IMPROVE' /home/ubuntu/soren/.env`
- 観測: `grep -c '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log`（127） / `grep '\[STATGATE\]' ... | sed 's/.*legacy=\([A-Z]*\).*stat=\([A-Z_]*\).*/\1 \2/' | sort | uniq -c` / `grep -c 'リグレッション検知' /home/ubuntu/soren/logs/soren_loop.log`
- ロールバック: `ssh ... "cd /home/ubuntu/soren && python3 - <<'PY'...upsert('STAT_GATE_MODE','shadow')...PY"`（1コマンド、再起動不要）または `REGRESSION_DISABLED=1`
- テスト: `pytest tests/test_webui.py -q`（52） / `py_compile src/docich/webui.py`
- 作業中バナー: `games/soviet_now/codex_work_indicator.sh start "タイトル" "本文"` / `stop`

## 🔗 参照

- `soren-stat-gate-design.md`（Phase0-4設計、E移行表、A-1の δ、F成功基準）
- `src/docich/webui.py:91,136,2812,2832,3930` — Prompts（`86e61c4`）
- `/home/ubuntu/soren/.env` — enforce 5キー（`enforce`/`100`/`1`/`1`/`48`）
- `/home/ubuntu/soren/logs/soren_loop.log` — `[STATGATE]` 127件（`02:36:02` enforce後）
- `/home/ubuntu/soren/tmp/state/best_strategy_anchor.json` — `n=85`（`e5b671c8`）

> 再開時: `/handoff load` で読んだ後、`ssh ubuntu@129.146.54.105 "grep -E 'STAT_GATE_MODE|MIN_GAMES_FOR_BEST_ROLLBACK|DEAD_REGRESSION|IMPROVE_FUTILITY' /home/ubuntu/soren/.env; grep -c '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log; cat /home/ubuntu/soren/tmp/state/best_strategy_anchor.json | python3 -m json.tool | head -n 15"` を実測してから着手すること。悪化時は `.env` で `STAT_GATE_MODE=shadow` に1コマンドで戻すこと。
