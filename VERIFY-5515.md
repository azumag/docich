# VERIFY-5515 — [半熟英雄] キャンプ帰還未確認: ch6 アルマムーン

対象: docich#5515（`bug` / `auto-generated` の監査自動起票）
ラン `g1342-1fe0734c` / generation `1342` /
`hanjuku-chart-v1493-interim-order-purpose` / 月 `ch6 11-1`（観測 56回 /
80.9秒 / max gap 2.1秒）
fingerprint `ea6dd8f4a62c57e8` / occurrence(incident_id) `7d49dc3e35ad4cbe8539`
created_at `1795569734.6645713`（2026-11-25 01:22:14Z）

判定: **(a) 実欠陥** — 終端判定 `camp_recall_unconfirmed` /
`dispatch_unconfirmed` 自体は契約どおりの正しい検出（帰還先ピッカーが
月次処理の裏で開いたまま残り、帰還は少なくとも受理されていない）。
実欠陥はその下層で、`await_dispatch` の帰還要求が場面・月次画面を
またいで中断刻印も検証 budget の保護もないまま検証 window を消費し、
receipt 証明の機会を失って終端する構造にある。v1493 では concert 場面
経路が await_dispatch を明示的に除外し、`_mark_recall_interrupted` の
呼出経路も戦闘前景に限られるため `recall_interrupted` の刻印機会がなく、
短期回復経路も無いまま通常の 400 tick 失敗 hold と `mem['uncertain']` で
捨てられる。同一機構は現行 main でも `camp_recall_unconfirmed` を同じ
signature で起票する（#5514 / v1495 が刻印→短期 hold + task 無料再試行を
カバーするだけで、incident の発火は防がない）。#5437 / #5452 とは別層。

調査基盤: worktree `/home/hatch/work/docich-5515`（detached HEAD
`89324e141` = origin/main = `apply stock fallback to adopted adjusted
sorties (#5282, v1442) (#5304)`）。read-only 検証: 本番 SSH なし /
ラン成果物 `hanjuku_decisions.jsonl`・`hanjuku_events.jsonl`・
`hanjuku_frames/` は VM 側で直接参照せず / 秘密情報なし /
コード変更なし（成果物は本ファイルの追加のみ）。issue 本文は GitHub
公開ページ経由で取得（`gh` 不使用）。`handoff.md` 正本は
`DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1）。
fingerprint の sha256 再計算は実行環境が計算コマンドを拒否したため
**未実施**。`_fingerprint`（`month_audit.py` L3112-3114）の式
`sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]` をコード上確認し、
呼出側 L931 が `_fingerprint(mem.get('chapter'), event['kind'],
[event['kind']])` であることから、本件の入力は
`6|camp_recall_unconfirmed|camp_recall_unconfirmed` に確定する（入力が
同じなら出力も同じ）。incident_id は L926-929 で
`runtime_id|generation|kind|reason_code|camp|general|int(now)` の
sha256[:20] であり、`now` は起票時刻依存のため後追い再計算は原理的に
不能（一意性担保用で照合キーではない）。

---

## 結論

**(a) 実欠陥。** 二段に分かれる。

1. **終端判定そのものは契約どおり（誤検知ではない）。**
   `_recall_dispatch_step`（v1493 `recall.py` L2452-2596）の受理条件は
   `input_sent && picker_closed && !interrupted`（L2478-2481）。
   `input_sent` は `_journal['_recall_inputs']` の送信 receipt 照合
   （L2454-2457: `request_trace` 一致かつ `a_inputs` 充足）でのみ立ち、
   本件は 6 観測を使い切っても receipt が得られず `dispatch_unconfirmed`
   で終端した。終端観測の画面は `world_map` — **帰還先ピッカー（いどう→
   全体マップの確定画面）が月次処理を終えてもなお開いたまま残った**
   ことを意味し、受理済みなら閉じて `map` へ戻る契約
   （`docs/operations/hanjuku-recall-dispatched-20261122.md`）上、
   帰還 A がピッカーを確定しなかったことは画面だけで証明できる。
   reason_code `camp_dispatch_unconfirmed` は `resulting_event` からの
   決定的写像（`month_audit.py` L768-769）で**正しい署名**
   （#5248/#5250 の誤署名は v1435 系で修正済み）。

2. **下層の実欠陥 — 月またぎの await_dispatch が無保護のまま死ぬ。**
   - 直前決定列（issue JSON `decisions.before`）には
     `camp_recall_requested`・`camp_recall_input_sent`・
     `camp_recall_interrupted` が**一切無い**。`_build_camp_incident` の
     決定窓は終端時刻の **120秒前まで**（`month_audit.py` L855、
     since=now-120 → 01:20:14.66Z 以降）であり、窓内の 24 決定は月次処理
     （兵数確認→卵回復→増築→商人完了→month_done→post_month_status→
     month_confirm）で埋まっている。`await_dispatch` は `_request_recall`
     （L2419）でしか作られず、同関数は `camp_recall_requested` を
     無条件記録するため、**要求はこの月の可視部分より前（10月の終盤〜
     月またぎ境界）に記録された**ことが確定する。
   - recall の画面所有は `_recall_dialog_wanted`（`orchestrator.py`
     L913-938）で決まる。`month_sub` セット中は dest+map_target を除き
     何も所有しない（L930-931）が、`month_sub` が立つのは卵回復・徴兵・
     増築などの部分取引中だけであり、それ以外では `map`/`map_target`/
     `text`（全ステージ、L932）に加え、await_dispatch なら `unknown`/
     `yes_no` も明示的に所有対象（L933）。所有しないのは `month_menu`
     や `shop_*` 等の月次自身の画面種別だけである。つまり月次処理中に
     recall は**全期間凍結ではなく**、部分取引の合間に現れる text/
     yes_no/unknown（フェード）などの所有可能画面をまたいで検証
     window を消費し得る。
   - await_dispatch の検証待ちに budget 保護は無い。`_camp_recall_step`
     は `stage == 'await_dispatch'` なら呼ばれた画面上で無条件に
     `_recall_dispatch_step` へ委譲し（`recall.py` L1497-1498、
     text-hold の `stage != 'await_dispatch'` 免除は L1461、コメント
     L1457-1458 が "the dispatch wait settles on the next screen, which
     can be a text/month screen" と明記）、呼ばれるたびに `steps += 1`
     （L2453）して `steps >= 6` で終端する（L2468）。`world_map` 画面は
     `map_flow.world_map_step` からも無条件に同じステップへ渡る
     （`map.py` L596-597）。一方 concert 場面の割り込み処理は
     `recall.get('stage') != 'await_dispatch'` 条件で**明示的に免除**
     される（`orchestrator.py` L2069-2071 → `_recall_after_scene` に
     到達しない）ため、場面をまたいでも中断刻印は一切残らない。
   - よって本件の構造は「月次処理 ~80秒（55 tick）のあいだ凍結」では
     なく、**場面・月次画面をまたいだまま中断刻印や budget 保護なしに
     検証 window を消費し得る**ことである。v1493 の回帰テスト
     `tests/test_hanjuku_recall_month_scene.py`
     `test_a_dispatched_recall_scene_coverage_waits_for_field_evidence`
     がまさにこの契約を検証している: await_dispatch の recall は
     concert 場面を `[a]`・無刻印・状態保持で通過し、6 回の dispatch
     step で finite に `camp_recall_unconfirmed` へ終端する（検証
     window は凍結されず消費される設計。ただし同テストは
     input_sent=True の到達未確認（`arrival_unconfirmed`）側で、
     本件の送信未確認（`dispatch_unconfirmed`）側とは終端が異なる）。本件の終端画面は月次終了直後に
     再露出した `world_map` ピッカーで、終端時刻 01:22:14.6Z は
     month_confirm（01:22:10Z）直後と時系列が一致する。要求時点からの
     6 ステップがどの画面（要求直後のピッカー / 月次処理中の所有画面 /
     終端前の world_map）に配分されたかは、非終端ステップが決定記録を
     残さないためラン成果物なしでは確定不能だが、いずれにせよ「月次処理
     の全期間凍結後に残りステップを消費した」構造ではない。
   - **送信 receipt の証明機会は journal 末尾の生存期間に限られる。**
     receipt は run loop が毎観測 `recall_input_receipts` で
     `hanjuku_events.jsonl` の**末尾64KiB**だけを走査して作り
     （`brains/hanjuku/bot.py` L411-462、`input_sent` 行を
     `request_trace` で照合）、`decide` の `recall_inputs` 引数経由で
     `_journal['_recall_inputs']` に注入される（`orchestrator.py`
     L1668）。注入値は観測ごとに pop され（L2709）実質1観測しか持たず、
     `input_sent` への確定は `_recall_dispatch_step` の中でしか行われ
     ない（L2454-2457）。つまり証明には「**送信行が末尾64KiB内に
     残っている間に** dispatch step が少なくとも1回走ること」が必要
     で、原理的に不可能ではないが、送信から dispatch step までのあいだ
     行が末尾から押し出されると二度と証明できない。
   - 本件では `dispatch_unconfirmed` で終端した以上、走った各 dispatch
     step の時点では既に照合可能な送信行が tail 内に無かったことが
     裏付けられる（tail 内にあれば `input_sent` が立ち
     `arrival_unconfirmed` になったはず）。月次処理 ~80秒の観測・入力
     イベントで要求時の送信行（あれば）が押し出されたか、送信自体が
     無かったか（A が月場面へ食われた / 要求アクションの無言喪失）の
     区別はつかず、終端形はどちらも同じ `dispatch_unconfirmed` に落ちる
     （#5250 §3-3 の receipt 境界の曖昧性と同型）。
   - **`interrupted=False` は刻印機会が無かった結果にすぎない。** v1493 の
     `_mark_recall_interrupted`（`recall.py` L435-451）は
     `state.get('stage') != 'await_dispatch' or state.get('input_sent')`
     で early return する（L449）— つまり await_dispatch かつ未送信の
     もの**だけ**を刻印対象にする関数で、対象から除外していたわけでは
     ない。しかし呼出経路は戦闘前景に限られる（`_camp_recall_step` の
     battle 系種別 L1217・`mem['battle']` L1233。text-hold 内の L1467 は
     外側が `stage != 'await_dispatch'` でガードされているため対象
     ステージには到達しない）。さらに月場面では orchestrator 側の
     concert 条件（L2069-2071 `recall.get('stage') != 'await_dispatch'`）
     が `_recall_after_scene` / `camp_recall_interrupted` への到達自体を
     遮るため、月場面に遮られた事実はどこにも残らない。結果として
     `_arm_recall_skip` は `base=None` のまま呼ばれ（L2513-2514）、
     **既定の 400 tick 失敗 hold** がかかり、task recall 向け無料再試行
     （`post_month_recall_requeued`、`post_month_status.py` L2528-2534）の
     前提条件である `recall_interrupted` マーカーも無い。月またぎをまたいだ
     孤児 recall は当月の task 文脈を持たないため、同じ無料再試行の
     `verification.general == name` 照合にも乗らない。
   - **発射の成否はラン成果物なしでは確定不能だが、どちらの枝でも欠陥が
     成立する**: A がピッカーへ届いたなら受理＝閉鎖のはず（開いたままなので
     反証済み）→ A は未送出か月場面に消費された。後者なら
     #5437/#5452 と同型の入力誤送、前者なら要求アクションの無言喪失。

## 1. issue 提示値 → コード対応（`425f1c38a` = v1493 上の行番号）

| issue の値 | 対応箇所 | 検証結果 |
|---|---|---|
| 決定 `camp_recall_unconfirmed` | `recall.py` L2557-2562（`_recall_dispatch_step` 終端の未受理枝、唯一の記録点） | 一致 |
| status `dispatch_unconfirmed` / `input_receipt_confirmed: False` | L2479-2481: `input_sent` 偽 → `dispatch_unconfirmed`／L2526 転記 | 一致（送信 receipt が一度も照合されず） |
| stage `await_dispatch` / steps 6 | L2419（stage 設定）/ L2453（+1）・L2468（`steps >= 6` で終端） | 一致 |
| `picker_closed: False` | L2466-2467: `input_sent` 後に `screen.kind=='map'` でのみ立つ | 一致（終端画面は `world_map` のまま） |
| `interrupted: False` | L2469: `_flow['recall_interrupted']` 未検出。`_mark_recall_interrupted`（L435-451）は await_dispatch+未送信を対象とするが呼出が戦闘前景（L1217/L1233）限定、concert 場面経路（L2069-2071）は同ステージを免除 → **刻印機会なし** | 一致（機械的には正しいが「横取りなし」の保証ではない） |
| `escape_move: False` / `target_cell: [128,111]` | L2520-2525。target_cell を渡す非 escape 経路は屋根確定（L2043-2044）・picker_forced（L2010-2011）・visible_move（L806）のいずれか | 一致（ピッカー上で確定点が測定済みだった帰還） |
| 将軍 `不明`（general null） | 終端 metric `general = state.get('general') if state.get('fort') else None`（L2529）→ fort 以外は構造的に常に None | 一致（非砦の通常・task・匿名帰還のいずれか） |
| 野営 `[216,191]` / 帰還先 `アルマムーン` | `terminal_metric['camp']`（L2522）/ `state['goal']`。`アルマムーン` = ch6 本城（`hanjuku_chart.py` `HOME_CASTLES[6]`）→ owned（世界状態でも在城3） | 一致 |
| 理由文 / reason_code `camp_dispatch_unconfirmed` | L2559-2560 文言 / `month_audit.py` L768-769（`resulting_event` 決定的） | 正しい署名 |
| `camp_recall_goal_failed` 不在 | L2573-2590 のペア失敗計上は `arrival_unconfirmed` 限定 | 一致（dispatch_unconfirmed では宛先拒否計上しない設計） |
| fingerprint `ea6dd8f4a62c57e8` | `_fingerprint(6, kind, [kind])`（`month_audit.py` L931 → L3112-3114）、章+kind のみ | 式から確定（実行再計算は環境拒否で未実施） |
| `auto_file` / confidence high | `_build_camp_incident` `auto_file=True`（L932）。観測56・max gap 2.1s で減点事由なし | 契約どおり |

## 2. 直前決定列と月またぎ機構（issue JSON `decisions.before` より）

```
01:20:35  poor_harvest        (text)       月またぎ冒頭イベント（凶作）
01:20:53  month_seen          (month_menu) 月一メニュー表示（監査窓の開始）
          [兵数確認→補充スキップ→卵回復→増築(イカリア16G)→商人完了]
01:22:03  month_done          (month_menu) 月一の購入を終了
01:22:10  post_month_status_armed + month_confirm
01:22:14.6 camp_recall_unconfirmed (world_map) ← 6 ステップ消化で終端
```

- `camp_recall_requested`・`camp_recall_input_sent`・
  `camp_recall_interrupted`・`post_month_recall_requeued` は全て不在。
  決定窓は終端の 120 秒前まで遡るため（`month_audit.py` L855）、
  **要求は 01:20:14Z 以前＝10月終盤か月またぎ境界で発行された**。
- `await_dispatch` の recall は concert 場面の割り込み処理を明示免除
  される（`orchestrator.py` L2069-2071 の `stage != 'await_dispatch'`
  条件により `_recall_after_scene`・`camp_recall_interrupted` 記録へ
  到達しない）ため月場面をそのまま通過し、刻印も残らない。
  `_flow['recall']` を月またぎで pop する経路は存在しない。
- 凍結ではなく**所有画面上での窓消費**が起こる: `month_sub` は部分取引
  （卵回復・徴兵・増築等）中のみ立つゲートで（L930-931）、その間以外は
  recall が `map`/`map_target`/`text`（全ステージ、L932）および
  await_dispatch 限定の `unknown`/`yes_no`（L933）を所有し得る。
  所有した画面では `_camp_recall_step` → `_recall_dispatch_step`
  （`recall.py` L1497-1498）が走り `steps` を消費する — 中断刻印も
  budget 保護も無いまま、場面・月次画面をまたいで 6 ステップの検証
  window を食い切り得る。月次終了後に残った `world_map` ピッカーは
  `world_map_step`（`map.py` L596-597）経由で同じ budget を消費し、
  本件はそこで `steps >= 6` に達して終端した。
- `world_map` で dispatch step が走る経路は 2 系統ある: 通常 recall は
  `map_flow.world_map_step` の無条件委譲（`map.py` L596-597）、
  retreat/anonymous_recheck 付き recall は `camp_recheck.owns_dialog`
  の `_ALLOWED_KINDS['await_dispatch']`（`camp_recheck.py` L33-34）が
  `world_map` を含むため同じ経路を使う。どちらでも同一終端になる。

## 3. 同型家族との切り分け

| issue | 形 | 機構 | 本件との関係 |
|---|---|---|---|
| #5248 / #5250 | `input_sent=False` → `dispatch_unconfirmed` | 送信 receipt 鎖の喪失（v1419 世代） | **同じ終端形の親族**。記録形欠陥（誤署名・metric 欠落）は v1435 系で修正済み、本件は新形（stage/steps/status/reason_code 全出し） |
| #5306 | `arrival_unconfirmed` ch10 デーメーテール、砦→ボス | 送信後の受理未観測 | 終端ステータスが違う（本件は送信段階） |
| #5437 | `arrival_unconfirmed` ch12 バジル | **送信済み**後の text/場面遮蔽で picker_closed 未観測 | 「場面が窓を食う」は似るが、本件は**送信 receipt 段階**かつ**月またぎの無保護窓消費**という別機構。#5514（v1495）の scene_covered は `input_sent` 確定後のみ有効で本件には効かない |
| #5452 | `arrival_unconfirmed` ch4 アルマムーン | 防衛戦闘で dest/y_route のモード喪失→`castle_menu` 誤入力 | 戦闘中断は本件の決定列に無く、終端画面も `world_map` で別層。#5518（v1498）の mode_lost 検出（castle_menu 等）も本件には不発 |
| 本件 #5515 | `dispatch_unconfirmed` ch6 アルマムーン | **月次処理をまたいだ await_dispatch の無保護窓消費＋receipt 証明機会の喪失** | 送信段階の未確認。要求が月またぎ以前に存在したことが 120 秒窓で確定 |

## 4. v1493 / v1495 / v1498 / 現行のカバー範囲

- **v1493（ランと同バージョン、`hanjuku-chart-v1493-interim-order-purpose`、
  版確定 commit `425f1c38a`）**: await_dispatch は場面割り込み処理から
  除外される（`orchestrator.py` L2069-2071 の `stage != 'await_dispatch'`
  条件で `_recall_after_scene`・`camp_recall_interrupted` 記録へ到達
  しない）。`_mark_recall_interrupted`（`recall.py` L435-451）は
  await_dispatch+未送信だけを対象とする刻印関数だが、その呼出は
  戦闘前景（`recall.py` L1217/L1233）に限られ月場面には到達しない
  → 月またぎで `recall_interrupted` が立たない → 終端
  `interrupted=False` → `base=None` で既定 400 tick hold。
  **刻印経路も検証 window の budget 保護も v1493 には無い。**
- **v1495（#5437、PR #5514 = `05b0363a1`）**: 現行 main では concert 条件
  （`orchestrator.py` L2083-2111）から `stage != 'await_dispatch'` の
  除外が外れ、`_recall_after_scene`（L948-965）は await_dispatch を
  捨てず `_mark_recall_interrupted` + `scene_pending` で保持する。
  新版 `_mark_recall_interrupted`（`recall.py` L443-465）は receipt 未
  成立なら `recall_interrupted` を、成立済みなら `scene_covered` を
  立てるため、**同じシナリオなら終端が `interrupted=True` になり**
  `_arm_recall_skip` へ `FORT_RETRY_TICKS=20` の短期 hold が渡る
  （L2685-2688）。task recall なら `wait_recall` の
  `post_month_recall_requeued` 無料再試行にも乗る。
  **ただし incident 自体は `dispatch_unconfirmed` のまま同じ signature で
  起票される**（receipt 不成立のままなので status は変わらない）。
  scene_covered 保護は `input_sent` 確定後の話で本件には無関係。
- **v1498（#5452、PR #5518 = `0193891d3`）**: `mode_lost`（終端窓の
  castle_menu/general_list/castle_info/main_menu 検出→interrupted 扱い・
  goal_failed 免責・`camp_recall_mode_lost` 記録）。本件の終端画面は
  world_map で対象外・不発。
- **現行 main（`89324e141`）**: 上記2修正を含むが、送信前の月またぎ
  要求が `dispatch_unconfirmed` で終端する機構は変わらず、非 task recall
  の無料再試行経路も無い（野営の再検出→最初からの帰還が実質の回復）。

## 5. 残件（read-only では確定不能、判定には影響しない）

一意確定にはラン成果物（VM 側 `hanjuku_decisions.jsonl` /
`hanjuku_events.jsonl` / `hanjuku_frames/`）が必要:

- **要求の正確な発行時点と経路**: 10月終盤の `camp_recall_requested`
  記録（target_cell [128,111] を返したのは屋根確定・picker_forced・
  visible_move のいずれか）。task recall（`post_month_status` 作成）か
  通常の野営帰還か匿名再確認か。
- **送信 A の着地点**: 要求の persist 後〜月場面までに `input_sent` 行が
  書かれたか。書かれていれば月場面への誤入力（#5437 型）、無ければ
  アクション喪失（#5250 型 receipt 境界の曖昧性）。
- **月次処理中の journal 末尾64KiBの内容**: 検証ステップは月次処理中にも
  所有画面上で走り得るため「一度も走らなかった」前提は取り下げる。
  `input_sent` が一度も立たなかった以上、**走った各 dispatch step の
  時点では照合可能な送信行が tail 内に無かった**（存在しないか既に
  窓外）ことが裏付けられる。残件は「送信行が一度でも tail 内に存在し
  たか」と「どの画面が各ステップを消費したか」（非終端ステップは
  決定記録を残さない）。
- **`recall_verification` の `requested_tick`**: issue の flow スナップ
  ショットは `_shrink` で stage/status/steps のみに縮小されており、
  要求 tick の月またぎ位置は issue から読めない。
- **同一ラン g1342-1fe0734c での他の recall 終端**: 前月（10-x）に
  この要求を発行した recall の記録列（`camp_recall_requested` の
  goal/camp/発行画面）。

## 6. 修正候補（実装はしない、検証の結論として）

1. **月またぎ要求の早期扱い**: `_request_recall` 実行前の月末ゲート
   （`_month_end_soon` は新規フィールド野営開始時にのみ存在）を
   dest 確定→要求の境界にも適用するか、await_dispatch の要求を月末場面で
   明示的に中断扱いして当月 task の無料再試行に乗せる（v1495 は刻印を
   始めたが、非 task recall には再キュー経路が無い）。
2. **開いたままのピッカーの再駆動**: 終端観測が `world_map`/`map_target`
   で宛先ピッカーがゲーム側に残っているなら、そのオープン状態は
   「A が未受理」の実画面証拠であり、B で閉じて捨てる代わりに同一
   確定を安全に再送する経路を検討する（receipt 曖昧性を考慮した
   二重送信抑止の設計は要るが、開いたままのピッカーへの再確定は
   受理の二重化ではない）。
3. **receipt 確定の観測レベル化**: `_recall_inputs` は毎観測注入・毎観測
   pop されるが、`input_sent` への確定は `_recall_dispatch_step` 内で
   しか行われない。trace 照合が取れた時点で（画面所有の有無に関わらず）
   state 側へラッチすれば、証明可能だった期間に dispatch step が
   所有画面へ渡らなかった分の証明機会の喪失を防げる（本件でも送信が
   実在した枝では `dispatch_unconfirmed` → `arrival_unconfirmed` に
   帰属が変わる）。
4. **終端 metric の月またぎ明示**: `requested_tick` と終端 tick の
   月またぎスパン（または「月次処理を挟んだ」フラグ）を終端 metric に
   残し、監査・トリアージ側で「月またぎの無保護窓消費による証明機会
   喪失」と通常の送信失敗を区別できるようにする。
5. **（A が月場面に食われた場合）場面への誤入力抑止**: 送信直後に
   非 picker 画面が被ったことを検出したら、ピッカー残存を前提に
   dispatch を安全に仕切り直す経路（#5437 §6-5 と同型、未送信層版）。

## 付録: issue 事実 → コード対応表

| issue の値 | 対応箇所（全て `425f1c38a` 上の行番号） |
|---|---|
| 決定 `camp_recall_unconfirmed` / status | `recall.py` L2479-2481, L2557-2562 |
| stage `await_dispatch` / steps 6 | `recall.py` L2419, L2453, L2468 |
| `picker_closed` = map 限定 | `recall.py` L2466-2467 |
| `input_sent` receipt 照合 | `recall.py` L2454-2464, `brains/hanjuku/bot.py` L411-462（`recall_input_receipts`→`_journal_pad_inputs`、末尾64KiB）|
| request_trace 刻印 | `brains/hanjuku/bot.py` L161-162（persist） |
| `input_sent` journal 行 | `src/docich/hanjuku_run.py` L482-486（`action_sent`） |
| `interrupted` 刻印機会なし（await_dispatch） | `_mark_recall_interrupted` 対象条件 `recall.py` L449（await_dispatch+未送信のみ）、呼出 `recall.py` L1217/L1233（戦闘前景のみ）、場面免除 `orchestrator.py` L2069-2071 |
| 月次画面の所有条件（month_sub ゲート＋種別） | `orchestrator.py` L929-938（`_recall_dialog_wanted`: month_sub 中は dest+map_target のみ／それ以外は map・map_target・text（全ステージ）+ unknown・yes_no（await_dispatch）を所有） |
| await_dispatch の dispatch step 委譲 | `recall.py` L1457-1461, L1497-1498（text-hold 免除・無条件委譲）、`map.py` L596-597（world_map 無条件委譲） |
| recheck 系の world_map 所有 | `camp_recheck.py` L33-34, L68-70, L1062-1068 |
| 終端 metric / `input_receipt_confirmed` | `recall.py` L2519-2529 |
| `camp_recall_goal_failed` が arrival 限定 | `recall.py` L2573-2590 |
| task recall の `to_camp` 作成・wait_recall 再キュー | `post_month_status.py` L2365-2389, L2487-2538, L1554-1570 |
| reason_code 決定的写像 | `month_audit.py` L761-769 |
| 決定窓 120 秒 / before 30行 / counts top30 | `month_audit.py` L855, L912, `_decision_rows` L2968-2984 |
| fingerprint 式 / incident_id 式 | `month_audit.py` L3112-3114（呼出 L931）/ L926-929 |
| `アルマムーン` = ch6 本城 | `hanjuku_chart.py` `HOME_CASTLES[6]` |
| v1495 の await_dispatch 場面刻印（現行のみ） | `orchestrator.py` L948-965, L2083-2111, `recall.py` L443-465 |
| 設計契約（6 観測・未確認保持・受理=ピッカー閉鎖） | `docs/operations/hanjuku-camp-recall-20261001.md`, `docs/operations/hanjuku-recall-dispatched-20261122.md` |

記録: issue #5515 への結論コメントは検品ゲートが行うため投稿しない。
本レーンはコード変更・マージを行わない（read-only 検証のみ）。

改訂記録（2026-11-25）: 本ファイルは PR #5549（検証記録の初版、マージ
済み）へのマージ後レビュー（Devin-Web、「マージ不可（要修正）」、
https://github.com/azumag/docich/pull/5549#issuecomment-7154289997 ）
の指摘1〜4に基づき訂正した。`_mark_recall_interrupted` の対象条件
（v1493 `recall.py` L449 は await_dispatch+未送信のみを刻印対象と
する。除外は `orchestrator.py` L2069-2071 の concert 条件）・月次
画面の所有モデル（`_recall_dialog_wanted` L929-938: month_sub
ゲート＋await_dispatch は unknown/yes_no も所有）・検証 window が
「凍結」ではなく所有画面をまたいで消費される構造・receipt 証明機会の
喪失（注入 receipt は末尾64KiB走査由来で毎観測差し替え）を、v1493
（worktree `425f1c38a`）と現行 main（`127168a54`）のコードを読んで
再検証した。結論 (a) 実欠陥は変更なし。
