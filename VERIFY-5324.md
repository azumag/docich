# VERIFY-5324 — [半熟英雄] 月内出撃ゼロ: ch3 出撃開始後に発射されない

対象: docich#5324 / 判定 `zero_sortie` (confidence high) / 月 `ch3 4-11` /
観測 1188回 / 1739.8秒 / ラン `g1342-1fe0734c` / BOT_VERSION `v1445`
（`hanjuku-chart-v1445-early-resource-rescue`、01d44e161・PR #5160）
fingerprint `054e5049579e3c90` / occurrence `32662d219ec7118b8d31`
出撃: 確定 0 / 発射 0 / `order_start` 6 / `order_launched` 0

調査基盤: worktree `/home/hatch/work/docich-5324`、ブランチ `verify/5324-zero-sortie`。
read-only 調査で実装変更なし。行番号は **origin/main（`c01748172`、
`hanjuku-chart-v1450-password-dispatch`、version.py 実測）** 基準。
依頼文の「現行 main は v1447」は古い情報で、実測では #5336(v1448)・#5338(v1449)・
#5345(v1450) まで進んでいた。**検品ゲート追記：ゲート中に PR #5343（v1451、
#5324 のオーナー修正）が 12:29:30 JST にマージされ、main は `2cbe3542f` へ
進んだ。本文中の「未マージ」の記述はその時点のもので、現在はマージ済み。**
`gh` バイナリは本環境で起動が拒否されたため issue/PR 本文・監査 JSON は未取得。
`python3`/`pytest` の実行も拒否されたためテストは **未実施**、fingerprint の
再計算も未実施。本番 VM SSH なし。ラン成果物（`hanjuku_decisions.jsonl`・
incident JSON・frames）は VM 側にあり直接参照できない。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。
幸い、オーナー修正ブランチ `origin/fix/5324-hanjuku-stuck-probe`
（`a4f056e17`、v1451、main 未マージ）に判断ログ実測に基づく診断ドキュメント
`docs/operations/hanjuku-defense-wait-probe-20261124.md` が同梱されており、
本検証はその記載と origin/main のコードの整合性を独立的に確認した。

---

## 結論: (a) 実欠陥 —— オーナー修正は PR #5343 としてマージ済み（v1451）

**6 回の `order_start` はすべて読み取り専用の `roster_probe` 暫定指示で、
発射を目的としない。これ自体は #5264 と同じ合法経路だが、本件にはその上に
2 つの実欠陥が重なっていた:**

1. **`_defense_gate` が probe だけの候補で `defense_wait` を解除していた**
   （replan.py:2124-2130）。解除ごとに `mem['defense_wait']` が消え、
   `eval_month`/`cycles` がリセットされるため、月次評価
   `chart_defense_wait_eval`（:2144-2148）が進まず `break`（:2150）に
   到達しない。「2人目の将軍が使えるようになった」という解除理由記録は
   実際には将軍が一人も増えていない状態で発行されていた。
2. **膠着時プローブの対象選定が「敵城への実測攻撃ルートがある城」に
   限定されていた**（replan.py:1952-1959 `if not reachable: continue`）。
   ch3 本拠アルマムーンの実測ルートは全て自城向きのため本拠が候補から
   外れ、加入将軍が現れる城の一覧を一度も再読しないまま
   `country_general_count(24) > located(4)` の状態が継続 ——
   #4689 と同じデッドロックの再発。

両者が組み合わさり「待機 → probe 候補で解除 → `order_start`（読取のみ）→
`order_done` → 再待機」のループが月内に6回回り、月内出撃ゼロが
4-11〜5-3 の5か月続いた。オーナーの判断ログ実測（11:13〜11:45の35分間に
`chart_defense_wait`/`chart_defense_wait_released` が交互に6回、
`world.sorties` は空のまま）とコード上の機構は完全に整合する。

**合法ゼロではない根拠:** 仮に解除バグが無くても出撃自体は `sender_min(2)`
制約上不可能だった。しかし (i) `defense_wait` は「2人目参入を待つ」ための
月次加算機構であり、probe による擬似解除はその状態機械を壊す（欠陥1）、
(ii) 膠着打破のために設計されたプローブが、加入将軍が実際に現れる城を
構造的に読めない（欠陥2 = #4689 再発）。特に (ii) は「合法候補ゼロ」の
前提そのものを自己維持するため、設計どおりの待機とは言えない。

**カバー状況:** `origin/fix/5324-hanjuku-stuck-probe`（`a4f056e17`、
`hanjuku-chart-v1451-defense-wait-probe`）が両欠陥を修正する。
検品ゲート時点（2026-11-24 12:4x）で **PR #5343 として 12:29:30 JST に
マージ済み**（main `2cbe3542f`、v1451）。§6 に内容を記す。

---

## 1. 監査ロジックの検証（issue 数値との対応）

`src/docich/hanjuku/month_audit.py`（origin/main 行番号）:

| 数値・事実 | コード | 検証結果 |
|---|---|---|
| 判定 zero_sortie | :3385-3386 `departures == 0 and confirmed == 0 and confidence == 'high'` | 月 4-11 が 1188 観測・1739.8 秒で完全観測 → 検出は正当 |
| order_start 6 / order_launched 0 | :3095-3096 `counts.get('order_start')` / `counts.get('order_launched')` | `order_start` は probe を含む全 dispatch で記録、`order_launched` は `_finish_order('launched')`（map.py:2956-2965 `_commit_departure` 経由、記録は orders.py:2521）のみ → probe は `order_done` で終わり機械的に 0 |
| signature「出撃開始後に発射されない」 | :2923-2924 `order_start` があり `departures == 0` → `order_start_without_launch`、:271-272 `departure_failure`、:188 ラベル | issue タイトルと一致。`plan_starved`(:270)が先に評価されるため非発火 → 月末時点の `plan.orders` に未終了ステータスが1件以上あったことを意味する |
| fingerprint 054e5049579e3c90 | :3032-3034 `sha256(f"{chapter}\|{kind}\|{primary}")[:16]` → `"3\|zero_sortie\|departure_failure"` | 式は整合（ハッシュ再計算は python3 拒否のため未実施） |
| departures の定義 | :103-110 `LAUNCH_DECISIONS`（`order_launched`・`sortie_departed_observed`・`sortie_launch_confirmed`・`sortie_launch_unconfirmed_kept`）、:2837-2854 `_departures`（`purpose == 'move'` 除外・step 単位で重複除去） | `order_launched_unconfirmed`（後述・戦闘割り込み終了）は LAUNCH_DECISIONS に**含まれず** departures にも order_launched にも計上されない点に注意 |
| confidence high | :2969-2995 `_confidence` | 完全観測月で減点事由なし → high 正当。`auto_file`（:3137-3138 zero_sortie かつ high）も成立 |

**判定: 監査の検出・分類・信頼度は設計どおり正しい。** ただし signature 名
`departure_failure` は probe の dispatch を「出撃失敗」と誤読しうる表現で、
#5264 と同じ注意が要る。

## 2. `order_start` 6 → 発射 0 の経路特定（検証観点1）

`order_start`（map.py:2664）は interim 暫定指示を含む全 dispatch で記録される。
`order_start` から `order_launched` に至らない経路は origin/main 上で以下の
通り列挙できる:

| 経路 | 記録される決定 | 性質 |
|---|---|---|
| `roster_probe` 注文 | `roster_probe_complete`/`roster_probe_incomplete` → `order_done`（deploy.py:2324-2348） | 読取専用・設計どおり（発射しない） |
| 戦闘割り込みで出撃先ピッカー閉鎖 | `launch_interrupted_by_battle` → `order_launched_unconfirmed`（map.py:194-203） | 設計上の中断処理・再開機構あり（`sortie_target_resumed` map.py:124） |
| 注文失敗 | `order_failed`（orders.py:2502/2521 `_finish_order('failed')`、deploy.py:2304/2388/2404/2418/2496/2581/2765 等） | 各種ガード・上限 |
| キャンセル観測 | `sortie_cancelled_observed`（deploy.py:110-120） | 取消 |
| 月末越えで active のまま | 終了決定なし | 稀 |

本件の帰属は **roster_probe 経路**。オーナーがランの判断ログを実測し、
「`order_start`（読取のみ）→ `order_done` → また待機」が6回繰り返されたと
記録している（修正ブランチ同梱ドキュメント）。コード上もこのループを
構成する全要素が存在する:

1. `_sole_starved`（replan.py:2082-2095）成立 —— 全 owned 城の自由将軍 ≤1
   かつ未占領の非ボス城あり。全章で `sender_min = 2`（hanjuku_chart.py:1303-1305、
   guard_free は ch9 のみ :1308-1318）のため、通常の retake/attack/move
   候補は全て源要件を満たせず `interim_candidates` の `out` は空になる。
2. `out` が空のときだけ probe フォールバックが走る（replan.py:1903
   `if not out and not _army_below_floor(mem):`）。`country_general_count >
   located`（:1921-1927、24 > 4）が真で `probe_stuck_1`
   （:1986-1996、`roster_probe: True`）が mint される。
3. `interim_candidates` が非空 → `_defense_gate`（:2105）が先頭の
   :2124 で `defense_wait` を pop して `chart_defense_wait_released` を記録し
   `return None` —— **候補が probe のみかを区別しない**（これが欠陥1）。
4. `_off_chart`（:3066-3068）は gate=None で先へ進み、`_mechanical_interim`
   （:2181）が `probe_stuck_1` を選び `chart_interim_order` → `next_order`
   → dispatch → `order_start`（map.py:2664、`roster_probe: True` 付き）。
5. deploy の general_list 段で `roster_probe is True` → `_observe_garrison`
   後に将軍選択・派遣せず `roster_probe_complete` を記録し
   `_finish_order('done')`（deploy.py:2324-2348）→ `order_done`。
   `arrival_probe_tick` と `roster_read_due` のクリア（:2340-2341）＋
   `STALE_ROSTER_PROBE_INTERVAL = 300`（replan.py:2001）で同城の
   再読は抑制されるが、別城が順番に eligible になるため
   probe 候補自体は周期的に再出現する。
6. probe が消えると `interim_candidates` は再び空 → `_sole_starved` 真
   → `defense_wait` 再作成（:2138-2142、cycles=0 からやり直し）→
   `gate == 'hold'` で `_off_chart` 即 return（:3067-3068）。
   この間は `chart_adjust_request` にすら到達しない。

**`break` に至れないことが実害:** 現在 `break` は
`_mechanical_interim(allow_sole=False)`（:3029-3032、:3136-3139）を呼ぶだけで
単独城を空にする出撃は発生しない（#3566 オーナー決定）。それでも cycles が
積み上がらないと (a) `chart_defense_wait_eval` による月次の可視化が発生せず
監査・運用から「何ヶ月待ち続けているか」が見えず、(b) 誤った
「2人目の将軍が使えるようになった」という release 記録が判断ログを汚染し、
(c) hold と release の往復が毎回 map 遷移・一覧開閉の実操作を伴う無駄な
往復を生む。

## 3. #5264（legal-zero-source 家族）との異同（検証観点2）

VERIFY-5264（コミット 697179af7、未マージの verify/5264 ブランチ）は
**同一ラン g1342-1fe0734c**・ch2 2-8・v1423 の zero_sortie で、
「`order_start` は roster_probe、真の停止点は `chart_defense_wait` の
合法 hold」として (b) 判定した。

| 観点 | #5264 (ch2 2-8) | #5324 (ch3 4-11) |
|---|---|---|
| `order_start` の中身 | roster_probe（同一型） | roster_probe（同一型、オーナー実測） |
| 月を何が消費したか | probe 完走後に `chart_defense_wait` が hold として持続 | `chart_defense_wait` ↔ `chart_defense_wait_released` が数分おきに交互（35分で6回） |
| 待機の健全性 | cycles 加算・月次評価が機能 | 解除で cycles が毎回リセット、`eval`/`break` に到達せず |
| probe の対象 | 到達可能城を読み、膠着情報を更新 | 本拠が `reachable` 条件で除外され、加入将軍を一度も観測できないまま |
| 判定 | (b) 設計どおり | **(a) 実欠陥**（解除バグ + #4689 再発） |

**月占有の検証:** 依頼では「月頭の召喚戦（egg_battle / monster_skill_stall）
による月占有」の可能性が挙げられた。コード上、月頭の防衛・召喚戦闘が
出撃ピッカーを閉じた場合は `launch_interrupted_by_battle` →
`order_launched_unconfirmed`（map.py:194-203）として別名記録され、
`battle_count >= 20` なら `battle_grind` シグナル（month_audit.py:2937-2938）
も立つ。オーナーの実測記録には戦闘決定の言及がなく、系列は
wait↔release↔probe-done の交互のみと記載されているため、
**本件の月占有は召喚戦闘ではなく待機解除ループと読むのが最有力**。
ただし incident の `decisions.counts`（`battle_*`・`egg_battle`・
`monster_skill_stall` の件数）を直接確認できていないため、完全な排除ではない。

## 4. #5326・#5332 との異同（検証観点3）

- **#5326（`chikujou_available_at_end`、skipped）:** 別の監査系列。
  築城月末評価（month_audit.py:1838 付近、`chikujou_available_at_end` /
  `chikujou_cancelled_with_resources` kind、:3959・:4017-4018）から起票される
  「築城可能なまま月一終了」で、出撃経路とは独立の機構。同族の既知欠陥は
  #5313（VERIFY-5313 で (a) 実欠陥、#5329 で修正）として別途処理済み。
  #5324 との因果関係は確認できない（`defense_wait` hold 中も築城フローは
  `_off_chart` とは別経路で動く）。skipped の扱い詳細は issue 本文未取得の
  ため不明。
- **#5332（`zero_sortie`、ch11 17-12、`order_start` 12 / 発射 0）:**
  同一 kind・同一 signature 系列。`sender_min` は ch11 でも 2
  （guard_free は ch9 のみ、hanjuku_chart.py:1308-1318）で、
  `_defense_gate`/`interim_candidates`/`probe_stuck` は全て章非依存の
  コードのため、**同一の probe 解除ループ機構が ch11 でも成立しうる**。
  incident JSON が無いため同機構と断定はできないが、`order_start` 12 回と
  いうパターンは本件と同型であり、v1451 修正は章非依存なので仮に同機構なら
  そのままカバーする。ローカルに `verify/5332` ブランチがあるが
  VERIFY-5332.md は未作成（v1446 時点の main から切られている）。

## 5. バージョン差分 v1445（ラン時点）→ v1450（検証時点の origin/main）

`git log` 実測。v1445（01d44e161）以降の origin/main 上の変更で本件機構に
関わるもの:

| コミット | 内容 | 本件との関連 |
|---|---|---|
| 57be9b382（v1446 #5308） | boss companion 待ち・城ジャンプ停滞の有界化 | ボス系ゲートで、defense_wait/probe 経路は不変 |
| 23c108d62（v1446/v1447 #5325） | パスワード入力を実測リスト順に | 無関係 |
| b68f1fd20（v1448 #5336） | 計画が空でも安全な札を1枚自動携行 | 出撃が成立した場合の振る舞いで、本件の不成立経路は不変 |
| cc2b2d969（v1449 #5338） | 築城の画面遷移断念を skip 理由として記録 | 築城監査側で無関係 |
| c01748172（v1450 #5345） | パスワード両ディスパッチ経路統一 | 無関係 |

**`_defense_gate` の probe 解除も `interim_candidates` の `reachable` 限定も、
origin/main 時点で未修正**（replan.py:2124・:1952-1959 を直接確認）。
修正は `origin/fix/5324-hanjuku-stuck-probe`（v1451）にのみ存在する。
（検品ゲート追記：同ブランチは **PR #5343 として 12:29:30 JST にマージ済み**。
main は `2cbe3542f`、v1451。ゲートで差分を確認し、probe_only 判定と
no_attack_leg 時の本拠優先が両欠陥を塞ぐことを追認した。）

## 6. 修正案（(a) のため記載）—— オーナー v1451 ブランチの内容と一致

`a4f056e17` の変更を検証・追認する（**PR #5343 として 12:29:30 JST に
マージ済み**。ゲートで main `2cbe3542f` の差分を確認し、新規テスト
`tests/test_hanjuku_defense_wait_probe.py` と `test_hanjuku_month_audit.py`
は 142件 green）:

1. `_defense_gate`: `interim_candidates` の結果が全て
   `roster_probe is True` の注文なら `defense_wait` を解除しない
   （`probe_only` 判定を追加し、解除・return None をスキップして cycles
   加算と break 評価へ進ませる）。probe 自体は従来どおり実行される。
2. `interim_candidates`（probe_stuck 系）: `reachable` が空の城も
   読み取り専用 probe の対象にする（行軍しないため実測ルートは不要、
   名目敵城を target に据える）。ルートなし城のうち本拠（`_arrival_castle`、
   加入将軍が現れる城）を優先順位で前に出す。
3. 新規テスト `tests/test_hanjuku_defense_wait_probe.py` と
   診断収集の `collect_diagnostics.py` 1行追加。

本検証でのコード確認上、この修正方針は両欠陥を正確に塞ぐ。
残留意点: 修正後も `country_general_count` が正確でない場合や、
本拠読取後も加入将軍が実際にはいない場合は月内出撃ゼロが続きうるが、
それはその時点で真の合法ゼロとなる（`chart_defense_wait_eval` の cycles が
可視化されるので再判定が可能）。

## 残る不確定点

- **監査 JSON・ラン成果物の未取得:** `g1342-1fe0734c` の
  `hanjuku_month_audit.json`/`hanjuku_decisions.jsonl` は VM 側にあり直接確認
  できない。「6 回の `order_start` が全て roster_probe」はオーナーの判断ログ
  実測記録（修正ブランチ同梱ドキュメント）への依拠であり、本環境での再確認
  ではない。`decisions.counts` に `launch_interrupted_by_battle`・`egg_battle`・
  `monster_skill_stall` 系が存在するかは未確認（§3 の召喚戦仮説の完全排除には
  必要）。
- **occurrence `32662d219ec7118b8d31` の対応月:** オーナー記録では月内ゼロが
  4-11〜5-3 の5か月続いたとあり、本 issue が fingerprint 集約で複数発生を
  抱えている可能性がある。issue 本文未取得のため集約状況は未確認。
- **fingerprint ハッシュ:** 計算式（§1）は確認したが `054e5049579e3c90` の
  再計算は python3 拒否のため未実施。

## 付録: issue 数値 → コード対応表（origin/main `c01748172`）

| issue の値 | 対応箇所 |
|---|---|
| order_start 6 | `flows/map.py:2664`（roster_probe を含む全 dispatch で記録） |
| order_launched 0 | probe は `deploy.py:2347` `_finish_order('done')` → `order_done`。`order_launched` は `map.py:2956-2965` `_commit_departure` → `_finish_order('launched')` のみ |
| 判定 zero_sortie | `month_audit.py:3385-3386` |
| signature「出撃開始後に発射されない」 | `month_audit.py:2924` `order_start_without_launch` → :271 `departure_failure` → :188 ラベル |
| fingerprint | `month_audit.py:3032-3034` `sha256("3\|zero_sortie\|departure_failure")[:16]` |
| 出撃元2将軍制約 | `hanjuku_chart.py:1303-1305` `sender_min`=2（ch9 以外）、`_sole_starved` `replan.py:2082-2095` |
| 待機の解除バグ | `replan.py:2124-2130` `if interim_candidates(mem):` → `chart_defense_wait_released` |
| 待機の月次評価 | `replan.py:2138-2150` `chart_defense_wait`/`chart_defense_wait_eval`/`break` |
| probe 対象のルート限定 | `replan.py:1952-1959` `if not reachable: continue` |
| probe 注文生成 | `replan.py:1903-1997`（`probe_stuck_1`、`roster_probe: True` は :1996） |
| probe 完走 | `deploy.py:2324-2348` `roster_probe_complete` → `order_done` |
| hold 中の早期 return | `replan.py:3066-3068` |
| 修正ブランチ | `origin/fix/5324-hanjuku-stuck-probe` `a4f056e17`（v1451、**PR #5343 として 12:29:30 JST にマージ済み**） |
