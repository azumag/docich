# VERIFY-5458 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: バルムンク (ch4 戦闘)

対象: docich#5458（`bug` / `auto-generated` の監査自動起票）
ラン `g1342-1fe0734c` / generation `1342` /
`hanjuku-chart-v1481-boss-camp-recall-followup` / ch4・月 `9-2`
fingerprint `d313b63dca4145a4` / occurrence `68701623817c12c11af0` / 時刻 `17:02:28Z`
未使用の携行札: **バルムンク** / 戦闘: 不明 vs 不明 / 城: 不明 / 画面: `None`
理由: 敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ

調査基盤: `/home/ubuntu/repos/docich`（`origin/main` = `54e6c11c3` =
`hanjuku-chart-v1484-clear-ending-corner-end`、`version.py:7` 実測）。
read-only 検証: 本番 SSH なし / ラン成果物 `hanjuku_decisions.jsonl` 等は VM 側で
直接参照せず / 秘密情報なし / コード変更なし（成果物は本ファイルの追加のみ）。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1）。

## 結論: (a) 実欠陥

#5259/#5322/#5328/#5331/#5352/#5362/#5373/#5381/#5398 と同一機構クラスの
召喚獣戦 card_miss で、現行 main（v1484）にも生存する。ただし本件の札は
**ボス専用のバルムンク**であり、系列で初めて「使用直前ゲートが関係する札」の
発火である。起票が成立した経路は 2 通りあり、どちらの経路でも現行コード上の
実欠陥が存在する:

- **経路1（ボス戦）**: 四季王城のボス突入戦中の敵召喚獣戦なら
  `measured_boss_entry` でバルムンクは使用可能（`BOSS_ONLY` 制限解除）。
  エッグモンスター即死（monster 255 / effect `エッグモンスター即死`）の札を
  残したまま `こうげき` を確定したのは系列と同一のゲート穴による実欠陥。
- **経路2（ボス出撃の行軍中・非ボス戦）**: 非ボス戦ではバルムンクは使用直前
  ゲート（`boss_only_balmung`）で正しく拒否される＝ゲームプレイ上は温存が
  設計どおり。にもかかわらず `battle_boss_card_missed` が起票されるのは、
  emit が `_confirmed_carried_kit` を無差別に数え**使用可否
  （`BOSS_ONLY`/`_boss_tactics_allowed`）を考慮しない**検出側の欠陥。

本 issue 単体では経路1か経路2かを確定できない（城・敵・side が投影欠落のため）。
ただし系列 VERIFY と同じく「携行確定札を残して攻撃確定した」記録上の事実は
起票条件どおりであり、検出器は仕様どおり実事象を報告した（誤検知ではない）。
どちらにせよ現行 main に対応する欠陥が存在するため結論は (a)。

## 根拠となるコード箇所（main = `54e6c11c3` = v1484 の行番号）

### 発火遷移（emit サイトは全コード中 1 箇所のみ）

発火関数は `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py:2802`）。
`orchestrator.py` が `kind == 'egg_battle_menu'` で振り分ける章非依存の汎用パス
（系列 VERIFY と同一）。

- `battle_menus.py:3104-3111` — `cards_left` は `planned_cards` 優先、空なら
  `_confirmed_carried_kit`（`judgment/battle.py:989`、出撃確定
  `departure_binding` − 確定使用）で埋める。issue の「未使用の携行札:
  バルムンク」は確定携行札（または計画携行札）。監査側も
  `_carried_card_left`（`battle_menus.py:1251-1266`）で同じ帳簿を見る。
- `battle_menus.py:3116-3126` — 揭示ゲート。発火には
  `cards_left and 'きりふだ' not in screen.text and
  ('たまごをつかう' in screen.text or empty_egg) and not card_flow`
  がすべて必要。穴（系列 VERIFY と同一）: 灰色・使用済み・未所持の卵行は白
  OCR（`screen.text`）に載らず（`os_layer/screen.py` の実測コメント通り）、
  `empty_egg`（`:2937`）も `_screen_egg_empty`（`:2309`）が観測済み整数 0
  等の限定条件でのみ立つため、**卵行欠落画面はこのゲートの前提を満たさず**
  scroll せず `こうげき` 確定へ直行しうる。`SUMMON_CARD_SCROLL_LIMIT`（`:3332`）。
- `battle_menus.py:3131-3155` — `きりふだ` が実表示された場合のみ list を開く
  fail-closed の開放ゲート（系列と同一構造）。
- `battle_menus.py:3257-3276` — `move == 'here' and label == 'こうげき'`
  のときだけ `missed = _carried_card_left(...)` を評価し、
  `missed and cards_left and not cards_used` で
  `battle_boss_card_missed` を 1 戦闘 1 回 emit（`:3269`、grep で唯一の emit
  サイト）。理由文は固定テンプレート。

### バルムンクの携行・使用条件（本 issue 固有）

- `hanjuku_reference.py:70` — `バルムンク` id 16、general 16 / monster 255 /
  boss 250 / egg_drop **5** / effect `エッグモンスター即死`。
- `hanjuku_reference.py:177` — `BOSS_ONLY_CARDS = ('バルムンク',)`
  （owner 2026-11-06「バルムンクはボス専用」。g1153 ココットが非ボス戦で
  失った事案が由来）。
- **携行経路はボス注文に限られる**:
  - `_auto_carry_safe_card`（`strategy/kit.py:117-146`）は egg_drop == 0 の
    札のみ自動携行する → バルムンク（egg_drop 5）は対象外。
  - `_strong_card_inventory` / `_dropper_card_inventory`（`flows/deploy.py`）
    は `boss_card_reserved`（`strategy/boss_cards.py:210-232`）を通り、
    非ボス注文では BOSS_ONLY を「supplement 不可」で除外する。
  - `CHAPTER_4_ORDERS`（`hanjuku_chart.py:671-716`）のどの注文も
    バルムンクを `cards` に持たない。
  - `_strong_card_names`（`deploy.py:480-496`）は chapter == 4 では
    `STRONG_CARDS`（クースカン/ミックミー/マグネガキン/ノリウツール）を
    返し、バルムンクを含まない。
  - 残る携行経路は `_boss_balmung_kit`（`deploy.py:188-`）のみ:
    ボス注文（`_is_boss_order` は `boss_sites(4)` = 四季王城4サイトを含む）に
    実在庫のバルムンク × min(stock,2) を先頭に足す `rare_card_kit`。
    **本件の出撃は 4-B1〜4-B4（四季王城）のボス注文であった可能性が高い**。
- **使用可否ゲート**: `_boss_tactics_allowed`（`judgment/battle.py:59-88`）—
  `entry_evidence == 'measured_boss_entry'`、または敵が章ボス
  （ch4 = 四季王、`hanjuku_chart.py:1197` `BOSSES[4]`）かつ現注文がボス注文
  のとき BOSS_ONLY を許可。ボス突入文ハンドラ（`flows/battle.py:2050-2097`）が
  `measured_boss_entry` を立てる。非該当では `judgment/battle.py:536-538` が
  `role='boss_only_balmung'` で使用を拒否する。
- **emit 側は使用可否を見ない**: `cards_left`/`missed` は `_confirmed_carried_kit`
  を無差別に数え（`battle_menus.py:3109-3111`、`_carried_card_left:1251-1266`）、
  `_planned_battle_cards` が BOSS_ONLY を除く処理（`flows/battle.py:660`）は
  戦闘計画だけで監査 emit には届かない。→ 非ボス戦でも card_miss が起票されうる。
- `orders.py:2342-2353` — ボス注文でバルムンク携行を確認した場合は
  `rare:{step}:バルムンク` tactic「実携行を確認したバルムンク(ボス250)を
  ボスへ開幕で使用」が計画される（ボス戦での使用意図がチャート上明記される）。

### 監査投影（`month_audit.py`）

- `_track_card_miss`（`:3236-3300`）: `battle_boss_card_missed` decision を
  incident 化（incident_id L3263-3264、fingerprint L3290-3292、auto_file）。
- `_render_card_miss`（`:3700-3740`）: `ally`/`enemy`/`castle`/`screen` は
  `_record` に渡されないため `不明`/`None`（投影欠落、系列 VERIFY と同一）。
  「卵なし・奥の手不可」は静的テンプレート文（`:3729`）で実測ではない。
- fingerprint 再計算: `sha256("4|card_miss|バルムンク+")[:16]` =
  `d313b63dca4145a4` で issue と一致（emit が castle を渡さないため
  第3要素は空。`_fingerprint` `:3105`）。
- `battle_boss_card_missed` の `observed_metric` には `cards_left`/`screen`/
  `text`/`scrolls`/`cursor` があるが issue へは投影されない（系列と同一）。

### PR #5347（v1452、is_boss_castle 条件付きフォールバック）との関係

- **未マージ**（2026-11-25 時点 OPEN）。v1484 のゲートに `own_attack_menu`/
  `enemy_summon_menu` は存在しない（grep 実測、`('たまごをつかう' in
  screen.text or empty_egg)` のまま）。
- #5347 のフォールバック条件は `is_boss_castle(castle)` ・
  `side == 'attack'`・`general_reading`・認識済み召喚獣1体・`こうげき`
  表示の同時成立。`is_boss_castle`（`hanjuku_chart.py:1401-1403`）は
  `BOSS_CASTLES.values()` に加え `boss_sites(4)` = 四季王城4サイトを含む
  → **本件が四季王城での発火なら #5347 の対象範囲内**であり、マージされれば
  本件型は救える可能性が高い（系列で「非ボス城の可能性が残る」とされた
  VERIFY-5381/5398 よりカバー率が高い）。
- 行軍中の非ボス城・防衛側（`mixed_item_box` 経由の `egg_battle_menu`）での
  発火なら #5347 対象外（系列と同一の注意）。

### v1481（観測版）→ v1484（検証ベース）の差分

v1481→v1484 の間に本経路を修正した版はない。期間中の変更は
#5460（監査 `_sortie_carried_cards` の未観測/実測空区別 — `card_miss` の
emit/投影とは別信号 `sortie_kit_unobserved`）、v1483（ch12 出撃順）、
v1484（クリア後コーナー終了）、#5441（出撃失敗バインド）で、
`egg_battle_step` の scroll gate・`battle_boss_card_missed` emit・
`BOSS_ONLY` ゲートは不変（`battle_menus.py` の直近変更は #5422 の
5項目リスト境界で本経路に無関係）。

## #5398・#5394 系との異同

- **同**: 起票サイト（`battle_menus.py:3269` 唯一）、理由文、投影欠落パターン
  （戦闘/城 不明、画面 None、fingerprint 形式）、同一ラン g1342 の姉妹 issue
  （#5362 ch3/バグストームは g1342 の初発火。本件は同ラン ch4）。
- **異**: 本件の札は **バルムンク = ボス専用**（系列は全て通常切り札:
  バグストームは自爆札だが携行経路はボス注文、ノリウツール/イッテツーン等は
  汎用）。よって「使用したかったのに開けなかった」（系列の機構）だけでなく
  「使用が禁じられていたのに起票された」（検出側の経路2）の可能性が新たに生じる。
  また #5394 は代表 #5362 へ集約済み（同型の集約ルールは確立済み）。
- **異**: ch4 では `is_boss_castle` が四季王城を認めるため、発火がボスサイト
  戦闘なら #5347 のカバーに入りうる点は ch12 の #5398（非ボス注文の自動携行が
  最有力で対象外の可能性大）より有利。

## 証拠が薄い点 — (d) 要素として残す部分

- **経路1（ボス戦）か経路2（非ボス戦）か**: issue の城・敵・side は投影欠落。
  確定にはラン成果物が必要: `g1342-1fe0734c` の `hanjuku_decisions.jsonl` で
  時刻 17:02:28Z 前後の `battle_boss_card_missed` 行の `observed_metric`
  （`cards_left`/`screen`/`text`/`scrolls`/`cursor`）および直前の
  `battle_start`/`egg_battle`/`attack_observed` 行（enemy/ally/castle/side/
  `entry_evidence`）。
- **scroll-0 型か scroll 枯渇型か**: `observed_metric.scrolls` は記録されるが
  issue へ投影されない。
- **バルムンクの携行経路の特定**: `_boss_balmung_kit`（rare_card_kit）か
  ボス注文での dropper/override かは `departure_binding`/`rare_card_kit`
  レコードが無いと確定できない（ただしいずれもボス注文であることは
  上述の絞り込みで確定的）。
- **「敵召喚獣戦」の分類**: reason 文は固定で、敵が将軍でも同じ文で発火しうる
  （系列 VERIFY と同一の注意）。

## 判定詳細

- **(a) 実欠陥**: 確定携行札（帳簿上のバルムンク）を残して `こうげき` を
  確定した記録上の事実に基づく起票で、対応する欠陥が現行 main に存在する
  （経路1: 揭示ゲートの穴、経路2: emit の BOSS_ONLY 未考慮）。
- **(b) ではない**: マージ済み修正はどれもこの発火を塞がない
  （#5347 は未マージ、v1481→v1484 の変更は本経路無関係）。
  経路2 でさえ検出側の欠陥が残る。
- **(c) ではない**: 起票条件は実測の携行・未使用・攻撃確定に基づく。
  ただし経路2 が成立していた場合、ゲームプレイ上は温存が設計どおりであり、
  実害は「誤起票」に限定される — その場合の欠陥は `battle_boss_card_missed`
  emit が使用可否（`BOSS_ONLY`・`_boss_tactics_allowed`）を見ない点に移る。
- **(d) 要素**: 経路1/2 の切り分け、scroll-0 vs 枯渇、携行経路の特定は
  上記のラン成果物が必要（issue 単体では判定不能）。

## 修正の方向（実装はしない）

1. **emit に使用可否フィルタを入れる**: `battle_boss_card_missed` の
   `missed`/`cards_left` 評価で `BOSS_ONLY_CARDS` を
   `not _boss_tactics_allowed(mem, battle)` のとき除外する
   （`_carried_card_left` または emit 直前）。経路2 の誤起票を止める。
   `planned_cards` 側と同じ帳簿規則（`flows/battle.py:660` と同条件）を
   監査側にも適用する。
2. **#5347 を現行 main へ rebase/衝突解消してマージする**: 卵行 OCR 欠落型の
   主機構を塞ぐ。ch4 は四季王城が `is_boss_castle` に含まれるため経路1
   （ボス戦での真の未使用）には直接効く。
3. **証拠投影の完成（系列 VERIFY と同じ）**: emit の `observed_metric` に
   `ally`/`enemy`/`castle`/`side`/`entry_evidence` と issue への
   `scrolls`/`cursor` 投影を追加し、経路1/2 の切り分けを起票時点で可能にする。

## 検証で使った根拠（抜粋・main = `54e6c11c3` = v1484）

- `src/docich/hanjuku/flows/battle_menus.py`: `_carried_card_left` L1251-1266、
  `_white_egg_available` L2297、`_screen_egg_empty` L2309、
  `egg_battle_step` L2802、interrupted card_flow 再開 L3086-3101、
  `cards_left` L3104-3111、owner 意図コメント L3113-3115、
  scroll gate L3116-3126、open gate L3131-3155、emit L3257-3276、
  `SUMMON_CARD_SCROLL_LIMIT` L3332
- `src/docich/hanjuku/judgment/battle.py`: `_boss_tactics_allowed` L59-88、
  `boss_only_balmung` L536-538、`_battle_labels` L90-93、
  `_confirmed_carried_kit` L989-1001、`_own_egg_empty` L2815
- `src/docich/hanjuku/flows/battle.py`: `planned_cards` 生成 L782、
  `_planned_battle_cards` BOSS_ONLY 除去 L660、ボス突入文ハンドラ
  L2050-2097（`measured_boss_entry` L2071/L2092/L2096）
- `src/docich/hanjuku/flows/deploy.py`: `_note_balmung_stock` L154-170、
  `_boss_balmung_generic`/`_boss_balmung_kit` L173-、
  `_strong_card_names` L480-496（chapter == 4 は STRONG_CARDS）
- `src/docich/hanjuku/strategy/boss_cards.py`: `boss_card_reserved` L210-232
  （BOSS_ONLY は非ボス supplement 不可）
- `src/docich/hanjuku/strategy/kit.py`: `_auto_carry_safe_card` L117-146
  （egg_drop == 0 のみ）
- `src/docich/hanjuku/strategy/orders.py`: L2342-2353（バルムンク携行ボス注文の
  rare tactic）
- `src/docich/hanjuku/month_audit.py`: `_fingerprint` L3105、
  `_track_card_miss` L3236-3300、`_render_card_miss` L3700-3740
- `src/docich/hanjuku_chart.py`: `BOSS_CASTLES` L455-459（ch4='ボス'）、
  `boss_sites` L1393-1398、`is_boss_castle` L1401-1403、`BOSSES[4]`=四季王
  L1197-1201、`CHAPTER_4_ORDERS` L671-716（バルムンク携行注文なし）
- `src/docich/hanjuku_reference.py`: バルムンク L70（id 16、egg_drop 5、
  エッグモンスター即死）、`STRONG_CARDS` L160、`BOSS_DAMAGE_CARDS` L171、
  `BOSS_ONLY_CARDS` L177、egg_monster 即死評価 L1345
- PR #5347: OPEN・未マージ（`own_attack_menu` は main に存在しない、grep 実測）
- 関連 VERIFY（main 上）: VERIFY-5398/5381/5373/5362/5331/5328/5322/5259
  （全て (a)・同一機構クラス）、VERIFY-5452（同ラン g1342・ch4・(a)）
- 関連 issue: #5398（ch12/ノリウツール、(a)）、#5394（#5362 へ集約で closed）、
  #5362（ch3/バグストーム、同一ラン g1342、(a)）

## 手順上の注記

- read-only 検証。VERIFY-5458.md の追加以外のコード変更・本番 VM 操作なし。
- テスト実行は未実施（静的照合のみ。関連テストは
  `tests/test_hanjuku_chart_bot.py` の card_miss 起票/不起票系と
  `tests/test_hanjuku_month_audit.py` の seal テストが対応）。
- fingerprint は再計算で一致を確認（`sha256("4|card_miss|バルムンク+")[:16]`
  = `d313b63dca4145a4`）。
- `agent-guards/secret_scan.py` は本環境に未配置のため、投稿物はローカルの
  秘密パターン走査で確認済み（clean）。
