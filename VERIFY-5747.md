# VERIFY-5747 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: バグストーム (ch1 けっかい)

- 対象: [docich#5747](https://github.com/azumag/docich/issues/5747) / 監査 `card_miss` / confidence `high`
- fingerprint `81f724e25a441a88` / occurrence `5aa4f1ef4d8383cca708`
- ラン: `g1373-b4c4126f` / generation `1373` / `hanjuku-chart-v1581-move-arrival-observed`
- ch1 / 月 4-2 / 時刻 2026-11-27 11:39:45Z / side `attack`
- 戦闘: ヴィーナス vs クイーン / 城: けっかい
- 画面: `battle_end` / scrolls・cursor・rearms・表示文言は不明 / card_flow `なし`
- 卵状態・敵召喚観測: unknown
- 検証ベース: `origin/main` = `868e45258`（`BOT_VERSION 'hanjuku-chart-v1582-mechanical-boss-purpose'`）
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は公開 issue から辿れず、issue 本文と現行コードの静的照合で判定した。
- `DOCICH_HANDOFF_PATH` 未設定のため handoff 正本は未読（AGENTS.md §1）。

## 結論: **(d) 判定不能 — バグストーム不使用が「生存/消耗を考えた正しい温存」だったのか、「開封・選択すべき状況で実行経路が動かなかった実欠陥」だったのかを、emit に残る証拠だけでは分けられない**

本件は `scrolls=3` 型や frozen `card_flow` 型ではなく、`battle_end` で戦闘終了後に起票される新しい generic 経路である。発火した事実（ボス戦終了時にバグストームが未使用だったこと）は emit 条件として確認できるが、**不使用が問題だったかどうか**を決める HP・戦闘結果・敵召喚有無・瀕死状態・実カード一覧・ゲート拒否履歴が issue/emit のどちらにも残っていない。したがって実欠陥とも設計どおりとも誤検知とも断定できない。

## 1. fingerprint / occurrence の照合

現行 `month_audit.py` の式で再計算でき、issue 記載値と一致する。

- fingerprint: raw `1|card_miss|バグストーム+けっかい` → sha256 先頭16 `81f724e25a441a88`
- occurrence: 時刻 2026-11-27 11:39:45Z は epoch `1795779585`。raw `g1373-b4c4126f|1|1795779585|バグストーム` → sha256 先頭20 `5aa4f1ef4d8383cca708`

## 2. 発火経路は battle_end の generic emitter

`src/docich/hanjuku/flows/battle.py` では、`_boss_card_left_unused` がボス性と未使用札だけを見る。

- `battle.py:1735-1742` — owner 2026-11-26 の意図として、召喚メニューを通らない白兵のボス戦でも「携行切り札を使わずに戦闘が終わったか」を記録する。
- `battle.py:1743-1755` — 既記録・`cards_used`・ボス判定だけを見る。ボス判定は敵名・boss latch・ボス城のいずれかでよい。
- `battle.py:1760-1768` — `_carried_card_left` または `planned_cards` から未試行札を返す。
- `battle.py:2039-2050` — battle_end で `battle_boss_card_missed` を記録する。`observed_metric` は `cards_left` と `screen: battle_end` だけで、戦闘結果・HP・敵召喚・瀕死状態・カード一覧・ゲート拒否・開封履歴を保持しない。

監査側では `src/docich/hanjuku/month_audit.py:3655-3739` がこの decision を受けると無条件に `confidence: high`、`confidence_reasons: ['carried_card_unused_in_boss_or_summon_battle']`、`auto_file: True` で起票する（特に `month_audit.py:3715-3737`）。したがって issue の `high` は、この戦闘で使用が実際に必要だったことを独立に証明した値ではない。

## 3. バグストームは通常の「使うべき強札」と同じには扱えない

`src/docich/hanjuku_reference.py` 上のバグストームは両軍半減の自傷札である。

- `hanjuku_reference.py:92-94` — バグストームは `boss_damage: 50` だが、効果は「両軍の将軍、兵士のHP半減」。
- `hanjuku_reference.py:332-351` — `BOSS_SACRIFICE_CARDS` に分類され、「使い捨てにできる弱い将軍」だけが対象。卵なし、最大HP 50以下、戦闘5以下という条件がある。
- `hanjuku_reference.py:549` — ヴィーナスは HP82・戦闘12・卵スーパーなので、sacrifice_general の条件を満たさない。
- `src/docich/hanjuku/strategy/replan.py:1085-1105` — ボス在庫札選びでは、sacrifice 適格者だけにバグストームを追加し、一般候補ループでは sacrifice を除外する。
- `src/docich/hanjuku/flows/deploy.py:584-604` — ボス強札 supplement でも、sacrifice 適格者だけが `BOSS_SACRIFICE_CARDS` を得る。

一方で、使用直前ゲートもバグストームを無条件には許可しない。

- `hanjuku_reference.py:1490-1498` — 将軍/将軍型ボスに対するバグストームは `half_both_hp` で、HP が既知でも `lethal=False`。
- `src/docich/hanjuku/judgment/battle.py:494-497` と `:513-516` — 消耗許可の helper は `self_harm` を明示的に除外する。
- `judgment/battle.py:529-552` — `_card_damage_gate` は選択可否を戦況から評価する。
- `judgment/battle.py:567-584` — 召喚済み・致死・治癒・安全な消耗などで許可されるが、バグストームは自傷のため通常の消耗許可に入らない。
- `judgment/battle.py:645-650` — ボス/召喚戦で `_critical_survival_needed` が真なら `critical_card_spend` として許可されうる。

つまり、ヴィーナスがバグストームを使わなかったこと自体は、少なくとも「非瀕死・敵召喚なし・自傷を避けるべき状況」では設計に沿いうる。逆に、瀕死で切り札を投げるべき状況や、召喚・劣勢で開封評価が必要な状況では問題になりうる。本件 issue にはどちらの条件も記録されていない。

## 4. 「開かなかった」ことと「開いたが正しく拒否された」ことを区別できない

`src/docich/hanjuku/flows/battle_menus.py:1266-1286` の `_carried_card_left` は、使用可否として `BOSS_ONLY_CARDS` だけを見る。バグストームは `BOSS_ONLY_CARDS` ではないため残存札になりうるが、ここで `_card_damage_gate` の許否、sacrifice 適格、瀕死例外、開封後拒否は評価されない。

また battle_end emitter の metric には card_flow の履歴ではなく終了時状態しか残らない。開封後に damage gate が拒否し、selection/use に進まなかった場合でも、selection/use/unclassified/missing に記録されなければ `_carried_card_left` 上は「未試行の残存札」のままである。その場合でも battle_end で同じ `battle_boss_card_missed` が作られる。

したがって issue の `card_flow: なし` は「一度も開かなかった」ことの確証ではなく、少なくとも emit 時点で active flow がなかったことしか示さない。公開 issue には `battle_card_damage_rejected`、`battle_card_menu_open`、`battle_card_selected`、生存ゲート、card_assessment などの時系列がない。

## 5. なぜ (a)(b)(c) に確定できないか

- **(a) 実欠陥とは断定できない**: `_carried_opening_tactics`（`src/docich/hanjuku/flows/battle.py:569-574`）は確定携行された未指定札を温存せず開幕へ回す設計を持つ。もし携行確定かつ使用判断が必要な状況で一度も開封評価へ進まなかったなら実欠陥だが、本件では携行 provenance、開封有無、戦況、ゲート履歴がない。
- **(b) 設計どおりとも断定できない**: ヴィーナスは sacrifice 適格ではなく、自傷札を温存する設計意図はある。しかし `_critical_survival_needed` による boss critical spend 例外があり、瀕死なら使用が正しかった可能性を排除できない。戦闘結果や HP がないため、非瀕死だった証拠もない。
- **(c) 誤検知とも断定できない**: 未使用の残存札があったという emit 条件自体は成立している。ゲート拒否や勝利済みなら誤検知に近いが、開封経路が欠落していたなら実事象のままである。区別証拠がない。
- **(d) 判定不能**: 上記の決定因子が emit/issue の双方に欠けている。これは追加調査不足ではなく、battle_end emitter が必要な文脈を記録しない構造に起因する。

## 6. v1581 → v1582 の影響

観測版は v1581、検証 base は v1582。`868e45258`（#5748）は mechanical boss order に `purpose='attack'` を補う変更で、差分は `replan.py`、version、diagnostics/version test に限られる。`_boss_card_left_unused`、`_carried_card_left`、`_card_damage_gate`、month audit の card_miss 投影は変わっていない。したがって本検証の結論は v1582 base でも v1581 と同じである。ただし v1582 は boss 出撃の武装/起動性の問題を扱うもので、本件のバグストーム使用判断を修正するものではない。

## 7. 判定可能にするために必要な証拠

同型発火を (a)/(b)/(c) へ分けるには、battle_end emitter または監査投影に少なくとも次を残す必要がある。

1. 戦闘結果（勝利/敗北/退却/不明）と終了時・開封時の `ally_hp` / `enemy_hp`
2. `_critical_survival_needed` の判定材料（瀕死・生存危機）
3. 敵召喚獣戦だったか、boss general の白兵だけだったか
4. カード行を開いたか、開いた場合の実一覧、damage gate の role/allowed、拒否 decision
5. 携行 provenance（planned_cards、departure binding、confirmed panel kit、auto-carry、stock fallback のどれか）
6. 終了時に active ではない過去の card_flow があったか

現行 issue だけではこれらが全て欠けており、ラン成果物も公開 issue から取得できないため、本件は (d) で closes せず open のまま残すのが適切である。

## 検証メモ

- 実行した検証: issue 本文照合、fingerprint / occurrence の Python sha256 再計算、現行 `origin/main` `868e45258` の emit・監査・バグストーム仕様・damage gate・sacrifice 条件の静的照合、v1581→v1582 差分の確認、GitHub Actions ログの確認（g1373 の該当戦闘に接続する runtime artifact は得られず）。
- ドキュメントのみの追加のためテストは実行しない。
- read-only 検証であり、本番操作・コード修正・VM への接続・ゲーム入力は行っていない。
