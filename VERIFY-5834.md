# VERIFY-5834 — [半熟英雄] 月内出撃ゼロ: ch5 計画の指示が尽きる

- 対象: docich#5834 / fingerprint `c6f55e15ee647982` / occurrence(incident_id) `2b94d6f9e0c1a9b9979e` / key `c6f55e15ee647982/10-3/72051`
- ラン: `g1373-b4c4126f` / generation 1373 / `hanjuku-chart-v1607-chikujou-noncontiguous` / 月 ch5 10-3
- 検証は read-only。commit・push・PR・GitHub 操作はしていない。ラン `g1373-b4c4126f` の本番 decision log はローテーション済みで、issue 本文とコードだけを根拠にした。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読。
- コード基準はこの worktree の `hanjuku-chart-v1612-month-floor-dispatch`。#5837（v1611）と #5836（v1612）の変更は入っている。`git show` による local main との全文照合は承認されなかったため、v1612 以後の差分の有無までは確認していない。

## 結論: **(a) 実欠陥**

観測版 v1607 の実欠陥である。ただし、主因となった liveness 欠陥は **PR #5837 / v1611 で main へ修正済み**であり、新たな修正 PR は要らない。zero_sortie の検出自体は誤検知ではない。10-3 は実際に confirmed 0 / departures 0 で閉じ、読取専用 probe 以外の出撃候補を作れないまま月が進んだ。

細部を分けると、roster probe が発射せず `done` で終わること自体は設計どおりであり、空携行の出撃を取消す guard 自体も意図どおりである。実欠陥は、#5790/v1593 が導入した unreadable-card ループ断ちの block が v1607 では月またぎで解除されず、**唯一の出撃元アルマムーンを章内で恒久に塞いだ**ことである。block の解除条件が「同じ出撃元の切り札一覧の構造読み」だけだと、出撃元が1つしかない章では、その一覧へ進む攻撃候補も全て block され、解除証拠を取る経路自体が無くなる。10-3 の `plan_all_settled` / `chart_plan.orders: []` はこの自己ロックの下流症状である。

## fingerprint / occurrence の式と入力値

現行コードの builder は `src/docich/hanjuku/month_audit.py` にある。

- `_incident_id(identity, chapter, month)`:
  - raw: `f"{identity.get('runtime_id')}|{identity.get('generation')}|{chapter}|{month}"`
  - hash: `hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]`
  - 本件の入力値: runtime_id `g1373-b4c4126f`、generation `1373`、chapter `5`、month `10-3`
  - したがって理論 raw は **`g1373-b4c4126f|1373|5|10-3`**
  - issue の occurrence は `2b94d6f9e0c1a9b9979e`
- `_fingerprint(chapter, kind, signals)`:
  - raw: `f"{chapter}|{kind}|{'+'.join(signals)}"`
  - hash: `hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]`
  - `_build_incident()` は `primary = _primary_signal(signals)` の1要素だけを渡す。
  - `PRIMARY_SIGNALS` では `plan_starved`（members: `plan_all_settled` / `no_orders`）が `departure_failure`（`order_start_without_launch` 等）と `monthly_scan`、`battle` より先にある。本件の signals には `plan_all_settled` があるため primary は **`plan_starved`**。
  - したがって理論 raw は **`5|zero_sortie|plan_starved`**
  - issue の fingerprint は `c6f55e15ee647982`

ハッシュの独立再計算は **未実施**である。python3 / sha256sum を使った実行が承認されなかったため、一致・不一致の判定はここでは行わない。式と入力値は上記のとおり特定できたので、検品ゲート側で再計算できる。なお issue key は hash ではなく `fingerprint/month/first_tick` の結合であり、本件では `c6f55e15ee647982/10-3/72051` という構造になっている。

## 10-3 に order_start 2・発射 0 で閉じた経路

月次 audit の counts は直近数秒ではなく、閉じた月の first_at から last_at までの decision rows 全体を数える（`_close_month()` → `_read_decisions(runtime, since, until, ...)`）。

1. `order_start` は attack だけでなく、読取専用 probe を含む dispatch 全般で記録される。`src/docich/hanjuku/flows/map.py` では dispatch 時に `order_start` を記録し、probe の場合は `roster_probe: True` を付ける。
2. roster/source probe は readiness 判定の早い段階で endpoint 実在だけを見て返るため、unreadable-card block の通常の attack 用チェックを通らずに開始できる（`src/docich/hanjuku/strategy/orders.py` `_ready()`）。
3. probe が将軍一覧を読むと、`src/docich/hanjuku/flows/deploy.py` は将軍選択・携行確認・出撃へ進まず、`roster_probe_complete` を記録して `order_done` で終える。理由文も「読取のみの指示を終了し、未確定なら次回再試行する」であり、発射しないのは設計どおりである。
4. issue の月内 counts は `order_start=2`、`order_done=2`、`order_failed=0`、`roster_probe_complete=2`、`order_launched=0` だった。本番ログが残っていないため step 単位の同一性は直接確認できないが、この組合せから、10-3 の2件の order_start はいずれも発射しない read-only roster probe だった可能性が非常に高い。少なくとも、attack が出撃確認や携行選択まで進んで失敗した形跡（`order_failed`、`sortie_departure_requested`、`sortie_launch_unverified` 等）は無い。
5. したがって `order_start_without_launch` は「attack が start 後に発射へ到らなかった」ことの直接証拠ではない。audit は decision 名だけを見るため、read-only probe の start でもこの signal が立つ。本件では primary signature は `order_start_without_launch` ではなく `plan_starved` であり、こちらは実態と一致する。

## 主因: unreadable-card block による単一出撃元の自己ロック

issue の plan snapshot は、`A:d6dcabfd:M25f492` が failed、`A:d6dcabfd:M8c9082` / `A:d6dcabfd:Mc1c2d9` / `A:d6dcabfd:Me99d2f` が done、`chart_plan.orders` は空だった。月内 counts に `chart_interim_hold=1` もあり、少なくとも暫定候補の確保にも一度失敗している。因果列は次のとおりである。

1. 直前月 10-2 の #5831（同じラン `g1373-b4c4126f`・同じ ch5）では、step `A:d6dcabfd:M25f492` の attack（タルタル、アルマムーン→オトラント）が `card_list_unreadable_depart` の後に空携行確認へ進み、`carried_kit_empty` で取消された。現行コードでも、この組合せは `unreadable_card_block.mark()` と `unreadable_card_loop_blocked` を経て `order_failed` / deviation `carried_kit_empty` へ進む（`src/docich/hanjuku/flows/deploy.py`）。
2. #5790/v1593 の block は、同じ失敗の波状攻撃を止めるためには有効だったが、v1607 時点では解除が同じ出撃元の切り札一覧の構造読みに依存し、月替わりや tick 上限での解除が無かった。
3. 10-3 の world snapshot では garrison がアルマムーンだけ（7名）で、captured/lost/sorties は空だった。ch5 の home はアルマムーンであり、この時点では実質的な唯一の出撃元である。
4. card を携行する attack 候補は `unreadable_card_block.order_blocked()` により `_ready()` で false になり、さらに `_mechanical_chart()` の候補列でも fresh step 名を得る前に除外される（`src/docich/hanjuku/strategy/replan.py`）。一方、読取専用 probe は cards 空・source_probe のため残り、一覧を読んで `done` になる。issue snapshot には内部移動や staffing を成立させる別の自軍城・在城記録も無く、結果として probe だけが消費される。
5. `_off_chart()` は、採用済み chart が尽きても mechanical interim へ fallback する設計である。したがって `chart_plan.orders: []` と全 orders の failed/done は「chart が尽きたので進行不能になるのが仕様」ではない。interim/full mechanical の attack が start 不能または候補除外になったことが異常である。
6. この block は同じ出撃元の card list を読むことでしか解除できないが、その card list へ進む attack 自体が block される。出撃元1城の章では解除不能な自己ロックになり、10-3 以降も出撃 0 の月が続く。PR #5837 の説明が指摘する `2026-11-28 ch5 10-2〜10-8` は本件と同じ系列である。

現行の修正済みコードでは、`src/docich/hanjuku/strategy/unreadable_card_block.py` の冒頭コメント自体が、出撃元が1つしかない章で解除機会が無くなり出撃 0 が続いた事案を明示している。実装は月替わりまたは `BLOCK_TICKS=900` で block を expire し、`_off_chart()` は `unreadable_card_block_expired` を記録して再計画する。`tests/test_hanjuku_unreadable_card_loop.py` には、ch5 `10-2` で block されたアルマムーンが `10-3` で解除されること、再失敗すれば新月で再 block されることの回帰テストがある。これは本件の月・城・ attack route と同じ fixture である。

## #5812 / #5831 / #5818 との関係

### #5812（post_month_sortie_floor の dispatch 穴）

本件の主因ではない。

- #5812 は、月次 scan が floor yield でマップを明け渡しても、launch latch 未成立の間に housekeeping が frame を取れる穴だった。現行コードでは `post_month_sortie_floor` が `arm_sortie_floor_dispatch()` を呼び、orchestrator がその1 frame だけ boss pursuit / housekeeping より先に `map_step` へ渡す。
- 本件の issue counts には `post_month_sortie_floor` が無く、signals にも `monthly_scan_yield` が無い。さらに `order_start=2` なので dispatch 前段には到達している。
- ただし到達した2件は上記の read-only probe であり、attack の発射へ進む指示ではなかった。したがって floor 後の frame 優先度の穴ではなく、その前段にある「startable な attack candidate が無い」問題である。
- 月次処理（`post_month_*` 系）と battle が多かったことは時間配分には影響しているが、月次 sequence は完了しており、probe dispatch 自体もできている。したがってマップ占有が主因だったとは断定できない。

### #5831（10-2 の タルタル出撃失敗）

直接の前段である。10-2 の empty-kit cancel がアルマムーンに unreadable-card block を作り、10-3 の attack candidate 枯渇を作った。別 independent defect というより、同一ラン内の連鎖した前段故障である。

### #5818（zero_sortie ch12 19-11, order_start 5）

同じ `zero_sortie` でも機構が違う。

- #5818 は実際の attack/boss 系 order が複数回 start し、strict な将軍確認や bounded cancel で fail-closed に終わった月であり、signature は `departure_failure` 側だった。
- 本件は `plan_starved` で、月内 `order_failed=0`、2件の start は read-only probe と強く推定される。attack 候補の生成自体が unreadable-card block で止まっていた。
- したがって #5818 を (b) 設計どおりと見る結論を、そのまま本件へ適用できない。本件は liveness を失う (a) 実欠陥である。

## 検知精度について

- zero_sortie の事実関係は正しい。confirmed 0 / departures 0 の full month であり、(c) 誤検知ではない。
- `order_start_without_launch` は read-only probe を attack の未遂と区別しないため、単独で見ると過剰な示唆をする。ただし本件では primary が `plan_starved` であり、issue 表題「計画の指示が尽きる」は実態に合っている。
- 検知器のこの粗さは補助的な観察であり、本件の結論を変えるものではない。

## 未決・制約

- ハッシュ再計算は未実施。理論 raw は fingerprint `5|zero_sortie|plan_starved`、occurrence `g1373-b4c4126f|1373|5|10-3`。
- 本番 decision log が残っていないため、10-3 の2件の order_start の step id を直接照合できない。counts、probe のコード経路、同ラン10-2の挙動から read-only probe と強く推定できるが、月またぎで start/complete が分かれた稀な可能性までは排除できない。
- ローカル実行が承認されなかったためテストも実行していない。コード変更はしていない。

## 検品ゲート確認（Muse、2026-11-28 22:57 JST、現行 main e50f39a4a）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("5|zero_sortie|plan_starved")[:16] = c6f55e15ee647982、SHA-256("g1373-b4c4126f|1373|5|10-3")[:20] = 2b94d6f9e0c1a9b9979e。いずれも issue の値と一致する（unit は python3 の承認却下で未計算だったが、式・入力の記述は正しかった）。
- 結論 (a) 実欠陥を維持する。現行 main のコードで次の3点を直接確認した。
  - `src/docich/hanjuku/strategy/unreadable_card_block.py` で BLOCK_TICKS = 900、月替わりでブロックを解除する修正が入っている（PR #5837、v1611）。`tests/test_hanjuku_unreadable_card_loop.py` が ch5 10-2 で block されたアルマムーンが 10-3 で解除されることを固定している。
  - 10-3 の2件の order_start はいずれも read-only roster probe で、発射しないのは設計どおりである。実欠陥は、v1607 時点の block が月またぎで解除されず、唯一の出撃元アルマムーンを章内で恒久に塞いだことにある。`plan_all_settled` / `chart_plan.orders: []` はこの自己ロックの下流症状である。
  - 修正は現行 main に入っているため、新しい修正 PR は出さない。
