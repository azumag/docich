# 半熟英雄 script bot の層構造

半熟英雄の決定的bot（`brains/hanjuku/bot.py` → `docich.hanjuku_bot.decide`）は、
1万行超の単一モジュール `hanjuku_policy.py` に画面読取・入力生成・チャート戦略・戦闘判断・
メニュー手順が混在していた。改善のたびに無関係な箇所を読む必要があり、どこを直すべきかが
分かりにくかったため、責務ごとの層へ分割した（`bot_version=hanjuku-chart-v183-layered`）。

この段階（第1段）は **挙動を変えない機械的な分割** であり、判断・入力・記録は分割前と同一である。

## 層と依存の向き

```
orchestrator   調停: このフレームをどの手順が扱うか、入力0件の有限ゲート
    │
flows          手順層: 複数観測にまたがるメニュー手順（出撃・月次・戦闘メニュー…）
    │
judgment       判断層: 状況判断（切り札の撃破判定・救済・卵・撤退・イベント回答）
    │
strategy       戦略層: チャート注文・携行札・計画外の再計画・月次予算
    │
os_layer       OS層: 画面理解（phase / readers / world）とコマンド実行（commands / navigation）

decision_log   判断記録（全層から利用可）
```

上位の層は下位の層だけを import できる。同じ層の中のモジュール同士は参照してよい。
`tests/test_hanjuku_layers.py` が次を検査する。

- 層の向き（下位→上位の import、互換ファサードへの依存を禁止）。
- 戦略層・判断層の関数は `screen` / `frame` を受け取らず、`pad` 入力を作らない。
  画面を読む処理はOS層、入力を出す処理は手順層へ置く。
- 既知の違反は `KNOWN_DEBT` に理由付きで列挙し、減らす方向にだけ更新する。
  新しい違反はテスト失敗、解消した違反を一覧に残してもテスト失敗になる。

## モジュール一覧

| 層 | モジュール | 責務 |
|---|---|---|
| OS層 | `hanjuku/os_layer/phase.py` | フレーム全体の分類（タイトル・戦闘・メニュー・フィールド等） |
| OS層 | `hanjuku/os_layer/readers.py` | 解析済み画面からの読取（メニュー項目と手カーソル、ワールドマップの旗・カーソル、出撃携行札、月次会話、定型語彙） |
| OS層 | `hanjuku/os_layer/world.py` | 観測した世界状態（マップの自己位置推定、城の所有、進軍中の将軍、主人公の城） |
| OS層 | `hanjuku/os_layer/commands.py` | pad入力の生成、見えているラベルへの手カーソル移動 |
| OS層 | `hanjuku/os_layer/navigation.py` | 目標セルへのマップカーソル移動、停滞検出、Yジャンプ、屋根合わせ |
| 戦略層 | `hanjuku/strategy/orders.py` | チャート注文の選択、出撃元・代役・予約、最後の城の保持 |
| 戦略層 | `hanjuku/strategy/kit.py` | 出撃の携行札、ボスキット、チャート戦術一覧 |
| 戦略層 | `hanjuku/strategy/replan.py` | 計画外の暫定注文・調整チャートの採用 |
| 戦略層 | `hanjuku/strategy/economy.py` | 月次予算（兵士・卵回復・修理・登用・築城・商人の予備金） |
| 戦略層 | `hanjuku/strategy/campaign.py` | 章の進行検出、城ラベルの正規化 |
| 判断層 | `hanjuku/judgment/battle.py` | 切り札の撃破判定、卵落・卵の脅威、救済・撤退の要否、白兵の優劣、防衛の最終手段 |
| 判断層 | `hanjuku/judgment/events.py` | 交換将軍の受諾判断、モンスター技の判定 |
| 手順層 | `hanjuku/flows/name.py` | 名前入力 |
| 手順層 | `hanjuku/flows/map.py` | フィールドマップ・出撃先選択・ワールドマップ |
| 手順層 | `hanjuku/flows/recall.py` | 野営からの帰還指示 |
| 手順層 | `hanjuku/flows/deploy.py` | 城メニュー・将軍一覧・切り札選択・出撃確認 |
| 手順層 | `hanjuku/flows/battle.py` | 戦闘パネルの読取と戦闘ループ、戦闘終了の記録 |
| 手順層 | `hanjuku/flows/battle_menus.py` | 戦闘コマンド・卵・切り札一覧・奥の手・モンスターのメニュー |
| 手順層 | `hanjuku/flows/month.py` | 月次メニューと購入・登用・築城の取引 |
| 手順層 | `hanjuku/flows/egg_recovery.py` | 月次の卵回復取引 |
| 手順層 | `hanjuku/flows/shop.py` | 商人の一覧・個数入力・兵士解雇 |
| 手順層 | `hanjuku/flows/prompts.py` | 交換・はい/いいえ・夏のボーナス・ギフト等のイベント文 |
| 調停 | `hanjuku/orchestrator.py` | `decide`：前景の所有者の優先順位と画面種別による振り分け、入力0件ゲート |
| 共通 | `hanjuku/decision_log.py` / `reporting.py` / `version.py` | 判断記録、状態パネル用の集計、`BOT_VERSION` |

## 改善するときにどこを触るか

| 起きている問題 | 触る層 |
|---|---|
| 画面の種類・文字・カーソル・HPを読み違える | OS層（`phase` / `readers`、文字は `hanjuku_screen` / `hanjuku_font`） |
| カーソルが目標に届かない、マップで迷う | OS層（`navigation` / `world`） |
| 出撃する将軍・行き先・携行札・買う量が悪い | 戦略層（`orders` / `kit` / `economy` / `replan`） |
| 戦闘で切り札・卵・撤退の選択が悪い | 判断層（`judgment/battle.py`） |
| 選んだはずの操作が実行されない、メニューで止まる・往復する | 手順層（`flows/*`） |
| どの手順がフレームを扱うかの優先順位が悪い | 調停（`orchestrator.py`） |

判断の誤りを直すために手順層を、メニュー停滞を直すために判断層を書き換えないこと。
両方にまたがる場合は、判断層が返す値（判断）と手順層の実行を分けて修正する。

## 互換ファサード

`docich.hanjuku_policy` と `docich.hanjuku_bot` は互換ファサードとして残す
（`hanjuku/_compat.py`）。

- 旧モジュールで定義されていた全ての名前を再公開する（`hanjuku_house` などの `p.menu_to` 呼び出しや
  既存テストの `from docich.hanjuku_policy import battle_step` はそのまま動く）。
- ファサード上で名前を再束縛すると（`monkeypatch.setattr(policy, 'nav_step', ...)`）、同じ
  オブジェクトを保持している全ての層モジュールへ伝播する。単一モジュールだった時と同じ意味になる。
- 層モジュールはファサードを import しない（層契約テストで禁止）。新しいコードとテストは、
  名前を定義している層モジュールを直接 import する。

`BOT_VERSION` は `hanjuku/version.py` に移した。挙動を変える変更ではここを更新し、
従来どおり `ops/vm_actions/collect_diagnostics.py` の既知版一覧とテストにも追加する。

## 第1段の検証方法

- 分割は生成スクリプトで行い、旧 `hanjuku_policy.py` の全トップレベル文453個が、関数内の相対 import の
  深さを除いて逐語的に一つの層モジュールへ移ったことをASTで照合した（多重集合として完全一致）。
- `decide` は旧版との差分が相対 import の深さと、`policy.chart`（＝`hanjuku_chart`）参照の綴りだけであることを
  確認した。
- 既存の半熟英雄テスト（`tests/*hanjuku*`）は変更なしで全件成功した（変更は `BOT_VERSION` の期待値のみ）。
- 層契約テストは、上位層の import・画面引数・pad生成を混入させた場合に失敗することを確認した。

## 既知の負債と次の段階

第1段は「置き場所」を分けただけで、層の間は依然として共有の方策メモリ `mem`（辞書）でつながっている。
次の段階で順に解消する。各段階は挙動を変えない変更と挙動を変える変更を混ぜない。

1. **残りのモジュールの編入**: `hanjuku_house`（あたしの家）、`hanjuku_camp_recheck`、`hanjuku_starter` を
   `flows` へ、`hanjuku_screen` / `hanjuku_font` / `hanjuku_pixels` / `hanjuku_roster` を `os_layer` へ移す。
   `strategy.economy` → `hanjuku_house` の依存（`KNOWN_DEBT`）は、家の画面定数をOS層へ移して解消する。
   VMの診断・画像評価が参照するファイル一覧（`ops/vm_actions/gateway.py` の `DIAGNOSTICS_FILES`、
   `evaluate_hanjuku_vision.py` の `PARSER_FILES`）を同時に更新する。
2. **状態の分離**: `mem` を、世界状態（OS層が書く観測）、計画（戦略層が書く注文・予算）、
   戦闘記録（判断層が読む）、手順の進行状態（手順層だけが持つ）に分け、他層の状態を書き換えないようにする。
   永続化済みの方策メモリとの互換（移行関数）を伴う。
3. **意図と判断の型**: 戦略層は「出撃」「購入」などの意図、判断層は「この札を使う」「撤退する」などの判断を
   値として返し、手順層はそれを受け取って実行する。手順層の中で戦略・判断を再計算しない。
4. **テストの移行とファサードの縮小**: テストを層モジュールへ直接向け、層ごとの単体テスト
   （判断層は画面なしの状態だけで検証できる）を増やす。参照がなくなった時点でファサードを削除する。
