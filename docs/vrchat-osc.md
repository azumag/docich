# VR同志: VRChat OSC 双方向連携 (docich ↔ VRChat アバター)

- 対象 Issue: [azumag/docich#316](https://github.com/azumag/docich/issues/316)
- 関連: #113（同志の VRChat 探検コーナー）
- 実装: `src/docich/vrchat_osc.py` / `config/vrchat_osc.toml` / `tests/test_vrchat_osc.py`
- 決定の要約: [`docs/adr/0005-vrchat-osc-bridge.md`](adr/0005-vrchat-osc-bridge.md)

## 0. この文書の状態

docich ↔ VRChat の OSC 連携（Phase 1: docich → VRChat / Phase 2: VRChat → docich）は
実装済みで、UDP を実ソケットで使う end-to-end テストと CLI の手動スモークまで確認している。

**VRChat クライアント + Unity アバターでの実機確認は未実施**である（この開発環境に
VRChat も Unity も無い）。§9 の受入チェックリストで、実機で確認すべき項目を明示する。

## 1. 目的

VRChat の「同志」アバターを、docich の状態・AI の発話・配信イベントに反応させる。
逆に、VRChat 内のアバター操作を docich 側の安全な処理へ返す。

```text
docich / LLM / OBS / 各種BOT        VRChat 実行 PC
        │                                  │
        │ speaking.json / event spool      │
        ▼                                  ▼
   ┌───────────────────── OSC Bridge (docich vr-osc bridge) ─────────────────────┐
   │  docich → VRChat : UDP → 127.0.0.1:9000  (/avatar/parameters/*)             │
   │  VRChat → docich : UDP ← 127.0.0.1:9001  (/avatar/parameters/*)             │
   └─────────────────────────────────────────────────────────────────────────────┘
                                   │
                              VRChat Client
                                   │
                          Expression Parameters
                                   │
                              FX Animator
                                   │
                                 VR同志
```

OSC の送受信そのものはアバターへ埋め込まない。VRChat クライアントを介して
Expression Parameter を外部と双方向にやり取りする。

## 2. 配置と責務（Phase 1 の決定）

| 項目 | 決定 |
| --- | --- |
| OSC Bridge の実体 | `docich vr-osc bridge`（stdlib のみの独立プロセス）。docich 本体の起動・停止に依存しない |
| 実行場所 | 既定は **VRChat と同じ PC**。別 PC の場合は VRChat を `--osc=9000:<bridge を動かす PC の LAN IP>:9001`（公式順序は `inPort:outIP:outPort`）で起動し、bridge 側は `vrchat_host` を VRChat PC の LAN IP、`listen_host` を LAN の待受アドレスにする |
| 責務 | (1) docich 状態 → Parameter 送信 (2) VRChat Parameter → 許可された action の記録 (3) ループ防止・debounce・fail-soft |
| 状態の受け渡し | ファイルのみ。`speaking.json`（実発話状態）と JSONL イベント spool。docich 本体と socket で直結しない |
| 待受 | 既定 `127.0.0.1`。外部公開しない |

docich 側の producer は spool に1行追記するだけ（`docich vr-osc emit ...`）なので、
VRChat や OSC が落ちていても docich 本体の動作には影響しない。

## 3. Avatar Parameter（送信 allowlist）

`config/vrchat_osc.toml` の `[parameters]` にあるものだけを送る。

| Parameter | 型 | 既定 hold | persist | 意味 |
| --- | --- | --- | --- | --- |
| `AI_Talking` | Bool | – | – | docich が実際に発話中。`games/soviet_now/tmp/state/speaking.json` から導出 |
| `AI_Emotion` | Int | 8000 ms | – | 感情。0=平常、1〜3 はアバター側の表情に割り当てる |
| `Alert` | Bool | 3000 ms | – | 通知・アラート。hold 後に自動で false へ戻る |
| `Mode` | Int | – | あり | 人格・モード。0=normal / 1=doushi / 2=capitalism（`[inbound.Mode].values`） |

- **hold**: イベント受信から自動で既定値（Bool=false / Int=0）へ戻るまでの時間。
  0 の場合は sticky（次に変えるまで保持）。
- **persist**: bridge 再起動後も `run/vrchat_osc/state.json` から復元する。
- 型・hold は config で増減できる。`AI_Talking` の導出元パラメータは
  `[speaking].parameter` で指定する（bool である必要がある）。

## 4. docich → VRChat の状態源

1. **実発話状態**: `say_enqueue.sh` が書く `tmp/state/speaking.json`
   （`{"speaking": true, "pid": ...}`）。bridge は presence + `speaking` を見て
   `AI_Talking` を決める。ファイル消滅後 `grace_ms`（既定 500ms）だけ口を閉じるのを遅らせ、
   連続チャンクのちらつきを防ぐ。POSIX では `pid` の生存も確認する。
2. **イベント spool**: `run/vrchat_osc/events.jsonl`。producer は
   `docich vr-osc emit AI_Emotion=2 --hold 3 Alert=true` のように追記する。
   bridge は起動時点より **後** に追記された行だけを読む（古いアラートを再生しない）。
3. **永続状態**: `run/vrchat_osc/state.json`（persist 指定のパラメータのみ）。

## 5. VRChat → docich（受信 allowlist）

`[inbound]` に明示したパラメータだけを受理する。OSC の値がそのままコマンド実行へ
渡ることはない。

| Parameter | 条件 | action | 効果 |
| --- | --- | --- | --- |
| `Mode` | 値が変わり、`values` の範囲内、`min_interval_ms` 経過後 | `mode` | アバター側の選択を docich が採用。`run/vrchat_osc/actions.jsonl` に記録し、`run/vrchat_osc/state.json` へ永続化 |
| `Alert` | false → true の立ち上がり、`min_interval_ms` 経過後 | `notify` | 通知レコードを記録。soren root がある構成では共有オーバーレイにも表示 |

- Bool は**立ち上がりエッジのみ**。押しっぱなし・値の再送では連打しない。
- Int は**値の変化 + クールダウン**でのみ発火する。
- action は `notify` / `mode` の2種類のみ。`[inbound.<name>].action` に他の値を書くと
  設定エラーになり起動しない。
- 記録は `run/vrchat_osc/actions.jsonl`（JSON Lines、4MiB で古い行を間引く）。
  docich 側の consumer や監視はこのファイルを tail できる。

## 6. セットアップ

### 6.1 VRChat 側

1. VRChat の OSC を有効化する（既定で `127.0.0.1:9000` 待受 / `127.0.0.1:9001` 送信）。
   - 同一 PC: 追加設定なし。
   - 別 PC / LAN: VRChat の起動引数は公式順序で `--osc=inPort:outIP:outPort`。
     `outIP` は **bridge を動かす PC**、`outPort` は bridge の `listen_port`（既定 9001）。
     既定構成なら `--osc=9000:<bridge を動かす PC の LAN IP>:9001`。
     - bridge 側は2つを別々に設定する。
       - `listen_host`: LAN の VRChat から届くようにする（既定 `127.0.0.1` のままだと
         LAN からは到達しない。例 `0.0.0.0`、または bridge PC の LAN IP）。
       - `vrchat_host`: VRChat PC の LAN IP（bridge からの送信先）。
     - 外部公開しない。信頼できる LAN 内だけで使う。
2. VRChat の OSC Debug パネルで `/avatar/parameters/...` の受信が見えることを確認する。

### 6.2 アバター（Unity）側

1. **Expression Parameters** に §3 の4つを追加する（Bool / Int）。Expression Parameters に
   無いパラメータは VRChat が転送しない。
2. **FX Animator Controller** にレイヤーを足し、パラメータ条件でアニメーションを切り替える。
   - `AI_Talking` (Bool) … 口パク / 発話エフェクトの ON/OFF。
   - `AI_Emotion` (Int, Equals 0..3) … 表情（BlendShape / アニメーション）切替。
   - `Alert` (Bool) … ランプ点滅・注意モーションなど。
   - `Mode` (Int, Equals 0..2) … 衣装・小物・表示の切替。
3. **Expression Menu** は、アバター側から docich へ送り返したい操作（`Mode` 切替、
   `Alert` 送信ボタン）にだけ使う。受信専用パラメータにはメニューを付けない。
4. VRChat 内で実際にパラメータが変化することを目視確認する（§9）。

> `Alert` をボタンで送る場合、VRChat 側で Bool が自動で false に戻る設定（Expression Menu の
> Toggle ではなく Button + Animator リセット等）にしておくと、docich が false を送り返さずに済む。

### 6.3 docich 側

設定は `config/vrchat_osc.toml`（無ければ既定値）。

```bash
# 設定と allowlist の確認
docich vr-osc status

# 常駐開始（tmux 例）
tmux new-session -d -s vr-osc "cd /path/to/docich && ./bin/docich vr-osc bridge"

# 動作確認（VRChat なし。monitor が VRChat の代わりに 9000 で待つ）
docich vr-osc monitor --port 9000
docich vr-osc send AI_Emotion=2     # 別ターミナル
```

- `--osc-config PATH` / `--soren-root PATH` で上書きできる。
- 環境変数 `DOCICH_VRCHAT_OSC_HOST` / `..._PORT` / `..._LISTEN_HOST` / `..._LISTEN_PORT`
  が config より優先される。
- VRChat PC が Windows の場合は、リポジトリで
  `set PYTHONPATH=src` → `python -m docich.vrchat_osc bridge --repo-root .`
  のように standalone 入口を使える（stdlib のみ・`fcntl` 不要）。

## 7. コマンド

| コマンド | 用途 |
| --- | --- |
| `docich vr-osc bridge` | OSC Bridge を常駐実行（`--duration` でスモーク、`--dry-run` で設定確認） |
| `docich vr-osc emit NAME=VALUE [...] [--hold SEC]` | docich 側イベントを spool へ追記 |
| `docich vr-osc send NAME=VALUE [...]` | bridge を介さず1回だけ送信 |
| `docich vr-osc inject NAME=VALUE [...]` | VRChat からの OSC を模擬注入（受信テスト） |
| `docich vr-osc status [--json]` | bridge の設定・状態・カウンタ表示 |
| `docich vr-osc monitor [--port N]` | VRChat の代わりに OSC を受信して表示（送信テスト） |

## 8. 安全性・ループ防止・フェイルソフト

### ループ防止

1. **送信は変化時のみ**（`_synced` と比較）。同じ値を繰り返し送らない。
2. **エコー抑制**: 自分が直近 `echo_suppress_ms`（既定 400ms）以内に送った値と
   同じ値を受信したら捨てる。
3. **受信の debounce / edge**: Bool は立ち上がりのみ、Int は変化+
   クールダウン。連打・ループで action が増えない。
4. **受信値の採用**: 受信した `Mode` は docich 側にも採用するため、送り返し（バウンス）が
   起きない。

### フェイルソフト

| 事象 | 挙動 |
| --- | --- |
| VRChat 未起動 / 送信先が閉じている | 送信エラーを記録して継続（status の `last_error`）。docich 本体は無影響 |
| `speaking.json` が無い・壊れている | `AI_Talking=false` として扱う。例外を投げない |
| イベント spool の壊れた行 | その行だけ捨てる |
| action sink の失敗（オーバーレイ等） | 記録して継続 |
| 受信ポートが使用中 | 起動時に明確なエラーで終了（bridge 単体の失敗。docich 本体は無影響） |
| OSC パケットが不正 | そのパケットだけ捨て、カウンタ `dropped` に計上 |

### セキュリティ

- 受信は `[inbound]` の allowlist のみ。OSC 入力から任意コマンド・任意パスへは接続しない。
- 値は型ごとに検証し、`Mode` は `values` の範囲外を捨てる。
- 既定で loopback のみ。外部公開しない。

## 9. 受入チェックリスト（Issue #316 の完了条件）

| # | 完了条件 | 状態 |
| --- | --- | --- |
| 1 | docich の状態変化でアバターの見た目が最低1種類変わる | 実装・E2E（UDP/CLI）確認済み。**VRChat 実機の目視は未実施** |
| 2 | アバター操作から docich 側の安全な処理が最低1種類発火する | 実装・E2E（inject → actions.jsonl / state.json）確認済み。**実機は未実施** |
| 3 | 双方向で無限ループ・連打が起きない | エコー抑制・edge/debounce を回帰テストで確認済み |
| 4 | VRChat/OSC が使えなくても docich 本体が正常動作する | 送信失敗・ファイル欠落・不正パケットの fail-soft をテスト済み |
| 5 | 設定方法・Parameter 一覧・起動方法を文書化 | 本書 + ADR + `config/vrchat_osc.toml` |

実機で確認する手順:

1. `docich vr-osc bridge` を起動し、VRChat を起動する。
2. `docich vr-osc send AI_Talking=1` でアバターの口／発話演出が変わることを確認（条件1）。
3. `docich vr-osc emit AI_Emotion=2 --hold 5` で表情が変わり、5秒後に戻ることを確認。
4. VRChat の Expression Menu で `Mode` を切り替え、`run/vrchat_osc/actions.jsonl` に
   `{"action":"mode",...}` が1行だけ増えることを確認（条件2・3）。
5. `Alert` ボタンを連打し、`min_interval_ms` 以上の間隔でしか action が増えないことを確認。
6. VRChat を終了した状態で `docich vr-osc bridge` を動かし、docich 本体
   （`docich status` 等）が正常なことを確認（条件4）。

## 10. 将来案

- 発話内容・感情推定に基づく自動表情（`AI_Emotion` の自動決定）。
- ジェスチャー同期、OBS/配信イベントを共通イベントバスとして OSC へ橋渡し。
- VRChat 内オブジェクト／ワールドギミックとの連携。
- 複数 AI 人格ごとのアバター状態切替。
- 受信 action の拡張（効果音、コーナー操作）は、allowlist に action を追加する形で行う。
