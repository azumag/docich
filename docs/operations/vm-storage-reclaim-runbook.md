# 本番VM root filesystem 容量回収 runbook（Phase 1）

- 対象: `azumag/docich` 本番VM（`soren-prod-vnic`, Oracle Cloud aarch64, root `/dev/sda1` ext4 45 GiB）
- 調査日: 2026-09-14（read-only 調査の結果に基づく）
- 前提: 本 runbook は**手順書**であり、実行は operator の明示承認後に行う。記載のコマンドは実行前に対象を `du`/`ls` で必ず再確認する。
- 実行経路（AGENTS.md「Production deployment contract」準拠）: production への変更は ad-hoc SSH ではなく owner-only control plane を使う。本 runbook の Phase 1 は reviewed helper `ops/vm_actions/storage_reclaim.sh` として実装し、`.github/workflows/vm-operations.yml` の `operation=reclaim`（`target=production`, `ref=main`, `confirm=production`, `apply=false` で dry-run / `apply=true` で適用）から実行する。`exec` の任意コマンドは public repo では authorize により無効化されているため、固定 reviewed helper を main から送る本経路が正本。削減量は前後の read-only `status`（使用率・空き容量）で確認する。
- 実行順: まず `apply=false`（dry-run）で DEL 対象と見込み bytes を確認し、対象が想定どおりであることを確認してから `apply=true` を実行する。VOICEVOX アーカイブは helper 既定では対象外で、`--include-voicevox-archive` 相当の明示指定時のみ削除する（再取得手段の確保が前提）。

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
| 2026-09-23 | Phase 1 apply（reclaim, allowlist + system + logrotate） | owner 承認・Astra 実行（VM operations run 35769050373, exit 0） | 86% / avail 7,091,830,784 B | 84% / avail 約7.6GB | dry-run run 35766163981 先行。before status run 35766046429 / after status run 35769216493・diagnostics run 35769274262。soren_logs 114,114,560 → 108,208,128 B (count 72→85, logrotate 初回ローテート)。soren_tmp は live 増加で相殺のため +12MB。実測削減は約+0.5GB（見込み1〜1.5GBとの差は stale 残骸が調査時より小さいため）。 |
| 2026-09-23 | Phase 2 apply（PR #1000: snapd cache 7日超・/tmp の古い docich clone・dangling docker 168h・`manual_challenge*`） | owner 承認（マージ→dry-run→apply 一括）・Astra 実行（dry-run run 35782642940 / apply run 35782692590、いずれも exit 0） | 84% / avail 8,016,740,352 B | **80% / avail 9,611,694,080 B（+1,594,953,728 B ≒ +1.49GB）** | production は `configured @ 9c4bb60a`（main push deploy run 35782514283 success 後に実行）。対象実測（apply 後 read-only SSH）: snapd cache 1.75 → **0.05GB**（7日超残 0、残り1件は7日以内で KEEP＝age gate 正常）/ `/tmp/opencode/docich-sync` **削除済** / `manual_challenge_20260824_meriken` **削除済** / docker Images 9→8・Build Cache 93→89 entries・Containers 0・**Local Volumes 0 維持**（`until=168h` gate により 5〜6日前の dangling 7個は保護＝設計どおり）。protected 3件（state/debug/chromium profile）生存。diagnostics: `required_down=[]`・`required_stale=[]`・zombies/duplicates/stale_locks 全 0・tracked drift 全 0・`game_switch` ready。`soren_loop`/`improve_daemon` の paused は `pause_owner=lifecycle_owned`（コーナー稼働時の通常 pause、helper に kill/systemctl 系コマンドは0件）。並行増加分: `/var/lib/snapd/snaps` 0.09 → 1.40GB（同時刻の snap refresh が新 revision を獲得、差分の約1.3GB を相殺）、bundle 1.40 → 1.42GB、journal 172.8 → 180.8M。**削除対象合計 ≈2.9GB に対し df 差分が +1.49GB なのはこの並行増加による**（top-level du で /var 3.67→3.23・/tmp 2.32→1.27・/home 13.73→13.44 を確認）。opencode.db 1.21 → 1.14GB（#389 の retention が動いた模様、引き続き観測）。 |
| 2026-09-23 | Phase 2b apply（PR #1005+#1007: Tier1 leftovers・`soren-src`・Aivis opt-in 実装、`aivis_engine=false`） | owner 承認（Tier1+Aivis+soren-src+Tier3 着手）・Astra 実行（dry-run run 35788326768 / apply run 35788459828、いずれも exit 0） | 80% / avail 9,525,788,672 B | **79% / avail 9,983,737,856 B（+457,949,184 B ≒ +437MB）** | production は `configured @ 487db5fe`（PR #1007 merge 後 deploy run 35788286349 success）。**途中障害**: PR #1005 直後の初回 dry-run（run 35787826037）が `operation_rejected` — helper 16,919B が gateway stdin 上限 16,384B を超過。**dry-run 時点の拒否で production 変更はゼロ**（fail-closed 正常）、PR #1007 でコメント整理し15,920B（余裕415B）＋サイズ回帰テスト（hard 16384 / 警告16000）を追加。対象実測（read-only SSH）: home 残骸7件（mkv 75.3MB・soren91-r97 22MB・docich-soren91 7MB・soren91-corner-verify 5MB・docich-paper-strategy-315 5MB・phase1 tar/tgz 2件）**全 gone** / `/tmp/soren91-phase1-rx.ts` 119MB + `issue303_*_recv.ts` 5個 117MB **全 gone** / `soren/tmp/deploy` 75.7MB・`radio_quarantine` 10.7MB・`manual_challenge_20260824` 18MB **全 gone**。**KEEP（gate 正常）**: `/tmp/s91test` 181MB（9/25 に7日到達で次回対象化）、`/home/ubuntu/soren-src` 1.05GB（.git mtime 9/23＝read-only probe の index.lock 作成で更新、9/30 頃に自動対象化）。**AivisSpeech-Engine 1.11GB は opt-in false で alive**（削除は再取得手段確認後）。protected 3件＋**配信 ffmpeg（pid 2250958, 18時間稼働）** 生存。diagnostics（run 35788594838）: `required_down=[]`・`required_stale=[]`・zombies・stale_locks・drift 全0、`game_switch` ready（sorengame gen300）、soren_loop/radio_worker alive。削除合計 ≈437MB は df 差分と一致（KEEP 2件を除く）。 |

## 6. Phase 2（2026-09-23 read-only 全体帰属に基づく追加対象）

Phase 1 後に承認済み read-only SSH（補助経路）で root fs 37GB を `du` / `docker system df` で切り分け、**tar と docker は合計0.13GBで圧迫要因ではない**ことを確認した。真因は swapfile 8.00 + OS 6.44 + TTS 3.17 の固定費、同一 repo checkout の重複5箇所 約8.3GB、snapd cache 1.75GB、`/tmp/opencode/docich-sync` 1.05GB、opencode.db 1.36GB。以下は helper の allowlist へ追加した対象（すべて age gate 付き、固定 path、dry-run 既定）。

| 対象 | 実測 | 安全条件 |
|---|---|---|
| `/var/lib/snapd/cache` の7日超 blob | 1.75GB / 17ファイル（最古 8/24） | 固定 path・`-maxdepth 1`・type f・7日超のみ。root 0700 のため `sudo -n` で list/stat/rm。cache のみで snap 本体は触らない |
| `/tmp/opencode/docich-sync` | 1.05GB（`.git` 1,054MB、docich HEAD `292a937` = 2026-09-16、`ps`/`/proc` 参照0） | 固定 allowlist。origin が `azumag/docich` のみ・dir/`.git`/`objects` と全ファイルが7日超・参照プロセス0 のみ削除。いずれか不確かなら KEEP/SKIP |
| docker dangling image + build cache | `docker system df` 上の再取得可能分 約113MB（Images 25.12 + Build Cache 88.18）。**ただし `until=168h` gate のため即時分は7日超の部分のみ**（現行 dangling は5〜6日前が中心なので初回は小さく、日を経て対象化する） | `docker image prune -f --filter until=168h`（dangling 強制・作成が7日超のものだけ）+ `docker builder prune -f --filter until=168h`（`unused-for` の同義語＝7日間使用されていない cache だけ。最近使った cache は日付に関係なく残る）。tagged image・container・`volume prune` は一切なし（PAPER sandbox は volume 不使用契約） |
| `manual_challenge*` 日付付き兄弟 | 18MB（8/24） | pattern を完全一致から前方一致へ。age gate（21日）は個別判定、本体は mtime 次第で KEEP |

### Phase 2b（2026-09-23 追加承認: Tier 1 leftovers + AivisSpeech + soren-src）

オーナーが追加承認した3系統（すべて read-only で絶対パス参照ゼロを確認、`ps`/`/proc` 参照検査は helper 内で apply 時にも再実行）。

| 対象 | 実測 | 安全条件 |
|---|---|---|
| HOME 直下の検証残骸: `2026-08-11 05-35-22.mkv`(75.3MB)・`soren91-r97`(22MB)・`docich-soren91`(7MB)・`soren91-corner-verify`(5MB)・`docich-paper-strategy-315`(5MB)・`soren-phase1-*`(約0.4MB) | ≈115MB | `stale_paths` 固定 allowlist（絶対パス・glob なし）・7日 gate・参照プロセス0 のみ削除 |
| system `/tmp` の古い成果物: `s91test`(181MB, 9/18)・`soren91-phase1-rx.ts`(119MB, 9/13)・`issue303_*_recv.ts` 5個(約117MB, 9/13) | ≈417MB | 同上（`--sys-tmp` はテスト専用、production は `/tmp` 固定） |
| `soren/tmp/deploy`(75.7MB, 8/16)・`radio_quarantine`(10.7MB, 8/12) | ≈86MB | 既存 `stale_patterns` へ追加、21日 gate |
| `AivisSpeech-Engine` | **1.11GB** | **opt-in のみ**（`aivis_engine` 入力 / `AIVIS_ENGINE`）。VOICEVOX engine（稼働中のTTS）が intact なときだけ削除。全域 grep で参照ゼロ・process 0・8/13以降更新なしを実測 |
| `/home/ubuntu/soren-src`（soviet_now clone 2本目） | **1.05GB** | `stale_clones` に `path\|origin` 形式で追加。origin=`azumag/soviet_now` 一致・**dirty なら KEEP**・7日 gate・参照0 のみ削除 |

- **除外（実測で判明）**: `/home/ubuntu/build`（**配信 ffmpeg x11grab が18時間稼働中**、mtime が古くても live）、`/home/ubuntu/soren-persist`（`strategy/persist.sh` 参照）、`.cache/ms-playwright`（chromium 22プロセスが実使用中）。
- dirty check は `git --no-optional-locks status` で実行（通常の `git status` は `.git/index.lock` 作成で `.git` dir mtime を更新し、直後の freshness gate が自己矛盾するため。順序も freshness → origin → dirty に変更）。

- 実行順: merge 後に `reclaim / apply=false` で DEL/CMD 一覧を VM private log で確認 → `apply=true` → 前後の `status`（使用率・空き）と read-only `du` で削減を実測して本表へ追記。
- **実施済み（2026-09-23）**: PR #1000 merge → dry-run → apply を実行し、削減と保護パス生存を §5 承認記録へ実測追記した（84% → 80%、+1.49GB）。`until=168h` gate の効き目で docker の 5〜6日前 dangling は保護され次回以降に対象化するため、以後の定期 reclaim で漸減する。
- スコープ外（参照未検証のため次回以降）: `/tmp/s91test` 181MB、`soren/tmp/deploy` 75MB、`radio_quarantine` 10MB、home 直下の mkv 75.3MB・`build/` 38MB・`soren91-r97/` 22MB。opencode.db(#389) / strategy archive(#392) / Git 履歴重複 / bundle retention(#365,#394) は設計 Issue のまま。
