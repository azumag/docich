# NetHack を docich で動かす

NetHack は docich の `cli` アダプタの標準例である。ゲーム画面がそのままテキストで観測できるため、
brain 開発・動作確認の題材として最も扱いやすい。設計の背景は `docs/architecture.md` §4.2 を参照。

## 表記ルール

- 【確認済】: 一次情報 (パッケージリポジトリ・`docs/architecture.md`・実機コンテナでの動作実証) で裏取りした事実
- 【要検証】: Oracle ARM 実機での検証が必要な事項

---

## 1. インストール

`scripts/setup_ubuntu_arm.sh` で `nethack-console` パッケージ (Ubuntu 24.04 universe) が
導入される。個別に入れる場合:

```bash
sudo apt install -y nethack-console
```

---

## 2. 起動

```bash
bin/docich up             # display(:98)/audio/stream 基盤
bin/docich start nethack  # ゲーム起動 (docich-game tmux セッション内で nethack を実行)
bin/docich status
```

`config/games/nethack.toml` の主要設定:

```toml
[game]
name = "nethack"
title = "NetHack"
adapter = "cli"

[cli]
command = "nethack"
cols = 80
rows = 24
font = "monospace"
font_size = 18

[nethack]
persistent_run = true
player_name = "docich"
save_dir = "/var/games/nethack/save"
xlogfile = "/var/games/nethack/xlogfile"
dump_dir = "/var/games/nethack/dumps"

[nethack.startup]
enabled = true

[nethack.narration]
enabled = true
cooldown_s = 20.0
speaker = ""

[agent]
enabled = true
brain = "nethack"
interval_ms = 1500
```

`persistent_run=true` は長期攻略用の明示 opt-in。これが無い通常の CLI `nethack` 定義は、従来どおり
汎用 `CliCoordinatorAdapter` として動作し、ディストリビューション固有の save path を要求しない。

---

## 3. 観測 (テキストで取れる)

```bash
bin/docich obs nethack
```

`cli` アダプタは `tmux capture-pane -p` の結果をそのまま Observation JSON の `text` フィールドに
入れて返す (`kind: "text"`)。screenshot を介さないため、brain (LLM) に画面がそのまま正確に渡る
(`docs/architecture.md` §3.1, §4.2)。

---

## 4. 操作

```bash
bin/docich send nethack '{"type":"text","text":"h"}'    # 左移動 (vi キー)
bin/docich send nethack '{"type":"special","key":"Escape"}'
```

`text` は `tmux send-keys -l` (リテラル送出)、`special` はキー名 (`Escape`/`Enter`/`C-c` 等) を
`send-keys` に渡す (§3.2, §4.2)。

`docich-game` セッションは読み取り専用 (`tmux attach -r`) で xterm に表示されているだけなので、
Xvfb 上に映る画面を直接クリック/キー入力しても NetHack には届かない【確認済】。入力は必ず
`bin/docich send` (内部で `send-keys`) 経由で行うこと。誤操作が紛れ込みにくい構造になっている。

---

## 5. 表示の調整 (xterm フォントサイズ)

game window では xterm が `tmux attach -r` (読み取り専用) して端末を Xvfb 上に表示する。
【確認済】monospace 18pt (既定値) で 80 桁がちょうど 1280px 幅に収まり、NetHack の 80x24 画面が
そのまま綺麗に表示されることをコンテナで実証済み。別の解像度・フォントを使う場合は
`config/games/nethack.toml` の `[cli] font_size` を変更して調整する。

また `docich-game` セッションの tmux status バーは off にしてある。付けたままだと配信画面の
下部に tmux の緑色ステータスバーが映り込むため【確認済】。

---

## 6. 補足

NetHack は例であり、`[cli] command` を差し替えれば任意の CLI/TUI ゲームが同じアダプタで動く
(§4.2)。ゲームの生死は `docich-game` tmux セッションの生死で判定するため、xterm 表示側が
落ちても (映像が消えるだけで) ゲーム進行自体は失われない。

---

## 7. NetHack 長期攻略コーナー (#490)

NetHackには、24時間 rolling rotationに参加するbounded slotと、死亡・昇天を境界にする専用の
長期攻略枠の2経路があります。rolling rotationでは `config/docich.soren-live.toml` の
`[retro_corner].games` に登録され、通常の `duration_minutes` で終了します。`persistent_run = true`
と `NethackCoordinatorAdapter` がslot切替時に通常のsave boundaryを作るため、次回のNetHack slotで
同じ冒険を再開できます。専用の `[nethack_corner]` と同時には有効化できません。

専用の定時コーナーと手動テストは別々の state/lock を使うため、手動 smoke がその日の定時枠を消費しません。

通常設定は安全のため `enabled = false`。本番時刻を決めて有効化するまでは自動起動しない。

```toml
[nethack_corner]
enabled = false
start_hour = 22       # 設定例。P0では本番時刻として確定していない
duration_minutes = 30
timezone = "Asia/Tokyo"
# weekdays = [0, 2, 4]  # 任意。0=Mon .. 6=Sun
```

### 一試合(死亡)境界とハング対策

NetHack は分数ではなく **一試合（死亡・昇天で終了）を境界**にします。`run_boundary = true`（既定）のとき、
コーナーは `duration_minutes` では終わらず、committed runtime の可視 TTY が終了画面になった時点で
終了します。終了判定は read-only の capture で行い、入力を送りません。canonical の committed NetHack
runtime だけを追い、presentation window (`game-g<N>`) の所有者を確認したうえで、**実際の capture は
runtime の birth window (`nethack-console`) から**行う。`game-g<N>` はゲーム session を映像化する xterm を
起動しているだけで、その pane に出るのは xterm 自身の出力であり、ゲーム画面ではない
（`adapters/cli_game.py`）。ここを取り違えていたため、プレイ中でも画面が変化していないと判定され、
**コーナーが必ず開始ちょうど `stall_timeout_minutes` 後に `stalled` で終了し、死亡/昇天も検出できなかった**
（2026-09-19 に本番で実測。16:12→16:21 / 16:27→16:37 / 16:56→17:06 と3回とも約10分）。
birth window は「presentation window が存在し、agent window を除いた残りがちょうど1つ」のときだけ
採用する fail-closed な解決で、曖昧なら capture しない（= 進捗なし扱い）。

- 終了マーカー: `You die...` / `You have died.` / `Do you want your possessions identified?` /
  `You ascend ...` / `Do you want to see what you had ...`
- ハング対策: **`stall_timeout_minutes`（既定 10 分）の間 TTY が変化しなければ打ち切ります**。
- `run_boundary = false` にすると従来どおり `duration_minutes` で終了します（後方互換）。
- run boundary 中は `duration_minutes` を境界に使いません。手動 runner も config の `run_boundary` に従うため、
  `bin/docich-nethack-corner-manual start --duration-minutes 5` の分数は run boundary 時は無視されます。

定時 runner:

```bash
bin/docich-nethack-corner tick
bin/docich-nethack-corner status --json
```

手動 smoke runner:

```bash
bin/docich-nethack-corner-manual start --duration-minutes 5
bin/docich-nethack-corner-manual status --json
bin/docich-nethack-corner-manual stop
```

本番 VM で owner-only に手動実行する場合は、GitHub Actions の **NetHack corner operator** を
protected `main` から手動実行する。固定 operation `start` / `stop` / `status` / `recover` / `force-recover` だけを受け付け、
`start` は `--duration-minutes`（1-60分）で bounded に実行する。workflow は値の検証と、production が
現在の protected main と一致することの確認だけを行い、VM 上では reviewed な
`bin/docich-nethack-corner-operator` が `config/docich.soren-live.toml` を明示して manual runner を
呼ぶ。generic な arbitrary exec は public repo では無効のままとする。`start` は長時間 oneshot を
detach 起動するため workflow は duration 分ブロックしない。`status` は VM 出力を返さず、固定の
exit-code カテゴリ（idle/terminal=0, starting=10, active=11, failed=12, unreadable=13）だけを
workflow の notice に出す。`recover` は、コーナーが `failed` になった後も canonical の active game が
NetHack のまま残った場合に、manual state に記録された previous game へ bounded に戻す（任意の
ゲームは指定できない。記録が無ければ fail-closed）。`force-recover` は `active` / `draining` の
まま動けなくなった中途状態を解消するための最後の手段で、後述の「中途状態の回復」にまとめる。

### 配信カテゴリー・タイトルの追従

コーナーが実際にゲームを切り替えたら、配信の Twitch カテゴリーも追従する。NetHack コーナー中は
`[NetHack]`（category_id 130、`config/games/nethack.toml` の `[twitch]`）、コーナーが終わって元ゲームへ
戻れば元のカテゴリーへ戻る。

- 実行するのは reviewed な Soren 側スクリプト `update_stream_game.sh` のみ。docich 側は
  `src/docich/stream_category.py` が **固定引数**（`--game <検証済みゲーム名> --games-dir <このリポジトリの
  config/games>`）で detached 起動するだけで、token・channel id 等の機密は一切渡さない
  （スクリプトが Soren root の `.env` を自分で読む）。`--games-dir` は必須: スクリプトの既定は Soren
  チェックアウト側で、ゲーム定義はこのリポジトリにあるため。
- 呼ぶのは **coordinator の切替が成功した後**だけ。失敗した切替は通知しない。
- 通知の失敗はコーナーを失敗させない（カテゴリーのズレは表示の問題、切替の巻き戻しは配信事故）。
  結果は `<state_dir>/logs/stream-game.log`（0600）に残す。
- `[twitch]` が無い・`category_id` が空のゲームは**何も通知しない**（現在のカテゴリーを推測で変えない）。
- 対象はコーナー経由の切替（NetHack・レトロ各種、operator の `recover` を含む）。CLI の
  `docich switch` からの直接切替は未接続。

どちらもゲーム切替を直接操作せず `GameSwitchCoordinator` を通す。開始前に別ゲームが active なら
NetHack へ transactional switch し、終了時に元のゲームへ戻す。元が idle なら NetHack 終了後も
idle に戻す。

P0時点は `agent.enabled=false` だったが、現在の標準configでは reviewed P3b brainと起動応答・ナレーションを有効化する。
既知の英語キャラ作成質問のみ応答し、60秒/40観測/12入力で停止する。
未知画面では無入力、gameplay到達後は可視安全地形への一歩とMoreだけを許可する。
ナレーションは `nethack:policy` context・既定音声でaudio workerへ順次投入し、20秒cooldownと同一intent抑制を行う。
発話障害はゲーム操作に影響させない。設定・上限・未対応範囲は [nethack-ai.md](nethack-ai.md) を参照。
本番での到達・音声再生は配備後の別検証が必要であり、「AI攻略が完成した」とは扱わない。以降は #490 の
ロードマップに従い、run 永続化 → spectator tile renderer → tactical/mid-level/LLM policy →
structured observation → 死亡履歴からの継続改善、の順に追加する。特にグラフィック表示は AI の
正確な text/structured observation と分離し、視聴者向け presentation のためだけに画像認識へ
退化させない。

### 中途状態の回復（`force-recover`）

manual state が `active` のまま canonical の phase が `draining` で止まると、従来は `stop` も `recover` も
進められなかった。原因は `_finish_locked` の先頭にある canonical active game の読み出し
（`retro_corner.py` の `_canonical_active_game`）が `ready` / `idle` 以外の phase で例外を投げ、しかもそれが
`failed` 記録より前のため state が `active` のまま残ることにある。`_ensure_runtime()`（`cmd_up`）の失敗も
同じく `active` のまま残っていた。加えて operator が manual runner を 600 秒で kill する
（`subprocess.run(timeout)`）と、coordinator の既定 request timeout（600 秒）と競合して `draining` を残しうる
（後者は推測であり、本番では未確認）。

- **`stop` の堅牢化**: manual manager の `stop` は、`_ensure_runtime()` 失敗・canonical phase が安定でない・
  canonical が読めない、のいずれでも manual state を `failed` にし、`last_error` に固定 token
  `stop_blocked:<runtime_unavailable|switch_not_stable|switch_state_unreadable>` を残して失敗する。
  以降の `stop` は `noop`（not-active）になり、同じ失敗で詰まらない。save boundary 経由で元ゲームへ戻す
  通常の `stop` の意味論は変えない。scheduled corner の `stop` も変えない。トレードオフとして、稼働中の runner が
  ある状態で `stop` が preflight 失敗すると corner は `failed` で終わり（runner は次の poll で終了する）、
  NetHack は canonical active のまま残るので `recover` が必要になる。
- **`force-recover`**: 引数も対象ゲームも取らない固定 operation。ゲームの stop / switch / start を自分では呼ばず、
  変更するのは manual state と、canonical が非安定 phase のときの `GameSwitchCoordinator.recover()`
  （既存の recover 契約）だけ。`recover()` は canonical に記録済みの中途 transition を契約の範囲で完了/巻き戻す
  だけで（期限切れ `draining` は、game adapter が boundary の取消しを acknowledge したときだけ、同じ runtime を active のまま取り消す）、新しい対象は選ばない。
  成否は `recover()` の戻り値ではなく、呼出し後に canonical を読み直した phase で判定する
  （期限切れ `draining` の取消しは `failed`/`timeout` の receipt を返すが、canonical は `ready` に戻るため）。
  期限内の `draining` は `recover()` が read-only の `busy` を返すので何も変えない。
- **fail-closed**: 解消後も NetHack が canonical active のままなら stop しない。`active` は `stop`、
  `failed`（と、`starting` を `failed` に落としたもの）は `recover` へ誘導する。manual state を終端にするのは
  NetHack が canonical active でない場合だけで、終端は `interrupted`（`ended_unknown` は run history の
  status であり corner state では無効）。
- **Actions**: workflow の `operation=force-recover` は固定 exit code だけを notice に出す（`status` と同様、
  VM の出力は返らない）。step が green になるのは `start` を実行してよい状態（`recovered` /
  `nothing_to_recover`）のときだけで、それ以外は notice に理由を出したうえで red にする。

| exit code | category | 意味 / 次の手 |
| --- | --- | --- |
| 0 | `recovered` | manual state を終端化し canonical は安定。`start` 可 |
| 20 | `nothing_to_recover` | 何も変更なし。`start` 可 |
| 21 | `manual_lock_busy` | 稼働中の runner が lock を保持。何も変更なし |
| 22 | `manual_state_unreadable` | manual state が壊れている。上書きしない |
| 23 | `switch_state_unreadable` | canonical が読めない |
| 24 | `switch_busy` | 期限内の `draining` 待機中。期限後に再実行 |
| 25 | `switch_recover_failed` | coordinator が非安定 phase を解消できなかった |
| 26 | `nethack_active_use_stop` | NetHack が active。次に `stop` |
| 27 | `nethack_active_use_recover` | NetHack が active。次に `recover` |
| 28 | `out_of_scope` | 対象外（無関係な切替中、所有者不在の NetHack、復元先不明） |
| 29 | `unexpected_error` | 想定外の失敗 |
| 30 | `drain_cancel_refused` | 期限切れ `draining` の取消しを game adapter が受理しなかった（canonical は `draining` のまま）。NetHack は「未回答の `Really save? [yn] (n)` を観測できたとき」だけ受理する |

エージェントを新コードで入れ替える手順は、`force-recover` → （26 なら `stop`、27 なら `recover`）→ `start`。
`stop` / `recover` は通常どおり save boundary 経由で元ゲームへ戻し、`start` が新しい runtime と agent を起動する。
`start` は manual state が `active` の間は拒否され、`force-recover` の後でも NetHack が canonical active の
ままなら新しい runtime にならない（`current == target` で transition が no-op になる）ため、必ず元ゲームへ戻してから行う。

---

## 8. 長期runの通常save/restore (P1a)

`persistent_run=true` の NetHack だけ、coordinator の switch/stop 前に通常の NetHack save を安全境界として
利用する。legacy `docich obs/send` は引き続き汎用 CLI adapter を使うため、AI observation 契約は変わらない。

起動時は coordinator adapter が `-u docich` を追加する。同じ Unix uid + player name を継続することで、
NetHack 自身の通常 restore を利用する。wizard (`-D`) / explore (`-X`) mode は長期攻略では拒否する。

終了時の順序:

1. active runtime/session ownership を確認
2. 実ゲームを持つ tmux birth window を一意に確認
3. 現在存在する同player saveの署名 (mtime/size) を記録
4. `Escape` でmenu/promptから抜ける
5. 通常コマンド `S` を送る
6. NetHack process window が終了したことを確認
7. **送信前から新規作成または更新された**同player saveを確認
8. 両方揃った場合だけ round-boundary を成功させ、coordinator が元ゲームへ切り替える

processだけ消えてsaveが作られない、tmux window一覧が取得できない、runtime windowが曖昧、という場合は
fail closed とし、単に「ゲームが終わった」と推測して切替を続行しない。古いsave fileが残っているだけでも
成功扱いしない。

ゲーム側が既に `S` で終了していて、process windowが無い一方で同player saveが存在する場合は
`suspended` として扱う。processもsaveも無い場合だけ `ended` と記録し、死亡・quit・ascension の分類は
P1b の run history へ委ねる。

境界の診断結果は generation runtime の `nethack_boundary.json` に `suspended` / `ended` として残す。

### boundary の取消し（cancel）

coordinator は boundary の待機が失敗・期限切れになると、adapter に boundary request の取消しを要求し、
adapter が acknowledge しない限り canonical の `draining` を `ready` に戻せない（`recover()` も同じ）。
`S` は確認（`y`）を与えるまでは可逆で、NetHack が出す `Really save? [yn] (n)` は既定回答 `n` でゲームを続行する。
確認後は save を書いて終了するため戻れない。よって NetHack adapter の cancel は次の条件をすべて満たしたときだけ
`n` を1回送り、プロンプトが消えてプロセスが生きていることを確認してから acknowledge する。

- session と所有権を確認でき、プロセス window が存在する
- 画面に**未回答**の `Really save? [yn] (n)` 行がある（`... (n) y` のように回答済みの行は対象外）

それ以外（プロンプトなし・プロセス消失・capture 失敗・所有権不一致）は一切キーを送らず拒否する
（fail closed）。かつては無条件に拒否していたため、driver が確認前に死ぬと `draining` が恒久的に残った。

---

## 9. 遠征履歴と終了結果 (P1b)

`NethackRunStore` はゲーム本体のsaveとは別に、番組側の「第N次遠征」を
`state_dir/nethack/` 以下へ記録する。run JSON はゲームを復元するためのsaveではなく、分析・番組表示・
将来の戦略改善のための履歴である。ゲーム状態の正本は常にNetHack自身のsaveとする。

### 継続するrun

- 初回開始: 新しい `run_id` と `expedition` を採番
- コーナー終了時に通常saveが存在: `suspended`
- 次回restore: 同じ `run_id` / `expedition` を `active` に戻す
- NetHackが元からactiveのままコーナーだけ終わる: `active` のまま session を `continued` として閉じる
- 履歴導入前のsaveが存在: 削除せず `recovered_existing_save=true` で採用
- 履歴導入前からNetHack runtimeがactive: `adopted_active_runtime=true` で採用

tracked `suspended` runなのにsaveが無い場合は、勝手に新規runを始めず fail closed にする。
またP1aを通した外部game switchによって `active` 履歴だけが残り、実際にはsave済みだった場合は、saveの存在を
根拠に `external_suspend_detected` として再同期する。

### terminal result の正本

Debian/Ubuntu版NetHackが書く `xlogfile` を機械可読な終了結果の正本として扱う。run開始時にxlogfileの
byte offsetを記録し、そのoffsetより後に追加された **同じplayer nameのrecordだけ**を読む。これにより、
過去の死亡を現在runの死亡と取り違えない。

保存する主な値:

- `points` → score
- `turns`
- `maxlvl` → max depth
- `death` → 記録上の終了理由
- `role`, `race`, `gender`, `align`
- `achieve` bit field
- start/end/realtime

`achieve & 0x0100` または `death` がascensionを示す場合は `ascended`、quit/escapeは `ended`、それ以外の
terminal recordは `dead` とする。`achieve & 0x0020` は Amulet of Yendor 取得として保存する。

xlogfileが無い、run開始後にtruncateされた、同playerの新規recordが無い場合は **死因を推測しない**。
`ended_unknown` と `analysis_error` を記録する。

### dumplog

`dump_dir` は詳細な反省材料として使う。run開始時点の最新mtimeをbaselineとして保存し、終了後にそれより新しい
同playerのdumplogだけを関連付ける。run JSONにはhost path全体ではなくbasenameだけを保存する。

### stateのクラッシュ整合性

run本体、`current.json`、`meta.json` は複数ファイルなので、書込み順を明示する。

1. run本体をatomic write + fsync
2. `current.json` をatomic write + fsync
3. expedition counter (`meta.json`) を更新

`meta.json` はrunファイル群から最大expeditionを復元できるため、3の前に停止しても番号を再利用しない。
terminal時はrun本体を先に保存してからcurrent pointerを削除する。削除直前に停止してterminal runへのpointerが
残った場合は、次回 `prepare_start` がterminal runを確認してpointerだけを安全に除去する。逆にpointerだけあり
run本体が無い状態は推測で修復せず fail closed にする。
