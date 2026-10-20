# ゲスト OS 無応答時の復旧手順（Oracle Cloud 帯域外通常再起動）

Issue #2278 の完了条件 3 に対応する runbook。

2026-10-15、本番 VM のゲスト OS が SSH / WebUI / 診断のいずれにも応答しなくなり、
Twitch 配信が停止した。復旧はオーナー明示承認のもとで Oracle Cloud の**通常再起動**
（帯域外）で行い、13:33 JST に復帰した。本手順はその実績を基に、同じ状態で
**diagnostics が使えない**ことを前提にした復旧経路を定める。

## 1. 適用範囲と前提

- 対象: `docich` 本番 VM（Oracle Cloud、Ubuntu、systemd user/system units）。
- 使うのは **ゲスト OS が無応答**のときだけ。判定は §2 の複数シグナルで行う。
- 通常の VM operations（GitHub Actions → forced-command SSH gateway）が応答する状態では
  この手順を使わない。`diagnostics` / `status` / 個別 recover operation が正規経路。
- クラウド操作権限の新設・自動再起動は #2278 で承認されていない。本手順は
  **オーナーの明示承認を受けた単発操作**に限定する。
- 復旧を優先する（AGENTS.md §12）。原因調査は復旧後に行い、復旧操作と恒久対応の PR を混ぜない。

## 2. 検知シグナル

### 2.1 必須シグナル（ゲスト応答経路の不通）

次のうち**少なくとも 1 つ**が成立していることを必須とする。これは「ゲスト OS 側の
応答経路が落ちている」ことの直接証拠であり、これが無い限り §3 の再起動判断へ進まない。

- `VM operations` の diagnostics が banner exchange でタイムアウトする
  （2026-10-15 は 13:06:36 JST と 13:12:55 JST の 2 回）。
- 手元の SSH が同じく banner exchange でタイムアウトする
  （TCP は受理されるが SSH banner が返らない = ゲスト側の停止を示唆）。
- 管理 WebUI が HTTP を返さない。

いずれも「TCP 接続は成立するがゲストの応答が返らない」ことを確認して判定する。
接続自体が拒否される場合は、ゲスト無応答ではなく別の要因（経路・firewall・インスタンス
停止など）を先に切り分ける。

### 2.2 補助シグナル（状況の裏取り）

次は必須シグナルを**補強する**ものであり、これだけでは再起動判断へ進まない。

- Twitch 公開チャンネルが `No playable streams found` / 公開映像なし。
- Oracle Cloud コンソールのインスタンス lifecycle が `running`（電源断ではない）。

**補助シグナルだけ（例: 公開映像なし + lifecycle `running`）では判定しない。**
配信停止はエンコーダ・ゲーム・コーナーの異常でも起こり得るし、lifecycle が `running` で
あることはゲスト OS が応答していることを意味しない。この 2 項目だけが揃った場合は、
まず正規経路の `diagnostics` / `status` / 個別 recover operation で復旧を試みる。

補助検知として `ops/vm_actions/monitor_silence.py`（#2377）が VM monitor の沈黙を
独立に検知する。これは「気づき」の経路であり、復旧手段ではない。

## 3. 決定ゲート（操作前に必ず確認）

1. オーナー（azumag, actor ID 9018513）の**明示承認**を Issue に記録する。
   タイムアウト・沈黙・過去の同種承認を承認とみなさない。
2. **通常再起動**（Oracle Cloud の Reboot。停止待ちを経て起動）を使う。
   即時強制再起動（Force reboot。OS の応答を待たずに power off → power on）や
   Terminate は選ばない。
3. 通常再起動も**リスクゼロではない**ことを承認依頼の影響に含める（下記 §3.1）。
4. クラウド操作権限を新設しない。既存の Oracle Cloud コンソール操作に限定する。
5. 復旧済みの配信を調査のために再停止しない。
6. 完了条件は**公開 A/V の再受入**まで。SSH 復帰だけでは完了にしない。
7. 対象を取り違えない。インスタンス名だけでなく **OCID とリージョン**を照合する。

### 3.1 通常再起動のリスク（承認依頼に明記する）

Oracle の公式ドキュメント（[Rebooting an Instance](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/restartinginstance-reboot-instance.htm)）に
よれば、コンソールの **Reboot**（`SOFTRESET`）は既定で OS へ shutdown コマンドを送り、
**OS の終了を最大 15 分待つ**。15 分で終了しない場合はインスタンスを power off して
から power on する。公式ドキュメントは、アプリケーションの終了に 15 分以上かかる場合、
不適切な停止が**データ破損を招き得る**と明記している。

つまり通常 Reboot と Force reboot は**待ち時間と即時性**で区別されるが、
**通常 Reboot が無リスクという意味ではない**。15 分の待機を超えれば結果として
power cycle に進み得る。次の点を承認依頼・Issue 記録に含める。

- 通常 Reboot を選ぶ理由は「OS に正常終了の機会を与える」ことであり、
  「データ破損が起きない」ことではない。
- 15 分待っても終了しない場合の挙動（power off → power on）と、その際の
  データ破損リスクを事前にオーナーへ提示する。
- 可能なら、コンソールから Reboot する前に **OS 側のコマンドで正常 shutdown する**
  ことが公式には推奨される。ただし本手順の適用条件（ゲスト OS 無応答）では
  OS に入れないため、この選択肢が取れないことを明示して承認を求める。
- 2026-10-15 の実績（13:17 実行 → 13:33 復帰、uptime 0 min）から、
  当時実際に 15 分超過の強制処理が起きたとは推定しない。経過時間と
  内部挙動は別物として扱う。

## 4. 帯域外通常再起動の手順

1. Oracle Cloud コンソールで対象インスタンスを特定する（インスタンス名・OCID・リージョン）。
2. **Reboot**（通常再起動、`SOFTRESET`）を実行する。Force reboot は選ばない。
   この操作が §3.1 のリスク（OS 終了を最大 15 分待ち、超えると power cycle へ進む）を
   持つことを、実行前にオーナー承認の記録と照合する。
3. 停止待ちを経て起動するまで待つ。2026-10-15 の実績:
   13:17 JST 実行 → 13:33 JST SSH 復帰、uptime 0 min。
   15 分を超えて応答が戻らない場合は、強制処理へ進んだ可能性を考慮し、
   復帰後のファイルシステム・journal を確認する。
4. SSH 復帰を確認する（banner exchange が成功し、ログインできる）。
5. 共有配信・relay/bridge サービスが `active` に戻ったことを確認する。
6. 続けて §5 の検証を行う。

## 5. 再起動後の検証チェックリスト

`diagnostics` が通るようになったら、まず
`gh workflow run "VM operations" --repo azumag/docich --ref main \
  -f operation=diagnostics -f target=production -f ref=main`
を実行し、以下の固定 field を確認する。

- [ ] SSH / ゲスト OS 応答: ログイン可能、uptime が 0 付近。
- [ ] 共有配信・relay/bridge・audio-priority: `active`、keeper pid あり、targets が at_target。
- [ ] WebUI: HTTP 200、`listener_is_unit=true`、`served_matches_deployed=true`。
- [ ] Twitch 公開受信: ライブ表示、映像スコアが盤面変化を示す、音声 mean/max が非無音。
      2026-10-15 の受入実績は 12 秒受信で映像スコア 176→242、音声 mean -25.8 dB /
      max -1.5 dB。
- [ ] `tracked_drift.drift_detected=0`、`scan_complete=1`。
- [ ] `workers.required_down` / `required_stale` / `zombies` が空。
- [ ] `game_switch.phase=ready`、`active_game` が期待どおり、generation が整合。
- [ ] corner-rotation timer が active/enabled。
- [ ] 中断した予約が残っている場合は §6 の照合を行う。

VM の docich HEAD は diagnostics の `sha` で確認する。Actions の candidate SHA と
実 VM HEAD は別物として扱う。

## 6. 中断した予約（slot255 型）の扱い

再起動で corner rotation の `pending` が `dispatched` のまま残ることがある。
**ledger の手編集・`pending` の強制 clear・latch の手動削除・予約の再実行をしない。**

照合する項目:

- receipt の有無と内容（`present` / `status` / request 一致）
- 実行 generation（`active_generation` / `next_generation` / request generation）
- terminal 結果（`terminal_seen`、`terminal_owners`、`terminal_result_matches`）
- 資源解放（`all_resources_released`、`resource_attribution_unknown`）
- 所有者（`pending_owner`、`manual_pending_owner`、`*_owner_status`）
- program slot と game-switch plane が当該 request を解放しているか

未起動の自動予約は `corner-rotation recover` の正規経路が同じ request を保持したまま
再開する（`docs/corner-rotation.md`）。terminal 証拠が無いまま予約を捨てない。

### 2026-10-15 の実績

- 復帰後、slot255（hanjuku-hero）が `dispatched` / `pending=true` / `owner=none` で残った。
- これは待つだけでは解けなかった。原因は別途 #2284 で修正した、復帰済み DOUSHI の
  `failed` 記録が共有 program slot を恒久ブロックする問題で、修正の main 統合
  （2026-10-15 19:18 JST）と配備後に自然 rotation が再開した。
- 以後の自然スロット（10/16 07:41 yosou-oh、08:45 retro_corner、09:45 weather_corner ほか）を確認。
- 2026-10-20 11:38 JST の diagnostics（run 39359980035, sha ca564906）では
  `slot=308`、`pending=false`、`manual_pending=false`、`cooldown=86400`、
  `game_switch.phase=ready`。slot255 の滞留は残っていない。
- 個別の精算記録（slot255 の receipt / generation / terminal の遷移時刻）は正規出力に
  明示されず、特定できていない。状態の収束は上記の実測で確認した。

## 7. 原因調査の証拠台帳（完了条件 1）

ゲスト OS 無応答時、**diagnostics は SSH 依存のため復旧経路にも調査経路にもならない**。
復旧後に取得できる範囲を記録し、取得できないものは「証拠不足」と明記する。

### 今回確認できたこと

- 障害時の diagnostics 2 回（13:06:36 / 13:12:55 JST）が同じ接続タイムアウトで失敗。
- Oracle 上の lifecycle は `running`。電源断ではない。
- 通常再起動で 13:33 JST に SSH 復帰（uptime 0 min）、共有配信・relay/bridge が active。
- 復帰後の diagnostics は `status=ok`、required_down / required_stale / duplicates /
  zombies 空、tracked drift 0、sorengame ready、WebUI `served_matches_deployed=true`。
- 復帰後の公開 A/V 受入（§5 の実績値）。
- 前回 boot の kernel journal のうち 11:45〜13:33 JST の範囲は 14 records で、
  OOM kill / hung task / lockup / panic の marker は無かった。
- 2026-10-20 の diagnostics でも `game_switch_watchdog.last_active_at` が
  2026-10-15 13:33:02 JST を示し、復帰時刻と整合する。

### 取得できなかったこと（証拠不足）

- **障害前のホスト指標**（CPU / memory / swap / I/O / load の時系列）。
  VM 上に履歴を残す収集は無く、diagnostics は実行時点の値しか返さない。
- **クラウドイベント**。本作業環境に OCI CLI / API 資格情報が無く、インスタンスイベント
  （reboot / status change）を取得していない。
- **当時の Actions run ログ**。障害時の run ID（38679659679、38680242904、
  38682137535、38719306359）はいずれも現在 404 で、再取得できない。
  2026-10-02〜10-17 の run 履歴自体が現存しないため、当時の monitor / deploy の
  成否を後から再構成できない。
- **journal の完全性**。14 records は「その範囲に記録が無い」ことしか示さず、
  ログ欠落（停止直前の書き込み消失）を除外できない。

### 原因の評価

**原因は未確定**。次のいずれも否定も断定もできない。

- ゲスト userland の停止（SSH banner 不応答 + WebUI 無応答と整合。kernel marker 無し）。
- メモリ / I/O 飽和。ただし OOM kill の marker は無く、**OOM ではないと断定しない**。
- クラウド / ホスト側の要因。lifecycle が `running` だったこと以外の情報が無い。

時系列上の事実として、12:43 JST の slot255 dispatch が最後に記録された通常動作で、
13:06 JST には diagnostics が通らない状態だった。**slot255 の起動処理と無応答の
因果は未証明**であり、再現試験なしに結び付けない。同様に、11:45 JST を障害開始と
決めつけない（journal の確認範囲の起点として記録された時刻である）。

## 8. 再発防止（今回の対応）

- 復旧手順を本書で固定した（帯域外通常再起動・承認ゲート・公開 A/V 受入）。
- 監視の沈黙を独立検知する #2377（`docs/operations/vm-monitor-silence-alert.md`）が
  main にある。monitor の成功 run が 6 時間途絶えると Issue を 1 件だけ開く。
- 回転停止の原因だった #2284 は main に統合済み（復帰済み DOUSHI の terminal 契約）。
- 自動再起動・クラウド権限の新設・監視閾値の変更は行っていない。

### 残る弱点（follow-up 候補）

- 障害前のホスト指標が残らないため、次回も同じ事象では原因を特定できない。
  時系列のホスト指標（load / memory / swap / I/O）を bounded に保持する仕組みは
  別 Issue で検討する。本書の PR には含めない。
- Actions の run 履歴が失われると、当時の証拠を後から参照できない。
  障害時は **run ID だけでなく、判定に使った固定 field と時刻を Issue に転記する**。

## 9. やってはいけないこと

- ゲスト OS 無応答時に VM operations（SSH gateway）で復旧を試みること。
- 即時強制再起動（Force reboot）・Terminate。
- 補助シグナル（公開映像なし・lifecycle `running`）だけで無応答と判定し再起動すること。
- 通常再起動を「無リスク」として承認依頼すること（§3.1 の 15 分待機とデータ破損リスクを
  提示せずに承認を求めること）。
- オーナー承認なしの再起動、latch 削除、予約再実行。
- クラウド操作権限の新設・自動再起動の有効化。
- 復旧済み配信の調査目的の再停止。
- receipt / generation / terminal / 資源解放の照合前の手動 latch 削除。
- Actions の candidate SHA と実 VM HEAD の混同。
- 証拠が無いまま「OOM が原因」「特定のコーナーが原因」と断定すること。

## 10. 関連

- Issue #2278: 本 runbook の根拠となる障害事例。
- `docs/operations/runtime-diagnostics.md`: 通常時の read-only 診断契約。
- `docs/operations/vm-monitor-silence-alert.md`: monitor 沈黙の独立検知。
- `docs/corner-rotation.md`: 中断した予約の正規復旧（§6）。
- `docs/operations/vm-storage-issues.md` / `vm-storage-reclaim-runbook.md`: storage 系。
- `ops/vm_actions/README.md`: 標準の VM operations 経路（SSH gateway 前提）。
