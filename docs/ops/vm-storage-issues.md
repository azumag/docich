# 本番VM ストレージ増加 対応 Issue 案

2026-09-14 の read-only 調査（`docs/ops/vm-storage-reclaim-runbook.md` 参照）に基づく起票候補。
起票時は各本文をそのまま Issue 本文として使用してよい。数値は調査時点の実測値。

---

## Issue A: opencode.db の増加対策（最優先・本質的な unbounded）

**背景**
- `/home/ubuntu/.local/share/opencode/opencode.db` が 2,489,364,480 B（実ページ 2.32 GiB）。
- SQLite メタデータ実測: page_size=4096, page_count=607,882, **freelist_count=0**。
- 内訳: event 1,610,756行 / part 736,664行 / message 32,030行 / session 11,120行。
- mtime は調査時刻（2026-09-14 21:29）で live 書き込み中。prune/保持期間/ローテーション処理が存在しない。
- 加えて `.config/opencode` 0.14 GiB、`opencode/log` 60 MB、`opencode/tool-output` 105 MB、worker 用 `soren/tmp/state/xdg_data/opencode/opencode.db` 111.9 MB。

**なぜ増え続けるか**
- AI生成・エージェント作業・改善ループが opencode CLI を呼ぶたびに session/event/part を追記。
- freelist=0 のため VACUUM では回収不能。cleanup 用 cron/systemd timer が無い。

**やること**
1. 保持期間（例: 90日）での古い session/event/part の削除ジョブ、または DB ローテーション設計。
2. `-wal`/`-shm` を含む安全な運用（live DB を止める停止窓の要否を判断）。
3. read-only で削減前後のサイズ・行数を実測して記録。

**受入基準**
- 1ヶ月運用後も DB サイズが頭打ち（または設定上限内）であること。削減前後の実測が残っていること。

**セキュリティ/安全**
- prompt・AI入出力の内容は取得・表示しない（メタデータ/行数のみ）。

---

## Issue B: VOICEVOX 導入アーカイブの要否確認

**背景**
- `/opt/voicevox/voicevox.7z.001` = 1,810,684,257 B（1.69 GiB）。
- `/opt/voicevox/linux-cpu-arm64` に展開済みエンジン（2.1 GiB）が存在し `current` シンボリックリンクが参照。

**やること**
- 再取得経路（配布URL or 手元バックアップ）を確認し、退避または削除。
- soren 側コードから本アーカイブが参照されていないことを `grep` で確認。

**受入基準**
- 退避/削除後も VOICEVOX/AivisSpeech の読み上げ経路が疎通すること。

---

## Issue C: soren ログ・tmp の retention 整備

**背景**
- `/home/ubuntu/soren/logs` 310 MB。`soren_loop.log` は 165 MB の単一ファイルで現在も追記中。`ai_stderr.log` 62 MB。
- `/etc/logrotate.d/soren` が存在せず、世代ファイルも無い（未ローテーション）。
- `/home/ubuntu/soren/tmp` 1.62 GiB。`direct_av_sync` 114M・`direct_stream_benchmark` 145M は 2026-08-11〜12 の残骸、`deploy-backups` 178M は deploy 毎に増加。

**やること**
- `logrotate` 設定（size + rotate、追記中は `copytruncate`）。
- `soren/tmp` の古い検証残骸・deploy-backups への TTL 適用。
- `soviet_local_chromium_profile` と `debug/ai_dispatch` は live のため除外。

**受入基準**
- ログが世代管理され、tmp が無制限に増えないこと。

---

## Issue D: strategy 永久アーカイブの世代設計

**背景**
- `/home/ubuntu/soren/strategy_versions_archive/by_hash` = 4,475 ファイル / 825,921,536 B（0.83 GiB）。
- `games/soviet_now/core/config.sh`: `STRATEGY_HASH_PERMANENT_ARCHIVE_DIR="strategy_versions_archive/by_hash"`、コメントに「永続アーカイブ: prune されないバックアップ」。`eloop_improve.sh` が追記。

**やること**
- gzip 圧縮、または keep 上限（`HASH_ARCHIVE_KEEP_TOP` 相当）の設計適用。
- 「prune されない」を前提とした backfill フォールバックへの影響を確認。

**受入基準**
- 世代数に上限、または十分な圧縮率で、増加が有界になること。

---

## Issue E: VM ルーチン回収手順の明文化

**背景**
- Phase 1 の安全な回収（apt clean / journal vacuum / snap disabled revision 削除）が定型化されていない。

**やること**
- `docs/ops/vm-storage-reclaim-runbook.md` を運用手順として採用し、定期実行（または閾値超過時）を定義。

**受入基準**
- 手順書に従って削減前後が実測記録される運用が回ること。

---

## Issue F（別Issue・既知）: Git bundle retention

**背景**
- `/home/ubuntu/.local/state/github-vm-ops/bundles/docich/` に 241 個 / 504,344,576 B（0.47 GiB）。mtime 9/6〜9/14 09:15 で ≈60 MB/日増加。retention 無し。
- 別Issueでローテーション設計予定のため、本調査では削除・retention設計の対象外。

**やること**
- 世代/期間ベースの retention と、release との対応関係の整理。
