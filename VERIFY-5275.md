# VERIFY-5275 — [半熟英雄] ボスフェーズで月内ボス出撃ゼロ: ch9 ハデス

対象: docich#5275（監査自動起票、`boss_month_zero_sortie` / confidence: high）/
ラン `g1190-ch1v9` / `hanjuku-chart-v1426-ch9-oshiroga-up` / 月 `ch9 12-12`
（観測 832 回 / 255.3 秒 / max gap 1.2 秒）、fingerprint `24fd89a3f147ba68`、
occurrence `bf5e06c3d6b43414ef04`。再発: `ch9 13-1`（occurrence `92512e35c05d4d5d0da5`）。
調査基盤: worktree `~/work/docich-5275`（base `8a219f65d`＝#5270、ラン時 v1426 系と同系の
pre-#5279 コード）。read-only 調査。`gh`・`git fetch` は本環境で拒否されたため、issue 本文・
#5276 本文は未取得。`origin/main` ローカル ref（`c3cf80fda`、#5279 `7b6539872` 含む）は
照合済み。本番 VM SSH なし。

---

## 結論

**(a) 実欠陥。** 月内ボス出撃ゼロは artifact ではなく実際の停滞であり、その直接機構は
**ボス注文が出撃元の将軍一覧ステージで `boss_companion_required` により無制限に
pending へ戻される待機ループ**（`src/docich/hanjuku/flows/deploy.py` L2380-2383 →
`_hold_guarded_sortie` L1767-1784）である。owner PR **#5279 はこの観測経路を
カバーしない**（後述 §5: 修正が有界化したのは別ステージの `general == NAME` ゲートのみ
で、ch9 の機械ボス注文ではそもそも到達不能）。

- 検出の正当性: ボスフェーズ成立中（boss `ハデス`・フェーズ観測 177 回）に月が閉じ、
  `_boss_departures`（ボス向け `LAUNCH_DECISIONS` 行）が 0 → `boss_month_zero_sortie`
  high は設計どおりの正しい起票（month_audit.py L3313-3315, L2787-2810, L103-110）。
- 直接の停止点: ボス注文 `A:3814542a:M381c6a`（attack、イデ！→ボス）は active 化・
  `order_start`・出撃元への `castle_jump` までは進むが、将軍一覧で
  `_order_hero_alternatives` が空（`not candidates`）となり毎回
  `boss_companion_required` で取消→pending 化。上限・失敗化・主人公単独への
  エスカレーションがこのサイトには存在しないため、代役が実一覧に現れない限り
  無制限に繰り返す。記録 2 件＝一覧到達が 2 回あったことの直接証跡。
- スクロール繰り返しは実在するが主因ではない: イデ！は 12 城コリドーの末尾であり
  8 行ウィンドウの最下行までのページ送りが毎トリップ必要（map.py L1839-1850）。
  繰り返し自体は「hold → pending → 再 active → 再 jump」の再試行ループの症状。
  ただしページが進まない停滞（scroll_stall）が混在した場合、
  `_castle_jump_y_fallback`（L1660-1680）は失敗カウンタを増やさず、
  L2624-2626 で同一城ジャンプを即再開するため、停滞系としても無制限側である
  （§3）。
- #5276（`boss_phase_no_battle`）は同じゼロ出発をフェーズ軸で数えた兄弟検出。
  出撃 0 ⇒ ボス戦闘 0 であり、別機構ではない（§4）。

## 1. 監査ロジックの検証（issue 数値との対応）

`src/docich/hanjuku/month_audit.py`:

| issue の値 | コード | 検証結果 |
|---|---|---|
| `boss_month_zero_sortie` 発火 | L3290-3315: 月末処理で `_boss_phase` が非 None かつ `_boss_departures == 0` → kind 確定 | 正しい |
| 月内ボス出撃 0 / launch_events 0 | `_boss_departures`（L2787-2810）: `LAUNCH_DECISIONS`（`order_launched`/`sortie_departed_observed`/`sortie_launch_confirmed`/`sortie_launch_unconfirmed_kept`、L103-110）のうち target ∈ ボス城群 or purpose=='boss'。`purpose=='move'` は除外（L2796） | 「ボス向け発射イベント 0」の意味と一致 |
| confidence: high | `_confidence`（L3305）+ `_build_incident`（観測数・gap・coverage・context） | 832 観測・gap 1.2 秒 < `MAX_GAP_SECONDS` 60 → high 正当 |
| ボスフェーズ 177 観測 / started_at | `boss` トラッカ（L2530-2559: chapter/bosses 同一で継続、観測毎に加算） | issue の `boss_month.started_at`・`observations` と一致する設計 |
| 出撃 確定 3 / 発射 11 / order_start 8 / order_launched 8 | `_departures`（L3290）・`_decision_counts` | 月内の他注文（増援 move・通常攻撃）は成立 — 出撃機構自体は稼働、ボス注文だけが発射まで到達しなかった構図と整合 |
| 再発 ch9 13-1（発射 7 / order_start 5 / 確定 0） | 同じ月末判定 | ハデス生存中はフェーズが月跨ぎで継続するため、同条件なら翌月も `boss_month_zero_sortie` になる設計と一致 |

**判定: 監査の分類・信頼度は正しい。** 誤検知ではない。

## 2. ボス注文 `A:3814542a:M381c6a` が発射に至らなかった経路

### 2.1 注文の出自

- ステップ id は `A:{rid[:8]}:{local_step}`（`hanjuku_chart_adjust.py` L117-122
  `execution_step`）。`local_step = 'M' + sha1('イデ！>ボス:attack')[:6]`
  （`replan.py` `_mechanical_step` L2686-2724）。すなわち `_mechanical_chart`
  （L2564-2684）→ `_adopt_plan`（L2901-2966）で採用された**機械計画の暫定ボス注文**。
- ch9 は全通常城保有後 `chart.corridor_next(9, owned) is None` → `corridor.mechanical`
  が False（`corridor.py` L83-88）となり、`interim_candidates`（replan.py L1026-）
  がボスを `[boss, *targets]` の先頭の攻撃対象として mint する（L1131-1139、
  owner 2026-11-21「ボス攻撃を最優先」）。採用計画内の順序は
  `out`＝front（先頭 retake/move）→ staffing → waves（**ボス攻撃**）→
  boss_wave_orders → 残り（L1763-1803）で、ボス注文は先頭グループ直後の高位に
  置かれる。出撃元は `_boss_wave_pick` によりコリドー末尾 `road[-1]`
  ＝**イデ！に限定**（L1192-1194、崖で他の出撃元は移動ボスへ届かない）。
- この注文の `general` は実測余剰将軍（`unavailable` に `{NAME}` を含む L1197、
  `_spares` が主人公除外 L1234）か `MOVE_ANY_GENERAL`（source_probe）。**主人公
  `NAME`（どうし）は注文の general になり得ない**。基本チャートの 9-B1 も
  `general: 騎馬A`（`hanjuku_chart.py` L957）であり ch9 のボス注文は設計上
  非主人公。

### 2.2 active 化 → 発射までの到達点

1. `plan_map_activate`（`orchestrator.py` L641-675）または `map_step` 内の
   `next_order` → `_order_start_due`（map.py L1099-1119、同一注文 12 観測
   バックオフ）を通り `active` 化 → `order_start` 記録（map.py L2597-2601）。
   `plan_map_activate` は `_ready` を評価する（L666）。
2. `_ready`（orders.py L734-747）のボスゲート: corridor 章では
   `garrison[source]` が実測済みで 2 名未満なら不可（L741-744）、
   `_boss_companion_source` が None かつ `garrison[source]` 非 None なら不可
   （L745-747）。**active 化した以上、当時は「イデ！の在城が未読
   （`garrison` キー欠落 = None）」か「実測済みだが適格代役を含む（実一覧と
   乖離した陳腐記録）」のいずれか**であった。issue の garrison スナップショットは
   未取得のためどちらかは確定できないが、結果として一覧到達は 2 回確認できる。
3. 出撃元ナビ: `_want_castle_jump`（map.py L1471-1489）→ `_start_castle_jump`
   （L1521-1541、`castle_jump_open` 記録）→ X メニュー「しろ」一覧で
   イデ！行を探す。owner の記録した `plan_map_activate → castle_jump_open（イデ！）
   → castle_jump_scroll` 系列はここに一致。
4. 出撃元到達 → 将軍一覧（`general_list`）→ `deploy_step`（deploy.py L2307-）:
   - `_cursor_order_pick`（L1156-1168）は `_is_boss_order` で早期 return（L1169）。
   - `order.get('corridor')` は機械/暫定ボス注文には付かないため
     `_corridor_list_end`（L2314-2315）も素通り。
   - L2366: `order['general'] == NAME or _is_boss_order or source_probe` の分岐で
     `present` を読み `_order_hero_alternatives`（orders.py L53-）を評価
     （重複名排除・非主人公・HP 既知・行軍中/位置不明除外・卵/負傷条件等）。
   - **L2380-2383: `_is_boss_order and not candidates` →
     `_hold_guarded_sortie(..., 'boss_companion_required',
     '十分な能力の代役を実一覧で確認できないため主人公を出撃させず準備を待つ')`**。
     この理由文の「実一覧で確認できない」表現は L2382 固有で、issue の 2 件の
     `boss_companion_required` は本サイト由来と読める（map.py L2745・deploy.py
     L2480 の理由文は語彙が異なる）。
   - `_hold_guarded_sortie`（L1767-1784）: `A:` 系ステップは `'pending'` に戻り
     （L1772、`'I:'` 系のみ `'failed'`）、`active = None`・選択将軍/コンテキストを
     消去し `[b, b]` でメニューを抜ける。
5. 計画はボス注文を先頭に持つため、バックオフ 12 観測経過後に同一注文が
   再び active 化 → 同じ城ジャンプ → 同じ一覧 → 同じ hold。**このループには
   回数上限も「失敗として閉じる」道も、主人公単独へ倒す道も無い**。

`launch_events` が 0 の理由: hold は発射確認（`map_target` の A →
`sortie_departure_requested` 系、`map.py` L2841-2864）より前の一覧ステージで
発生するため、発射イベント行が一度も書かれない。通常出撃の発射 11 件は
ボス向けでない注文（増援・通常攻撃）のもの。

## 3. castle_jump スクロール繰り返し（owner 指摘）の評価

実在するが、2 種を区別する必要がある。

**(i) 正常なページ送り（主たる内訳と推定）**: しろ一覧は 8 行ウィンドウで、
`castle_jump_scroll` は「出撃元の行が見えないため、手を一覧の最下行まで送って
ページ送りする」（map.py L1843-1849）毎に記録される。イデ！は 12 城コリドー
（`hanjuku_chart.py` L1243-1245）の末尾であり、ウィンドウ最下部までのスクロールが
毎トリップ必要。`CASTLE_JUMP_SCROLL_LIMIT = 16`（L1304、旧上限 8 では後半章の城に
届かず引き上げ済み）以内で行が見つかれば `castle_unreachable`/`select` へ進む。
**hold → pending → 再 active の繰り返し 1 回ごとにこのスクロール列が再発する**ため、
owner が見た繰り返しの大半は再試行ループの症状である。

**(ii) 既知の停滞系（残存リスク）**: ページが進まない停滞は
`scroll_signature`/`scroll_stall`（L1832-1838）で検出し、
`_castle_jump_y_fallback`（L1660-1680）で一覧を閉じ `castle_jump_y` を残す。
**この経路は `_end_castle_jump` を通らず `castle_jump_menu_failures` を
インクリメントしない**（失敗カウントは L1594-1599 のみ）。その後 map_step が
`castle_jump_y` を消費して**同一の「しろ」ジャンプを即再開する**
（L2624-2626。owner 2026-11-19「自城は Y 画面を使わない」のコメントどおり、
ここでの "Y 経路" は同一メニュー再挑戦を意味する）。この再開は
`_want_castle_jump` を通らないため `castle_jump_skip` の
`CASTLE_JUMP_COOLDOWN = 40` 観測クールダウン（L1481-1485）にも掛からず、
カーソル付き map フレームが来た時点で即座に次のトリップを始める。
- したがって「一覧は開くがページが進まない」状態は **open → scroll → stall →
  fallback → 即再 open の無制限ループ**を構成しうる。これは
  map.py:1627 コメントの既知事案（owner 2026-11-22「208 castle_jump_scroll /
  71 menu failures」）と同族で、当該コメント直下の L1630-1654 の放棄経路
  （`castle_jump_abandoned`、n≥`CASTLE_JUMP_ABANDON`）は `_end_castle_jump`
  終了理由にしか効かず、**stall→fallback 単独では発火しない**。
- なお単一トリップ内は `total > CASTLE_JUMP_LIMIT`（48）で 'limit' 終了し、
  それは `_end_castle_jump` 経由でカウントされる。無制限なのは
  **トリップを跨いだ再開**である。

issue 決定表（`castle_jump_y_fallback`・`castle_jump_end` 系の有無・各行 signature）
を取得できていないため、(i) と (ii) の内訳比は未確定。ただし (ii) が発生していた
としても、一覧到達 2 回（hold 2 件）と矛盾せず、§2 の companion 待機ループが
主因である評価は変わらない。

## 4. #5276（boss_phase_no_battle）との比較

- #5275 は**月境界**の検出: ボスフェーズ中に月が閉じボス向け出発 0 →
  `boss_month_zero_sortie`（month_audit.py L3313-3315）。
- #5276 は**フェーズ継続時間**の検出: フェーズ開始から
  `BOSS_NO_BATTLE_SECONDS = 600` 秒（L51）ボス戦闘（敵名がボス名/BOSS_GENERALS の
  `battle_start`、L2560-2579・L617-619）が無ければ `boss_phase_no_battle` を 1 件。
- 同一ラン・同一フェーズ（ch9 ハデス）では、**出撃 0 ⇒ 戦闘 0** であり、#5276 は
  #5275 の下流症状を別粒度で拾った兄弟 issue と読むのが自然である。#5275 の
  `launch_events 0` は「発射前に止まっている」ことを示し、#5276 単体では
  発射前停止か行軍中/到着不成立かは区別できない。
- 残存不確定: #5276 本文（gh 取得不可）に出発後の行軍停滞・`boss_pursuit`
  （`flows/boss_pursuit.py` は発射後の移動ボス追尾機構であり本件の発射前とは別段）
  の証跡があれば別機構の可能性も残るが、本件決定表上は発射 0 が確定的なため、
  共通原因（発射前の companion 待機ループ）と評価する。

## 5. PR #5279 のカバレッジ評価（最重要）

`7b6539872`（v1431、2026-11-24 05:41、ローカル origin/main に存在、実際のマージ
ライフサイクルは user 提供情報に依存）の差分は `map.py`＋新規テスト＋version＋
diagnostics のみ。**deploy.py は無変更**。

変更内容: `BOSS_COMPANION_WAIT_TICKS = 200`（origin/main map.py L2749）と
`_boss_companion_wait_expired`（L2752-2775）を追加し、`target_step` 内の
従来 hold を

```python
if (_is_boss_order(order, mem) and _sortie_general(order, mem) == NAME ...):
    if not _boss_companion_wait_expired(mem, order):   # L2812
        return _hold_guarded_sortie(..., 'boss_companion_required', ...)
```

で有界化した（200 tick 経過で `boss_companion_timeout` を記録し主人公単独出撃へ
フォールスルー）。

**カバーしない（本件の観測経路）**:

1. **観測された hold サイトが違う**。本件の `boss_companion_required` 2 件は
   「実一覧で確認できない」理由文から `deploy.py:2380-2383`（general_list
   ステージ）由来であり、#5279 は `map_target` ステージ（target_step）のみを
   有界化した。フロー上 general_list は map_target より**前**（一覧 → 札選択 →
   出撃確認 → `map_target` で発射）にあり、本件の注文は有界化されたゲートに
   **到達しない**。
2. **ch9 ではそのゲートの前提条件が満たせない**。ゲートは
   `_sortie_general(order, mem) == NAME` を要求するが、ch9 の機械/暫定ボス注文は
   `general` に主人公を取らない（replan.py L1197・L1234・9-B1=騎馬A）。
   `general_override`/`sortie_general`/`actual_general`/`bound` 経由で
   NAME になる経路も、deploy フローが主人公代役を明示拒否する
   （`hero_substitute_refused` deploy.py L2441-2446、`sortie_general` 代入は
   常に実一覧の非主人公候補 L2414・L787・L1400）ため存在しない。
   すなわち #5279 の有界化は**ch9 のこの注文系では条件式自体が成立せず、
   発火しない**（他章の「チャートが主人公をボス役に指名する」注文、
   例えば 1-B1/3-B1/4-B1..B4/8-B1 や、hot-loaded `sortie_general` 残留の
   ケース向けの修正と読める）。
3. **残る無制限サイト**: `deploy.py:2380-2383`（本件サイト）、
   `deploy.py:2477-2481`（card_select/sortie_confirm の `== NAME` ガード。
   これも #5279 対象外で無制限のまま。ただし ch9 では同じく到達不能）、
   `_ready` のボスゲート（orders.py L745-747）はいずれも変更なし。

**結論**: 同じ証拠列（一覧で代役を確認できない状態）で本件を再演しても、
修正後のコードでも `boss_companion_required` が deploy.py:2381 で無制限に
繰り返され、月内ボス出撃ゼロは再発しうる。#5279 が効くのは「主人公として
解決済みのボス注文が map_target に達する」ケースに限られ、本件の機構は
対象外。

## 6. `boss_companion_required` 2 件と月内ゼロ出撃の関係

- 2 件は「実一覧を 2 回開いて 2 回とも適格代役が居なかった」直接証跡であり、
  ボス注文が発射前ステージで 2 回取消されたことの確定証拠。0 出撃の**原因**
  そのもの（結果でなく）。
- ループのコスト: hold 毎に active 解放 → 計画高位の同一注文がバックオフ
  （12 観測）後に再 active → 城ジャンプ数十観測を消費 → 一覧 → hold。
  `active` が占有される間、増援 move を含む他 pending 注文は開始できず、
  **代役を連れてくるはずの移動注文の枠を待機側が削る**構造（ボス注文が
  waves 枠＝ front 直後の高位＝ replan.py L1769, L1802）。月内の他発射
  11 件は hold 間の隙間や先行 front 注文に出たもの。
- 代役が来なかった背景（推定・補助的根拠）: フェーズは月内 177 観測 ≒数十秒で
  あり、城間行軍は実時間で数分を要するため、フェーズ中に発射した増援は月内に
  イデ！へ着き得ない。イデ！の在城が hero 単独等の状態なら、待機しても当月は
  構造的に代役が揃わず、「代役待ちの継続」or「主人公単独」のいずれかに
  落とす判断が必要だった — その分岐がこのサイトに無いのが欠陥。

## 7. 残存欠陥（修正の方向を示すのみ・実装しない）

1. `deploy.py:2380-2383` の companion 待機に上限が無い。`boss_companion_required`
   の発生回数/経過 tick を注文ステップ単位で数え、上限で「主人公単独を許す」
   または「注文を failed にして計画再構築（別出撃元/増援優先）へ倒す」
   分岐が必要。#5279 の `_boss_companion_wait_expired` をこのサイトにも
   適用するか、一覧ステージ用の同等タイマを置く形が対称。
2. `castle_jump` の stall→fallback→再開ループが失敗カウンタを迂回する
   （map.py L1660-1680 vs L1594-1654）。`_castle_jump_y_fallback` でも
   ステップ単位の停滞カウントを進め、`CASTLE_JUMP_ABANDON` へ合流させるか、
   再開回数に上限を持たせる余地がある（§3(ii)）。
3. （任意）`deploy.py:2477-2481` も同型の無制限 hold として残る。

## 8. 本調査の制約・未確認事項

- `gh`/`git fetch`/ネットワークが本環境で拒否されたため、issue #5275・#5276 の
  本文・決定表（`decisions.before` の実レコード列）、`garrison` スナップショット、
  #5279 の PR 本文は未取得。コード上の理由文照合（§2.2-4, §5-1）と owner
  提供の事実で代替した。#5279 の「マージ済み」時刻は user 提供情報＋ローカル
  ref の存在に基づく。
- 関連テストのローカル実行は本セッションで実行が拒否されたため未実施
  （対象として `tests/test_hanjuku_hero_guard.py` — deploy.py:2380 の
  hold を `test_readable_boss_list_without_any_companion_never_selects_hero`
  が固定、`tests/test_hanjuku_castle_jump*.py`、
  `tests/test_hanjuku_chapter9_mechanical_march.py`、origin/main 側
  `tests/test_hanjuku_ch9_boss_companion_timeout.py` を同定済み）。
- `castle_jump_scroll` の停滞（ii）と正常ページ送り（i）の内訳比は、issue 側の
  `castle_jump_*` レコード列を見ないと決定できない（§3）。

## 付録: issue 数値 → コード対応表

| issue の値 | 対応箇所 |
|---|---|
| `boss_month_zero_sortie` high | `month_audit.py` L3313-3315（`_boss_phase` 非 None かつ `_boss_departures==0` かつ high） |
| 月内ボス出撃 0 / launch_events 0 | `month_audit.py` L2787-2810 `_boss_departures`、L103-110 `LAUNCH_DECISIONS`（`purpose=='move'` 除外 L2796） |
| pending ボス注文 `A:3814542a:M381c6a` attack イデ！→ボス | `hanjuku_chart_adjust.py` L117-122（`A:`＝採用計画実行 id）、`replan.py` L2686-2724（`M`＝内容ハッシュ）、L1192-1194（出撃元＝`road[-1]` イデ！限定）、L1131-1139（ボス最優先） |
| `boss_companion_required` 2 件 | `deploy.py` L2380-2383（理由文「実一覧で確認できない」は本サイト固有）→ `_hold_guarded_sortie` L1767-1784（`A:` 系は pending 復帰 L1772） |
| `plan_map_activate → castle_jump_open → castle_jump_scroll` 繰り返し | `orchestrator.py` L641-675、`map.py` L1521-1541（open）、L1839-1850（scroll 記録）、L1304 `CASTLE_JUMP_SCROLL_LIMIT=16` |
| scroll 停滞→再開 | `map.py` L1832-1838（stall 検出）、L1660-1680（`_castle_jump_y_fallback`、失敗カウンタ非経由）、L2624-2626（`castle_jump_y` で同一ジャンプ即再開・`_want_castle_jump`/クールダウン不経由）、L1630-1654（`castle_jump_abandoned`、stall 系に効かない） |
| #5279（7b6539872, v1431） | origin/main `map.py` L2749 `BOSS_COMPANION_WAIT_TICKS=200`、L2752-2775 `_boss_companion_wait_expired`、L2812-2816 target_step ゲート（`_sortie_general == NAME` 前提） |
| #5276（boss_phase_no_battle） | `month_audit.py` L51 `BOSS_NO_BATTLE_SECONDS=600`、L2530-2579（600 秒ボス戦闘 0 で 1 件）、L617-619（ボス敵名の battle_start 判定） |
