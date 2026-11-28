# VERIFY-5831 — [半熟英雄] 出撃失敗: ch5 タルタル

- 対象: azumag/docich#5831 / fingerprint `264feebab430f27f` / occurrence(incident_id) `31c1a9080828d364dc7d` / key `264feebab430f27f/10-2/71729`
- ラン: `g1373-b4c4126f` / generation 1373 / `hanjuku-chart-v1607-chikujou-noncontiguous` / ch5 10-2
- 検証範囲: read-only。issue 本文・現行 main のコード・リポジトリ内の commit 履歴だけを使い、本番 decision log は探していない（ローテーション済みの前提）。コード修正・commit・push・PR・issue 操作はなし。
- 検証 base: 現行 main `a87a7fe83` / `hanjuku-chart-v1611-card-block-month-window`（`src/docich/hanjuku/version.py`）。
- `gh` は指示どおり実行していない。`DOCICH_HANDOFF_PATH` の確認実行は環境で却下され、handoff 正本は未読として扱う。

## 結論: **(a) 実欠陥**

ただし欠陥の所在を限定する。**`card_list_unreadable_depart` → 空携行ガード取消 → `order_failed` という単発の取消自体は、#5353 系の資源保護と #5790 の loop 防止に沿った設計どおりの防衛動作**である。実欠陥は、観測版 v1607 にあった `unreadable_card_block` 台帳の寿命側である: v1593/#5790 の台帳は章内では月替わりでも失効せず、解除条件が事実上「同じ出撃元の切り札一覧を構造読みする」だけだった。出撃元がアルマムーンだけの ch5 では、そのブロックが一覧を再開する唯一の経路も止めるため、自己ロックして以後の出撃を失う residual defect だった。現行 main の v1611 はまさにこの ch5 10-2〜10-8 の事象をコメント・テスト付きで修正している。

したがって本件は「#5790 が未適用で同じ波状 loop をそのまま再発した」事案ではない。逆に `unreadable_card_loop_blocked` が本文に出ていることは #5790 の blocker が発火した証拠である。一方で、その blocker の v1607 時点の保持方法に実欠陥が残っていた、という切り分けである。現行 main では月替わり/900 tick で解除する修正済みだが、観測当時の挙動を設計どおり・誤検知とは判定しない。

## 1. fingerprint / occurrence / step_local の式照合

現行 `src/docich/hanjuku/month_audit.py` の sortie 系 builder だけを見た。別種別の式は使っていない。

### step_local

`month_audit.py` の `_sortie_event` は、step を `record.chart_step` または `observed_metric.step` から取り、

```python
step_local = step.rsplit(':', 1)[-1] if step else None
```

とする（現行 L1211-L1224）。したがって本件 `A:d6dcabfd:M25f492` の step_local は **`M25f492`** である。`A:` は調整チャート実行 step の prefix、`d6dcabfd` は request id `d6dcabfd15d3e283` の先頭8桁、最後の `M25f492` が local step である。#5811 の `I:d8d78d:2ec91b77` → `2ec91b77` と同じ規則である。

### fingerprint

`_build_sortie_incident` は

```python
record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])
```

で、`_fingerprint` は

```python
sha256(f"{chapter}|{kind}|{'+'.join(signals)}").hexdigest()[:16]
```

である（現行 L1328-L1329, L3545-L3547）。sortie incident では本文の全 signals（`sortie_order_failed` など）を渡さず、`[event['kind']]` の1要素だけを渡す。したがって本件の理論 raw は次のとおり。

```text
5|sortie_failed|sortie_failed
```

期待値は issue の `264feebab430f27f`。

### occurrence(incident_id)

同じ builder は

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{event['reason_code']}|{event.get('step_local')}|"
       f"{event.get('general')}|{event.get('target')}|{int(float(now))}")
record['incident_id'] = sha256(raw).hexdigest()[:20]
```

とする（現行 L1324-L1327）。issue JSON の値から構成入力は次のとおり特定できる。

```text
g1373-b4c4126f|1373|sortie_failed|sortie_order_failed|M25f492|タルタル|オトラント|1795860280
```

`1795860280` は `created_at=1795860280.9572198` の `int(float(now))`。期待値は issue の `31c1a9080828d364dc7d`。

### 再計算の成否

`python3` での sha256 再計算は実行承認が却下された。承認要件を迂回する代替ハッシュ実行もしていないため、**独立再計算は未実施**である。式と入力値は上記のとおり完全に特定できたので、検品ゲート側で再計算可能な形で残す。なお `git log 59bfc0850..HEAD -- src/docich/hanjuku/month_audit.py` は空で、v1607 以降現行 main まで `month_audit.py` 自体は変わっていない。

key `264feebab430f27f/10-2/71729` は `_month_key` の `(fingerprint, month, first_tick)` の表示と一致する（`month=10-2` / `first_tick=71729`）。

## 2. 現行 main での決定連鎖

### 2.1 切り札一覧を読めない場合の fallback

`src/docich/hanjuku/flows/deploy.py` L2800-L2855:

- `card_select` で `_measured_card_select` が構造読みできない。
- strict boss ではない通常出撃では `card_unreadable[step]` を数え、`NORMAL_CARD_UNREADABLE_LIMIT` に達すると `card_skip_departure[step]` を増やして `card_list_unreadable_depart` を記録する。
- 初回は B 1回で一覧を閉じ、携行確認へ進む。ここで「計画の切り札無しで出撃を続行する」のは 2026-11-10 owner 方針の bounded fallback として実装されている。

本件の直前決定はこの `card_list_unreadable_depart` を含む。したがって後段の `unreadable_attempt` は 1 以上だったと確定できる。

### 2.2 空携行ガード取消

`deploy.py` L951-L959 の `_empty_kit_blocks_departure` は、`want` が非空で `carried == []` の readable confirm を出撃承認しない。コメントにあるとおり #5353 を受けた owner 方針で、「無装備で損失を取りに行くより取消して後で再試行する」ための防衛である。

`deploy.py` L2984-L3080:

- `sortie_confirm` で `carried=[]` が読め、`_deploy_cards(order, mem)` の実効 kit `want` とは一致しない。
- mismatch の bounded reading が `SORTIE_CONFIRM_LIMIT` に達し、`_empty_kit_blocks_departure(want, carried)` が真なら `sortie_kit_empty_cancel`。
- 同じ step に `card_skip_departure` がある場合だけ、先に `unreadable_card_block.mark(...)` と `card_census_booking.book(mem, 'unreadable_card_loop')` を行い、`unreadable_card_loop_blocked` を記録する。
- その後 `_finish_order(mem, 'failed', deviation_reason='carried_kit_empty', ...)` が `order_failed` を記録する。

本件本文の `card_list_unreadable_depart` → `unreadable_card_loop_blocked` → `sortie_kit_empty_cancel` → `order_failed` は、このコード順序と完全一致する。監査側では `SORTIE_TERMINAL` が `order_failed` を `sortie_failed` に対応づけ、`carried_kit_empty` は専用分岐がないため generic の `sortie_order_failed` になる（`month_audit.py` L1158-L1192）。この reason_code は「空携行ガード」固有の意味ではなく、#5811 とも字面が同じになるだけなので機構の同一性証拠には使えない。

なお issue 要約の `計画cards: None` や、`plan.chart_plan.orders` の JSON に `cards` が無いことは、「実効 kit が空だった」証拠ではない。`_summarize_plan` は chart order の `cards` を incident へ写さない（`month_audit.py` L455-L477）。また `_empty_kit_blocks_departure` が発火した以上、少なくとも `_deploy_cards` の実効 `want` は非空だったことがコード上確定する。ただし、それが明示計画札だったのか安全札 auto-carry だったのかは、issue の縮約 JSON だけでは判別できない。

## 3. #5790（v1593）のカバレッジと残存欠陥

### 3.1 本件は #5790 の対象条件そのもの

`tests/test_hanjuku_unreadable_card_loop.py` の冒頭は「docich#5770: unreadable card list + empty-kit guard must not form a loop」と明示し、現行テスト L48-L62 は `card_skip_departure` がある空携行取消で、

- `unreadable_card_loop_blocked`
- `sortie_kit_empty_cancel`
- `order_failed`

が全部記録される形を固定している。本件の連鎖はこのテスト対象と同型である。

`unreadable_card_block.py` の key は `key_for(source)` で、将軍・step・target ではなく **出撃元**である（現行 L29-L36）。card_select の在庫画面は target ごとではなく出撃元が開く同じ画面なので、step 名を変えた波状攻撃で同一失敗をなぞらないためである。`mark` は row に chapter/month/source/cards/steps を保持し、steps は bounded list に追加する（現行 L164-L205）。

block の適用も現行コードで確認できる。

- `_ready` は `unreadable_card_block.order_blocked` を false ready として返す（`strategy/orders.py` L857-L875）。
- 新規 interim/mechanical 候補の生成でも blocked order を skip し、新しい step 名を付け直さない（`strategy/replan.py` L2132-L2138）。
- failed step の再武装でも blocked order は pending に戻さない（同 L2698-L2708）。

本件の `order_start 2` は、1件目が read-only roster probe `A:d6dcabfd:Mc1c2d9`、2件目が attack `A:d6dcabfd:M25f492` である。同じ attack が何度も re-arm された #5770 型ではなく、#5790 の blocker は少なくとも当月内の波状反復を止める方向へ機能している。

### 3.2 台帳の扱いに残っていた residual defect

現行 `unreadable_card_block.py` の冒頭コメント L8-L13 が、今回と同じ ch5 10-2〜10-8 を明示する: アルマムーンだけが在城で、取消後の全注文が standing のまま出撃0が続いた。現在の docstring は、解除証拠が無いまま章末まで止めると「可読性の証拠を取る機会自体が失われる」と説明している。

これは v1607 当時の実装と一致する。`git show c032a6a37:src/docich/hanjuku/strategy/unreadable_card_block.py` で確認した v1593 初版では、

- `_row_active` は `row.chapter == mem.chapter` だけを見る。
- `mark` の docstring は「no automatic monthly expiry」「chapter transition clears the whole key」と明示する。
- 章内の解除は同じ出撃元の sortie card list の構造読みだけであり、card census 完了は解除証拠にしない。

一方で block されるのは札を携行する注文なので、出撃元が1つしかない章では、その block が「同じ出撃元の一覧を開いて構造読みする」唯一の現実的経路も止める。本件 issue の世界状態は `在城: アルマムーン(4)` で、ほかの城に在城がいない。この条件では v1593 の loop 防止が月またぎの再試行窓を持たず、出撃0へ自己ロックする。これが residual defect である。

現行 v1611 では、

- `_expired_window` が月替わりまたは `BLOCK_TICKS=900` で block を期限切れにする（`unreadable_card_block.py` L39-L55, L24-L26）。
- `mark` の docstring が「失敗を測った月＋tick上限に限定」「再失敗は新しい窓で再 mark」と説明する（同 L164-L175）。
- `_off_chart` が `expire` を呼び、`unreadable_card_block_expired` を1回記録して再試行する（`strategy/replan.py` L3688-L3700）。ここのコメントは、再試行が `order_start` または次の empty-kit cancellation として見えることまで明示している。

したがって、現行 main ではこの residual defect は修正済みである。ただし本件ランは v1607 であり、修正前の章内無期限 block を持つ世代だった。

補足: v1611 でも、月替わり/tick 上限で再試行した一覧が再び読めなければ、単発の empty-kit cancellation は設計上の再試行コストとして再発しうる。したがって将来の同型 occurrence が1件現れるだけなら residual defect の証拠ではない。問題だったのは v1607 で再試行窓そのものが無かった点である。単発の設計済み取消を high/confidence の auto issue として残すかは監査ポリシー上の別論点であり、本検証では変更しない。

### 3.3 card census / 台帳の意味

`unreadable_card_loop_blocked` の際に `card_census_booking.book(mem, 'unreadable_card_loop')` も行われる。これは全所持読みで `card_stock` 台帳を確定する予約であり、block そのものの解除証拠ではない。現行モジュールも census は stock を証明するだけで、出撃元の occluded/unreadable panel が読めることの証明ではないとしている（`unreadable_card_block.py` L164-L175）。在庫0が確定して将来の注文が本当に cardless になる場合は block 対象外だが、本件のように実効 `want` 非空の注文は対象である。

## 4. #5801/#5806（v1597）と #5803/#5829（v1607）との関係

関係なし。

- `018db6630`（#5806/v1597）の変更ファイルは `src/docich/hanjuku/flows/month.py`、version、diagnostics、対応テストだけで、築城 defer の skipped 記録である。
- `59bfc0850`（#5829/v1607）も `src/docich/hanjuku/flows/month.py`、version、diagnostics、scroll テストだけで、築城一覧の非連続走査である。
- いずれも `flows/deploy.py`、`strategy/unreadable_card_block.py`、`strategy/replan.py`、`strategy/orders.py`、`month_audit.py` を変更していない。
- 本件 issue の decision counts にも chikujou 系は出ていない。

したがって本件の card_select/sortie_confirm 連鎖に対する直接の修正・悪化要因ではない。`hanjuku-chart-v1607-chikujou-noncontiguous` という version 名は、これらを含むビルドであることを示すだけである。

## 5. #5811 との異同

同じなのは監査上の外側だけである。

- 両方とも terminal decision が `order_failed` で、`_sortie_event` 上は `sortie_failed`。
- 両方とも reason_code が generic の `sortie_order_failed`。
- 両方とも `order_launched` なく終わる。

機構は別である。

- #5811 は ch12 / boss / アルファルファで、将軍一覧の本人・カーソル確認が strict 条件を通らず、`general_list_hold_exhausted` → `order_failed` に進んだ。カード選択へ到達していない。v1597 当時の `GENERAL_LIST_HOLD_LIMIT=8` の bounded cancellation であり、検証では (b) 設計どおりとされた。現行 v1610 では strict failure の内訳記録と scan-backed recovery が追加されている。
- 本件 #5831 は `general_list_scanned` でタルタルを確認し、将軍選択段階を通過している。失敗したのはその後の `card_select` 読み不可 → `sortie_confirm` の空携行ガードである。したがって roster strict-hold の別観測ではない。

#5811 と違って本件では、単発取消の先に「block の解除証拠を取る経路を block 自身が塞ぐ」自己ロックがコード上特定できる。この差が、(b) ではなく (a) と判定する主理由である。

## 6. なぜ (b)(c)(d) ではないか

- **(c) 誤検知ではない**: `order_failed` / `carried_kit_empty` は issue 本文の decision 連鎖と現行コードの双方で実在する。監査が存在しない失敗を作り出したわけではない。
- **(d) 判定不能ではない**: 本番 log がなくても、issue 内包の decision 時系列と現行/当時のコードで、発火条件・block key・台帳の寿命・single-source 自己ロックまで特定できる。
- **(b) だけではない**: 空携行で取消した瞬間だけを切り出せば設計どおりだが、v1607 の block 台帳は月替わりで再試行できず、解除証拠を取る経路を失う実害を持つ。現行コードの v1611 修正コメントと回帰テストが、この ch5 事象をそのまま残存欠陥として固定している。単発取消を「設計どおり」、全体を「実欠陥なし」と混同しない。

## 7. 未決・残件

- fingerprint / occurrence は式と入力値まで確定したが、`python3` 実行却下のため独立ハッシュ再計算は未実施。検品ゲートで再計算すること。
- 実効 `want` の具体的なカード名は issue の縮約 JSON からは読めない（`plan.chart_plan` は `cards` を写さず、`decisions.before` も `observed_metric` を落とす）。ただし `_empty_kit_blocks_departure` 発火から非空であることだけは確定する。
- 切り札一覧が読めなかった画面上の根因（一時的な描画/OCR不可か、読み取り条件の過不足か）は、ローテーション済みの本番 log/frame がないため判定しない。結論 (a) は unreadable の根因特定ではなく、v1607 の block lifetime に対する判定である。

## 検品ゲート確認（Muse、2026-11-28 22:31 JST、現行 main e50f39a4a）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("5|sortie_failed|sortie_failed")[:16] = 264feebab430f27f、SHA-256("g1373-b4c4126f|1373|sortie_failed|sortie_order_failed|M25f492|タルタル|オトラント|1795860280")[:20] = 31c1a9080828d364dc7d。いずれも issue の値と一致する（unit は python3 の承認却下で未計算だったが、式・入力の記述は正しかった）。
- 結論 (a) 実欠陥を維持する。現行 main のコードで次の3点を直接確認した。
  - `src/docich/hanjuku/strategy/unreadable_card_block.py` で BLOCK_TICKS = 900、月替わりでブロックを解除する修正が入っている（PR #5837、v1611）。`tests/test_hanjuku_unreadable_card_loop.py` の `test_month_boundary_releases_a_blocked_source_and_a_new_failure_reblocks` が ch5 10-2 → 10-3 の解除を固定している。
  - 単発の空携行ガード取消自体は設計どおりの防衛動作であり、実欠陥は v1607 時点の台帳の寿命側（月替わりでも失効せず、解除条件が事実上「同じ出撃元の切り札一覧を構造読みする」だけだった）にある。#5790 の blocker が発火した証拠（`unreadable_card_loop_blocked`）は本文に出ているため、「#5790 が未適用で同じ loop を再発した」事案ではない。
  - 修正は現行 main に入っているため、新しい修正 PR は出さない。
