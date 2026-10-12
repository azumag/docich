# 半熟英雄 script bot の層構造

半熟英雄の決定的bot（`brains/hanjuku/bot.py` → `docich.hanjuku_bot.decide`）は、
1万行超の単一モジュール `hanjuku_policy.py` に画面読取・入力生成・チャート戦略・戦闘判断・
メニュー手順が混在していた。改善のたびに無関係な箇所を読む必要があり、どこを直すべきかが
分かりにくかったため、責務ごとの層へ分割した（`bot_version=hanjuku-chart-v183-layered`）。

本PRの第1段（方策の分割）と第2段（残りの半熟モジュールの編入）は **挙動を変えない機械的な移動** であり、
判断・入力・記録は分割前と同一である。

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
| OS層 | `hanjuku/os_layer/pixels.py` / `glyphs.py` / `font.py` | PNG読込、8×8字形の照合による文字読取 |
| OS層 | `hanjuku/os_layer/screen.py` | 1フレームの構造化（文字・手カーソル・マップカーソル・城の屋根・戦闘パネル）と画面種別 |
| OS層 | `hanjuku/os_layer/starter_view.py` | 先発隊画面の名前欄と確認画面の読取 |
| OS層 | `hanjuku/os_layer/phase.py` | フレーム全体の分類（タイトル・戦闘・メニュー・フィールド等） |
| OS層 | `hanjuku/os_layer/readers.py` | 解析済み画面からの読取（メニュー項目と手カーソル、ワールドマップの旗・カーソル、出撃携行札、月次会話、定型語彙） |
| OS層 | `hanjuku/os_layer/world.py` | 観測した世界状態（マップの自己位置推定、城の所有、進軍中の将軍、主人公の城、あたしの家の位置） |
| OS層 | `hanjuku/os_layer/roster.py` | 将軍名簿・卵・賃金の新鮮な観測記録（同一ラン・同月に束縛） |
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
| 手順層 | `hanjuku/flows/house.py` | あたしの家での卵修理、名簿・城収入の巡回 |
| 手順層 | `hanjuku/flows/camp_recheck.py` | 野営・帰還の再確認 |
| 手順層 | `hanjuku/flows/starter.py` | 章開始時の先発隊の選択 |
| 調停 | `hanjuku/orchestrator.py` | `decide`：前景の所有者の優先順位と画面種別による振り分け、入力0件ゲート |
| 共通 | `hanjuku/decision_log.py` / `reporting.py` / `version.py` | 判断記録、状態パネル用の集計、`BOT_VERSION` |

## 改善するときにどこを触るか

| 起きている問題 | 触る層 |
|---|---|
| 画面の種類・文字・カーソル・HPを読み違える | OS層（`phase` / `screen` / `font` / `readers`） |
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

パッケージへ移した旧モジュール（`hanjuku_pixels` / `hanjuku_glyphs` / `hanjuku_font` / `hanjuku_screen` /
`hanjuku_roster` / `hanjuku_house` / `hanjuku_camp_recheck` / `hanjuku_starter`）の旧パスは、移動先モジュールの
`sys.modules` 別名として残す。`import docich.hanjuku_screen` は `docich.hanjuku.os_layer.screen` そのものを返すため、
属性の参照・monkeypatch・`isinstance` はどちらの名前でも同じオブジェクトに効く。層モジュールは旧パスを
import しない（層契約テストで禁止）。

`BOT_VERSION` は `hanjuku/version.py` に移した。挙動を変える変更ではここを更新し、
従来どおり `ops/vm_actions/collect_diagnostics.py` の既知版一覧とテストにも追加する。

## 検証方法

- 分割は生成スクリプトで行い、旧 `hanjuku_policy.py` の全トップレベル文453個が、関数内の相対 import の
  深さを除いて逐語的に一つの層モジュールへ移ったことをASTで照合した（多重集合として完全一致）。
- `decide` は旧版との差分が相対 import の深さと、`policy.chart`（＝`hanjuku_chart`）参照の綴りだけであることを
  確認した。
- 既存の半熟英雄テスト（`tests/*hanjuku*`）は変更なしで全件成功した（変更は `BOT_VERSION` の期待値のみ）。
- 層契約テストは、上位層の import・画面引数・pad生成を混入させた場合に失敗することを確認した。
- 第2段で移したOS層のファイルは import 行以外がバイト単位で同一。手順層の3ファイルは、旧ファサード経由の
  `p.X` 参照を所有する層モジュールからの呼出し時 import に置き換えただけで、`p.X`→`X` と import を正規化すると
  全関数がASTで一致する。あたしの家の位置・HP満タン判定・出撃後の城ステータス判定、先発隊画面の読取は、
  移動先でASTが一致する。
- 旧ファサード経由で `policy.own_camps` 等を差し替える既存テストの意味を保つため、手順層は地図の読取関数を
  `os_layer.readers` の束縛から読む（`screen.parse` 自身の束縛は従来どおり差し替えの影響を受けない）。

## 既存の設計・Issueとの関係

### #1353 戦略とexecutorの分離（`docs/hanjuku_strategy_executor.md`）

オーナー承認済みの #1353 は、半熟英雄botを「戦略＝アプリケーション」「executor＝ゲーム操作OS」の境界で
再構成する計画で、P1として純粋な契約核 `hanjuku_execution.py`（`WorldState` / `StrategyPlan` /
`SortieCommand` / `InputIntent` / `CommandResult`）がmain統合済み・旧bot未接続である。
本構造は、旧botのコードをその境界に沿って並べ直す段であり、次のように対応する。

| #1353 の区分 | 本構造 |
|---|---|
| 戦略（将軍・出撃先・携行札・攻略順・資金配分・回復/撤退/防衛の好み） | 戦略層 `strategy/*`、状況判断は判断層 `judgment/*` |
| executor（指定対象の選択、カーソル移動、割込み、中断、実行結果の確認） | 手順層 `flows/*` ＋ OS層 `commands` / `navigation` |
| 観測（`WorldState`、`SortieEvidence` を作る測定済みobserver） | OS層 `phase` / `readers` / `world` |
| 監督（証拠確定、入力権限、ラン終了・reset、次ランの版選択） | 調停 `orchestrator.py` と既存の `hanjuku_run` / コーナー側 |

ユーザー指示の「OS層」は、#1353 のexecutorのうち画面理解と入力の基本操作にあたる。
複数観測にまたがるメニュー操作は、改善の単位を分けるため手順層として独立させた。

#1353 の注意点は本構造でも維持する。

- `flows.map.target_step` の `order_launched` はA入力を返す時点の記録であり、出撃確認ではない。
- `general_override` / `source_override` / 携行札補正は戦略判断である。判断関数
  （`_order_hero_alternatives`、`_hero_source_alternative`、`_deploy_cards` 等）は戦略層へ置いたが、
  呼び出しと結果の適用はまだ `flows.deploy.deploy_step` の中にある。第3段で、戦略層が確定した
  `SortieCommand` を手順層へ渡す形に変え、手順層の中で代役・札削減を決め直さないようにする。

### #1085 チャート外の動的調整（L0〜L4）

| #1085 | 本構造 |
|---|---|
| L0 基準チャート `hanjuku_chart.py` | 静的データ（全層から参照） |
| L1 非同期LLM調整 `hanjuku_chart_worker.py` / `hanjuku_chart_adjust.py` | 戦略層の外部入力。採用は `strategy/replan.py` |
| L1b 暫定JEV `hanjuku_interim.py` | 候補生成 `strategy/replan.py:interim_candidates`、採用 `_adopt_interim` |
| L2 決定的実行 `map_step` / `month_step` / `deploy_step` | 手順層 `flows/map.py` / `flows/month.py` / `flows/deploy.py` |
| L3 実績履歴 `hanjuku_decisions.jsonl` | `decision_log.py` の判断記録（書出しは `brains/hanjuku/bot.py`） |
| L4 GO後照合 `hanjuku_chart_review.py`、終了後改善 `hanjuku_improve.py`（#954） | 層の外（ラン終了後のみ） |

#954 / #1353 P6 の改善ループは「戦略のみ／executorのみ／両方」の比較を求めている。層が分かれたことで、
改善候補がどの層を変えるかを差分のパスで判定でき、比較評価の単位にできる。

### 未解決の半熟英雄Issueの層への対応

| Issue | 問題 | 主に触る層 |
|---|---|---|
| #2040 卵なし城防衛の召喚前救済がHP期限だけでは間に合わない | 救済を始める時期の判断 | 判断層 `judgment/battle.py`（`_defender_egg_window` / `_defender_last_resort`）。召喚選択中の識別はOS層 `readers` |
| #2076 クイーン戦の将軍コマンドで下入力が効かない | メニュー所有者と入力受付区間、効かない入力の検出 | 手順層 `flows/battle_menus.py` とOS層 `commands`（`_battle_menu_to`）。#1353 の「入力意図・送信・効果の分離」を適用する候補 |
| #1996 コンサート画面でB連打ループ | 画面分類と既定入力 | OS層 `os_layer/phase.py`（`classify` の concert 判定）と調停 `orchestrator.legacy_actions` |
| #1553 青ゲージの画素検出と卵落札携行 | 画素の読取と携行札の計画 | OS層 `readers` と戦略層 `strategy/kit.py` |
| #1608 夏イベントの応答文が未読の場合の復旧 | 未読の応答と復旧手順 | 手順層 `flows/prompts.py`（`summer_bonus_*`）とOS層 `readers` |

### 並行中のPRとの関係

- **#2058**（交換二択の検証）: `hanjuku_policy.trade_guard_step` と `hanjuku_bot.decide` を変更している。
  本構造の後にマージする場合、変更先はそれぞれ `flows/prompts.py` と `orchestrator.py`。
  テストは互換ファサード経由（`policy.trade_guard_step`、`monkeypatch.setattr(policy, ...)`）のままで動く。
  先にマージされた場合は、本分割を新しいmainから生成し直す（分割は機械的な移動のため再生成で追従できる）。
- **#1915 / #1469**（active runへのhot-load境界）: logic generation を `hanjuku_bot.py` / `hanjuku_run.py` の
  ソースのバイト列から求める設計になっている。分割後の `hanjuku_bot.py` はファサードであり、`classify` /
  `decide` / `BOT_VERSION` の変更はそのバイト列に現れない。hot-load境界を本構造の後に入れる場合は、
  `src/docich/hanjuku/` 全体を世代計算の対象に含める必要がある（`hanjuku_run` が使う `classify` は
  `os_layer/phase.py`、画素読込は `os_layer/pixels.py` にあり、旧パスのファイルは別名・ファサードにすぎない）。

## 既知の負債と次の段階

第1段は「置き場所」を分けただけで、層の間は依然として共有の方策メモリ `mem`（辞書）でつながっている。
次の段階で順に解消する。各段階は挙動を変えない変更と挙動を変える変更を混ぜない。
第3段以降は #1353 の P2〜P6 と同じ作業であり、別の契約を新設せず `hanjuku_execution.py` の契約を使う。

1. **残りのモジュールの編入**（挙動不変、**本PRで実施済み**）: あたしの家・野営再確認・先発隊を `flows` へ、
   画面読取（`screen` / `font` / `glyphs` / `pixels`）と名簿観測（`roster`）を `os_layer` へ移した。
   `strategy.economy` → `hanjuku_house` の依存は、家の位置等の世界状態を `os_layer.world` へ移して解消し、
   `KNOWN_DEBT` は空になった。画像評価の `PARSER_FILES` は移動先の実ファイルを指す。
   `ops/vm_actions/gateway.py` の `DIAGNOSTICS_FILES` にある `hanjuku_screen.py` / `hanjuku_policy.py` は
   collectorが import しない改変検査対象で、旧パスのファイルは別名として残るため変更していない。
2. **状態の分離**（挙動不変）: `mem` を、世界状態（OS層が書く観測。将来の `WorldState` の入力）、
   計画（戦略層が書く注文・予算。`StrategyPlan`）、戦闘記録（判断層が読む）、手順の進行状態
   （手順層だけが持つ。`Session.executions` に相当）に分け、他層の状態を書き換えないようにする。
   永続化済みの方策メモリとの互換（移行関数）を伴う。
3. **意図と判断の型**（#1353 P2・P4）: 戦略層は出撃を `SortieCommand` として確定し、手順層
   （`flows.deploy` / `flows.map`）はそれを測定済みdriverとして実行して `CommandResult` を返す。
   代役・出撃元変更・札削減は戦略層で決め、手順層は黙って行わない。入力意図・送信・効果を分け、
   #2076 のような「送ったが効かない入力」を手順層で検出する。購入・回復・戦闘・撤退も同じ形で小さく移す。
4. **テストの移行とファサードの縮小**: テストを層モジュールへ直接向け、層ごとの単体テスト
   （判断層は画面なしの状態だけで検証できる）を増やす。参照がなくなった時点でファサードを削除する。
5. **層単位の改善ループ**（#1353 P5・P6、#954）: 終了後の原因分類を層（観測／手順／判断／戦略）に対応させ、
   改善候補が変更する層を明示して、戦略のみ・executorのみ・両方を比較評価する。
