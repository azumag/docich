# 本番VM root filesystem 容量回収 runbook（Phase 1）

- 対象: `azumag/docich` 本番VM（`soren-prod-vnic`, Oracle Cloud aarch64, root `/dev/sda1` ext4 45 GiB）
- 調査日: 2026-09-14（read-only 調査の結果に基づく）
- 前提: 本 runbook は**手順書**であり、実行は operator の明示承認後に行う。記載のコマンドは実行前に対象を `du`/`ls` で必ず再確認する。
- 実行経路（AGENTS.md「Production deployment contract」準拠）: production への変更は ad-hoc SSH ではなく owner-only control plane（`.github/workflows/vm-operations.yml` の `exec / production`、または reviewed script を main に置いて同経路で実行）を使う。通常の診断は read-only `diagnostics` / `status`（`.github/workflows/vm-storage-monitor.yml`）で取得する。`exec` の stdout/stderr は Actions log に出ず VM private log に残るため、削減量は前後の `status`（使用率・空き容量）で確認する。

## 0. 安全条件（厳守）

- サービス再起動、worker kill、production drift の上書きはしない。
- 秘密情報（stream key / OAuth / API key / 秘密鍵）を表示しない。prompt/AI入出力/ブラウザ履歴を表示しない。
- 削除前に `du -sx -B1 <path>` と `ls` で対象を確認し、**想定サイズと一致してから**削除。
- 各 Step の前後で `df -B1 /` を記録し、削減量を実測する。
- `/swapfile`、`/var/lib/snapd/snaps` の hardlink 管理、`soren/tmp/soviet_local_chromium_profile`、`soren/tmp/debug/ai_dispatch`、稼働中サービスデータは触らない。

## 1. 現状（調査時の実測値）

| 項目 | 値 |
|---|---|
| total / used / avail | 45 GiB / 36 GiB (35.16 GiB) / 8.9 GiB |
| 使用率 | 80% |

主な寄与（非重複カテゴリ）:
- `/swapfile` 8.00 GiB（固定、触らない）
- `/usr` 6.44 GiB（固定）
- TTS（/opt/voicevox 3.74 + AivisSpeech-Engine 1.11）4.85 GiB
- Git (.git) 3.23 GiB
- `/var` 3.38 GiB
- `/home/ubuntu/soren` 2.93 GiB
- opencode DB+log 2.62 GiB
- browser cache 1.08 GiB
- `/tmp` 0.93 GiB
- gateway Git bundle 0.47 GiB（別Issue）

## 2. Phase 1（安全・即効、合計 ≈3.5–4.5 GiB）

> すべてサービス無停止。実行順は「効果が大きく安全なもの」から。

### Step 1: VOICEVOX 導入アーカイブの退避/削除（≈1.69 GiB）

対象: `/opt/voicevox/voicevox.7z.001`（1,810,684,257 B）

```bash
# 前提確認: 展開済みエンジンが存在し current がそれを指すこと
ls -la /opt/voicevox/
ls -la /opt/voicevox/linux-cpu-arm64/
# voicevox_engine の実起動確認（読み取りのみ。既存の起動経路で疎通確認）
# 再取得経路（配布URL or 手元バックアップ）を operator が確認
du -sx -B1 /opt/voicevox/voicevox.7z.001
```

- 再取得可能が確認できたら退避または削除。ローカルの soren 側から参照されていないことを `grep -R` で確認する。
- 注意: 削除は不可逆。必ず再取得手段を確保してから。

### Step 2: 定番回収（≈1.0 GiB）

```bash
apt-get clean                      # /var/cache/apt 0.34 GiB
journalctl --vacuum-size=200M      # /var/log/journal 0.43 GiB → 約0.2 GiB
```

snap の disabled revision 削除（≈0.23 GiB）:

```bash
snap list --all      # disabled を確認: core22_2438 / core24_1644 / opencode_226 / snapd_27709
# 例: snap remove --revision 2438 core22 （disabled のみ。現行 revision は消さない）
```

### Step 3: soren/tmp の古い検証残骸（≈0.5–0.7 GiB）

対象（**mtime が古く、live でないもののみ**）:

```bash
du -sx -B1 /home/ubuntu/soren/tmp/direct_av_sync            # 114M, 2026-08-12
du -sx -B1 /home/ubuntu/soren/tmp/direct_stream_benchmark   # 145M, 2026-08-11
du -sx -B1 /home/ubuntu/soren/tmp/manual_challenge          # 53M, 2026-08
du -sx -B1 /home/ubuntu/soren/tmp/deploy-backups            # 178M, deploy毎backup（古い分のみ）
du -sx -B1 /home/ubuntu/soren/tmp/game-lifecycle-e2e        # 25M
du -sx -B1 /home/ubuntu/soren/tmp/soviet-iso-verify-*       # 3.3M x N
```

**触ってはいけない（live）**:
- `soren/tmp/soviet_local_chromium_profile`（601M, mtime=現在）
- `soren/tmp/debug/ai_dispatch`（63M, mtime=現在）
- `soren/tmp/state`（189M, 稼働中 state。`xdg_data/opencode/opencode.db` 含む）

### Step 4: soren ログの恒久ローテーション（再発防止）

現状 `soren_loop.log` = 165 MB 単一ファイル、`/etc/logrotate.d/soren` 無し。

```bash
# 例（設定は別途レビュー）。既存ログは切り詰めず、次回ローテーションから世代管理。
# /etc/logrotate.d/soren:
# /home/ubuntu/soren/logs/*.log {
#   weekly
#   rotate 4
#   size 100M
#   missingok
#   notifempty
#   copytruncate   # 追記中プロセスを止めない
# }
```

- `copytruncate` は追記中ファイルに対して使う（サービス停止不要）。
- 初回のみ既存 `soren_loop.log`(165M) のローテーションを検討（削除ではなく `.1` へ退避）。

## 3. 実施後の検証

```bash
df -B1 /                    # used が期待どおり減少
du -sx -B1 <削除した各パス> # 消えていること
# 稼働確認（再起動はしない）
systemctl is-active soren-runtime soren-shared-overlay 2>/dev/null
tmux ls 2>/dev/null
```

- 削減見込み: 1.69 + 1.0 + 0.6 ≒ **3.3 GiB** → used 約31.9 GiB（≈72%）。ログ対応含め整えば ≈68%。

## 4. やらないこと（今回のスコープ外）

- `/swapfile`（8 GiB）の縮小・再作成: mem 23 GiB / swap 使用 89 MB のためリスク大。逼迫時の最終手段。
- gateway `releases/docich` の submodule 履歴重複解消（≈2 GiB）: gateway 設計変更が必要。
- gateway `bundles/docich`（0.47 GiB）: **別Issue**（retention 設計対象）。
- opencode.db（2.32 GiB）: live DB。freelist=0 のため VACUUM 無効。保持期間付き削除/ローテーションを Issue A で設計してから。

## 5. 承認記録

| 日付 | Step | 実施者 | 前 used | 後 used | 備考 |
|---|---|---|---|---|---|
| | | | | | |
