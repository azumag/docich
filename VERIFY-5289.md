# VERIFY-5289 — [半熟英雄] 月内出撃ゼロ: ch2 行軍が停滞

- 対象: azumag/docich issue #5289 / fingerprint `c5b2607dc1c69430` / occurrence `eabc0152dac16907c0d0`
- ラン: `g1342-1fe0734c` / `hanjuku-chart-v1432-boss-companion-timeout`
- 月: ch2 3-7（観測 306 回 / 481.9 秒 / max gap 3.5 秒）
- 出撃: 確定 0 / 発射 0 / `order_start` 0 / `order_launched` 0
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 現行 HEAD: `8dd7862d6` で `BOT_VERSION` はランと同じ v1432（`src/docich/hanjuku/version.py:7`）。以下の行番号はこの HEAD でのもの

## 結論: (b) 設計どおり・既存修正でカバー済み

月内出撃ゼロの直接因は、召喚戦（会話→変身→技メニュー→敵将軍メニュー待ち→経験反映→`battle_result`）が月の観測枠をほぼ占有したことであり、行軍側の欠陥ではない。`sortie_unverified_rearmed` は出撃を抑止したのではなく、前行月から残っていた「発射は記録されたが行軍記録の無い」phantom 出撃を、設計どおりの照合で `pending` に戻した清掃である。`stuck_march` シグネチャも、月内に実際に停滞した行軍ではなく、設計上の保持窓内に残る古い行軍記録の残滓を指している。監査の high 起票は仕様どおりであり、issue は「合法的な出撃ゼロ月の証拠記録」として正しく機能した。

## 検証観点1: 月内に 1 件の出撃も無かった経路

issue が示す月内決定列は、召喚戦シーケンスの後に `world_map_open` → `world_anchor_from_yview` → `sortie_unverified_rearmed`（step `I:c30371:08142e33`）→ `world_map_units` と続く。これをコードで追うと:

- `order_start` 0 は、月内に一度も `next_order` の選択→ディスパッチに至らなかったことを意味する（`order_start` は `src/docich/hanjuku/flows/map.py:2617` で、開始可能な指示が選ばれ active 化された時点でのみ記録される）。
- `world_map_open` は `map.py:2514-2523` で **active な指示が無く・全体マップ調査が期限** のときだけ発火する。戦闘終了直後にこの記録があることは、その時点で実行中の出撃指示が無かった（バトルがフローを握っていた）ことの裏付けになる。
- 全体マップの読み取りは `map.py:893-912` の固定順で進む: `world_anchor_from_yview`（:893）→ `_probe_launch_markers`（:906）→ `_settle_unverified_launches`（:907）→ `world_map_units`（:910）。issue の決定順はこの順と完全一致し、月末の定期調査が正常に走っただけである。
- `sortie_unverified_rearmed` は `_settle_unverified_launches`（`map.py:480-525`）が記録する。発火条件は (a) `launched_orders` に step が残る、(b) `sorties`（行軍記録）に同 step が無い、(c) 全城観測で読んだ ▲ 数 < `launched_orders` 数、の状態が `UNVERIFIED_LAUNCH_LIMIT = 3` 観測続くこと（:492-516）。成立すると `launched_orders` から step を外し `orders[step]` を `pending` に戻す（:513-516）。

つまり `I:c30371:08142e33` は**この月の出撃ではない**。step 名の `I:` は暫定指示（`src/docich/hanjuku_chart_adjust.py:28` の `INTERIM_PREFIX`）で、`{rid先頭6}:{order内容digest}` の形（`src/docich/hanjuku/strategy/replan.py:2220`）から、以前の chart request `c30371` 下で発行された暫定出撃である。その出撃は先行する月に発射記録を持ったが、行軍記録が消えた後も `orders`/`launched_orders` の「出撃済み」帳簿だけが残った phantom 状態であり、今月の調査で初めて「▲不足 3 観測」の条件を満たして再装填された。

この phantom 残留の生成経路は設計済みのもので、例えば:

- `src/docich/hanjuku/strategy/orders.py:1502-1539`（`_expire_spent_sorties`）— 期限超過の行軍記録は `sorties[step]` を破棄するが、実測終了でない消滅（zombie）では `launched_orders`/`orders` を触らない。非ボス step の phantom 化はこの分岐で起きうる。
- `map.py:104-110` — 中断後に出撃先ピッカーが `map_target` へ戻った再開経路では `sorties[step]` を破棄して `orders[step]='pending'` にするが、`launched_orders[step]` は残る。

この「発射帳簿と行軍記録の乖離を全城観測で照合して直す」機構そのものが、v1153（PR #4627、docich#4626: 5 ヶ月間 3 城が launched のまま Y view の部隊 1 個だけだった事案）で導入された修正である。`sortie_unverified_rearmed` の発火は欠陥の発生ではなく、その修正が働いた記録であり、出撃ゼロへの寄与は **抑止ではなく復旧**（次月に同じ目標を選び直せるようにする）に限られる。

## 検証観点2: legal-zero-source 系（#5190・#5213・#5236・#5257・#5264）との異同

`_sole_starved`（`replan.py:2082-2097`）→ `_defense_gate`（`replan.py:2105-2152`）による 'hold' は、出撃元2人残し制約で候補が 0 件になった合法的な出撃ゼロである。本件ではこれが成立しない根拠が 2 つある:

- `_defense_gate` が 'hold' を返すと `chart_defense_wait` / `chart_defense_wait_eval` の決定が残る（replan.py:2140-2148）。issue が列挙する月内決定にこれらは無い。
- `_sole_starved` は全所有城の自由将軍が ≤1 を条件とするが、監査信号の `no_source`（`month_audit.py:2910-2913`: 全在城一覧が ≤1 名）は本件で不発（主シグネチャが `stuck_march` である以上、`PRIMARY_SIGNALS` のより前方にある `no_source` は含まれていない — `month_audit.py:265-274`, `:2981-2985`）。つまり少なくとも 1 城に 2 名以上の在城記録があった。

`launched_unconfirmed` 系の保留も、本件の「月内出撃ゼロ」の**原因**ではない。`launched_unconfirmed` は「出撃先ピッカーが目標決定前に閉じた」際の出撃ステータス（`map.py:172-202` で `launched_orders`+`sorties` 双方に書く）であり、月内にその記録自体が無い（`order_start` 0）。関連しているのは同じ unverified-launch 系の帳簿残滓だけで、`_settle_unverified_launches` が処理するのは `launched_orders` に残って `sorties` に無い phantom（＝未確認化した出撃の後処理を含む設計どおりの照合）である。

よって本件の「hold」に相当するのは `_sole_starved`/`_defense_gate`/`launched_unconfirmed` のいずれでもなく、**召喚戦による月の時間消費 + 先行月の phantom 出撃帳簿**という別機構であり、これらはすべて設計済みの処理で説明がつく。verdict としては legal-zero 系と同じ (b) だが、機構は出撃元枯渇系ではない。

## 検証観点3: 召喚戦（monster_menu_choice「そしてわかれ」）と battle_result の関係

`monster_menu_choice` は召喚獣ターンの技メニュー決定記録（`src/docich/hanjuku/flows/battle_menus.py:3415-3435`）であり、月の `battles.decisions` にも `monster_menu_choice` として計上される（`month_audit.py:3030-3031`）。issue の決定列が示すように、この月は召喚戦の会話・変身・技選択・敵将軍メニュー待ち・経験反映・`battle_result` という戦闘完走シーケンスで埋まっている。

重要なのは、この戦闘が我々の新規出撃を伴わないことである。`order_start`/`order_launched`/発射がすべて 0 なので、月内に新しい出撃は開始されておらず、戦闘は先行月の行軍（または敵側の攻勢）に由来するものである。月内の時間が戦闘処理に消費されるのはゲーム機構上の制約であり、`battle_grind`（`month_audit.py:2898-2899`、battle 系決定 ≥20）にすら達しない規模の通常戦闘だった。行軍側に別欠陥は認められない — dispatch 枯渇の直接証拠となる `order_dispatch_starved`（`orders.py:1798-1843`、v1392/#5125 で全フェーズ計測）があれば切り分けられたが、issue 本文の決定列にその記録は無い。

## 検証観点4: high 起票の妥当性

監査の分岐（`month_audit.py:3337-3353`）では、`departures == 0` かつ `confirmed == 0` かつ `confidence == 'high'` で `zero_sortie` になる。confidence の判定（`:2930-2955`）は `started_clean`・章変化なし・月の正規進行・ledger scope 一致・max gap ≤60s・月 ≥120s・観測 ≥20・coverage ≥0.9・context あり、をすべて満たす場合に 'high' となる。本件は 306 観測 / 481.9 秒 / max gap 3.5 秒でいずれも閾値を大きくクリアしており、'high' の計算は仕様どおり正しい。

`auto_file = (kind == 'zero_sortie' and confidence == 'high')`（`month_audit.py:3098-3099`）により起票も仕様どおりである。監査の目的自体が「出撃ゼロ月を証拠として残し、人が読んで (a)/(b) を判定する」こと（`docs/operations/hanjuku-month-audit.md:345-352`）であり、合法的なゼロ月の起票は誤検知ではなく設計された運用経路である。

一点だけ注意: `stuck_march` シグネチャは「月内に行軍が実際に停滞した」ことを意味しない。これは `world.sorties` の `age_ticks > 3000` の記録が 1 件あれば発火する信号（`month_audit.py:2914-2917`）で、`age` は出撃 tick からの経過である。`orders[step]=='launched'` の `overdue` 記録（`orders.py:1416-1418` で退役しない設計）、`'arrived'`/`'closed'` 記録（`_expire_spent_sorties` の対象外、orders.py:1520-1521）、planned step の保持猶予（orders.py:1535 の `2*SPENT_SORTIE_TICKS` = 6400 tick）など、設計上の保持窓内の残滓で発火しうる。本件ではこのシグネチャが「月内の出撃ゼロの原因」を表すものではなく、戦闘消費という別の事実と古い帳簿残滓が重なった結果である。シグネチャと実態のこの乖離は監査上のノイズとしては許容範囲だが、再発するようなら `stuck_march` を「live な行軍」に限定する監査側の follow-up 候補になりうる。

## 残件・手順上の注記

- read-only 検証。VERIFY-5289.md の作成以外のファイル編集・本番 VM 操作・issue コメントは行っていない。
- `gh` による issue 本文・先行 issue（#5190 等）の直接参照は本環境では利用できず、依頼時点で提供された issue の要約とコード・ドキュメントのみを根拠にした。issue 本文に `order_dispatch_starved`・`chart_defense_wait` 等の追加記録があれば上記の結論は変わらないが、月内前半の詳細なタイムラインは issue 本文の `decisions.before` / `counts` で確認できる。
- `agent-guards/secret_scan.py` は本 box 未配置。投稿物は作成していないが、もしコメント等を書く場合は AGENTS.md §3.1 のフォールバックに従う。
