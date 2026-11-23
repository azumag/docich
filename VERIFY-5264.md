# VERIFY-5264 — [半熟英雄] 月内出撃ゼロ: ch2 出撃開始後に発射されない

対象: docich#5264（監査自動起票）/ ラン `g1342-1fe0734c` /
`hanjuku-chart-v1423-sortie-cell-after-battle` / 月 `ch2 2-8` /
fingerprint `601cb9e3e8188b68`
調査基盤: worktree `~/work/docich-5264`（ブランチ `verify/5264`、main ＝ `b649f8146`、
v1427・PR #5267 マージ済み）。read-only 調査。`gh` は本環境で実行不可のため、
関連 issue 本文・監査 JSON は未取得。検証はリポジトリのコード・git 履歴・
テスト・ユーザー提供の issue 数値に基づく。

---

## 結論

**(b) 設計どおり。** 本件は #5190・#5191・#5213 と同じ「合法的に出撃源ゼロ」の
(b) 家族に属し、実欠陥ではない。

- 記録された `order_start`（step `A:fd5def79:Me8253d`、アルマムーン→アウスパジア）は
  **`roster_probe: True` の読み取り専用指示**であり、発射を目的としない。実際に
  `roster_probe_complete` → `order_done` → `[B,B]` と設計どおり完走している
  （`src/docich/hanjuku/flows/deploy.py` L2323-2348）。経路は停止していない。
- 真の停止点は probe 完走後の `_defense_gate` → `chart_defense_wait`（'hold'）。
  記録理由は「出撃元に将軍2人以上を残す制約で候補0件。2人目の参入を待つ」
  （`src/docich/hanjuku/strategy/replan.py` L2140-2142）で、issue 記載と一致する。
- この hold は**正しい**。在城アルマムーン 1 城・将軍どうしのみ（自由な将軍 1 名）
  という世界状態では `sender_min(2) == 2`（`src/docich/hanjuku_chart.py` L1303-1305）
  を満たす出撃元が存在せず、`_sole_starved`（replan.py L2082-2097）が成立する。
  ゲーム自体が「最後の 1 将軍がいる城からの出撃」を拒否する（replan.py
  L1810-1819・L1200-1204 のコメント、オーナー確認 2026-11-08）ため、
  これはボット側のバグではなくゲームの制約。
- さらにオーナー決定（2026-11-06、#3566／`b87d0215d`）で「唯一城を空にする」
  月次フォールバック出撃も廃止済み。`allow_sole` は dead parameter で、
  `interim_candidates(mem, allow_sole=True)` でも候補は空になる
  （`tests/test_hanjuku_defense_wait.py` L91-101 で保証）。
- 出撃ゼロの原因は dispatch 停滞でも `next_order` 抑制でもなく、
  **合法候補の不在**。2 人目の将軍が参入すれば `chart_defense_wait_released`
  （L2125-2128）で通常運行へ復帰する解放経路が存在する。

---

## 1. 監査ロジックの検証（issue 数値との対応）

`src/docich/hanjuku/month_audit.py`:

| 数値・事実 | コード | 検証結果 |
|---|---|---|
| 出撃確定 0 / 発射 0 | `zero_sortie` は `departures == 0` かつ `confirmed == 0` で完全観測月に発火 | 月 2-8 が 771 観測・1171.4 秒で完全観測 → 検出は正当 |
| order_start 4 / order_launched 0 | `_signals` L2854-2855: `order_start` があり `departures == 0` → `order_start_without_launch` | 機械的に成立。ただし 4 件は読取専用 probe の dispatch を含む（§2） |
| signature = departure_failure | `PRIMARY_SIGNALS` L266-267: `order_start_without_launch` → `departure_failure`（ラベル「出撃開始後に発射されない」L186、issue タイトルと一致） | `plan_starved`（L265）が先に評価されるため、`plan_all_settled`/`no_orders` は非発火 → 月末時点の plan `orders` に未終了ステータスが 1 件以上存在したことを意味する |
| fingerprint 601cb9e3e8188b68 | `_fingerprint` L2963-2965: `sha256(f"{chapter}\|{kind}\|{primary}")[:16]` → `sha256("2\|zero_sortie\|departure_failure")[:16]` | 式はタイトルの signature と整合（ハッシュ値の再計算は本環境で未実施） |
| `no_source` 系の併記 | `_signals` L2880-2883: 全在城リストの最大人数 ≤1 → `no_source` | 「在城 1 城・将軍どうしのみ」（= 1 名）なら発火するはず。signature とは別に signals 内に残るため解釈可能 |
| severity high | `_confidence` L2900-2925: 観測ギャップ・欠落のみ減点 | 771 観測・完全月 → high 正当。`auto_file` 条件（L3068-3069: zero_sortie かつ high）も成立 |

**判定: 監査の分類・信頼度は設計どおり正しい。** 1 点だけ注意: `departure_failure`
のシグネチャ名は「probe の dispatch を出撃失敗と誤読」しうる表現だが、
signals に `no_source` が併記されるため誤分類ではない。

## 2. order_start 後の経路実測 — どこで止まったか

issue 記載の系列 `order_start → select_to_hero → arrival_unverified →
roster_probe_complete → order_done → chart_defense_wait` をコードで追跡:

| 段階 | コード位置 | 意味 |
|---|---|---|
| `order_start` | `flows/map.py` L2597-2601 | dispatch 記録。`roster_probe: True` 付き（L2600）。`source_probe` なら `arrival_probe_tick` を armed（L2594-2595） |
| `select_to_hero` | `os_layer/navigation.py` L135 / L152 | カーソル喪失時の SELECT 復帰。`NAV_RECOVER_LIMIT` 超過で `'infeasible'`（L127, L147-148）と有界 |
| `arrival_unverified` | `os_layer/navigation.py` L171-195 | 屋根アンカー未確定の保留。`UNVERIFIED_LIMIT = 3`（L22）を超えれば `'arrived'` で確定（L193-195）と有界 |
| `roster_probe_complete` | `flows/deploy.py` L2342 | `general_list` で `_observe_garrison`（L2322）後、`roster_probe` 注文は将軍選択・派遣をせず読取記録して `[B,B]` で離脱（L2323-2348） |
| `order_done` | `flows/deploy.py` L2346 → `strategy/orders.py` L2430 | `_finish_order(mem, 'done')` → `order_done` 記録。**ch2 で `order_done` に至るのはこの roster probe 経路のみ**（他の `order_done` 記録箇所は corridor/ch9 `guard_free` 限定） |
| `chart_defense_wait` | `strategy/replan.py` L2140-2142 | 次 tick の `_off_chart` → `_defense_gate` で 'hold'（後述 §3） |

step id `A:fd5def79:Me8253d` の構造:
- `A:` = 採用済み機械計画の実行プレフィックス（`chart_adjust` の PLAN_PREFIX）
- `Me8253d` = `_mechanical_step`（replan.py L2686-2724）のハッシュ id。
  `roster_probe` 注文には `:probe:<stamp>` サフィックスが付く（L2698-2712）。
  これは「読取 probe の 'done' が、それが検証する本来の出撃指示の終了ステータスを
  継承しない」ための分離であり、設計どおり。

probe の起票元は `interim_candidates` 内の probe 系フォールバック
（`probe_stuck` L1896-1997: `country_general_count > located` のとき最も古い
到達可能在城を読む。`source_probe`+`roster_probe`+`MOVE_ANY_GENERAL`、
L1986-1997）、または `roster_read_due`/到着確認 probe（L1829-1842）。
失城 3 城（ハドリバーグ・ドミノーラ・フーリック）を踏まえると、未所在将軍が
`total > located` を満たす `probe_stuck` 系が最有力だが、id から種別は一意に
決められない。いずれも同じ読取専用の終端経路を取る。

**結論: order_start 後の経路はどこにも「止まっていない」。** probe は読取を
完了して正常終了した。出撃が生まれなかったのは probe の仕様（読取のみ）であり、
その後の候補評価で合法的な出撃源が存在しなかったため。

## 3. `chart_defense_wait` の成立条件と正当性（要件 4）

`_defense_gate`（replan.py L2105-2152）の評価順:

1. L2124-2129: `interim_candidates(mem)` が非空 → `defense_wait` を解除して
   `chart_defense_wait_released` を記録し通常経路へ。
2. L2130-2131: `_sole_starved(mem)` が偽 → 通常経路へ。
3. L2138-2142: 初回 → `mem['defense_wait']` 作成 + `chart_defense_wait` 記録。
   observed_metric に owned・garrison・month・prospects（`_joiner_prospects`
   L2100-2102: 未配置将軍 or 行軍中の将軍の有無）を含む。
4. L2143-2148: 月が変わるたび `chart_defense_wait_eval`（cycles 加算）。
5. L2149-2152: `cycles >= DEFENSE_WAIT_MAX_CYCLES`（2、L2079）または
   `cycles >= 1 かつ prospects なし` → `'break'`。`_off_chart_mechanical`
   （L3029-3030）は break でも `allow_sole=False` で `_mechanical_interim` を
   呼ぶため、空城化出撃は発生せず、候補が無ければ `chart_interim_hold`
   （L2182-2193）として可視化される。

`_sole_starved`（L2082-2097）の成立要件:
- 未占領の非ボス城が存在する（L2091）— アウスパジア等が該当 → 真
- 全 owned 城の自由将軍数 ≤1 かつ 1 城は丁度 1（L2093-2097）—
  アルマムーンのみ・将軍どうしのみ（free = 1）→ 真

`_off_chart`（L3067-3069）: `gate == 'hold'` かつ `_plan_pending` なし →
即 return（モデル要求なし・per-tick churn なし）。これが月内の「待ち」の実体。

**判定: 「将軍 2 人以上を残す」制約は本状況で正しく機能している。**

- `sender_min` は ch9 以外全章で 2（`hanjuku_chart.py` L1303-1305、
  `guard_free` L1308-1318）。ch2 は対象外。
- 「唯一城でも空にしない」はオーナー決定で廃止済み（`b87d0215d`、
  #3566）。テストで `allow_sole=True` でも `interim_candidates == {}` が
  保証される（`tests/test_hanjuku_defense_wait.py` L91-101、
  L45-66、L69-82）。
- 4 回の order_start は probe 繰り返しだが、`arrival_probe_tick` の armed
  （deploy.py L2340、オーナー修正 2026-11-23）と
  `STALE_ROSTER_PROBE_INTERVAL = 300`（replan.py L2001）・
  `PROBE_INTERVAL = 200`（routes.py L17）で有界化済み。
  両修正とも v1423 より前に main へ入っている（下記 §5）。

## 4. 関連 issue との比較（要件 2・3）

### #5257（同ラン g1342・同チャート・ch2 2-6 の zero_sortie）

issue 本文は取得不可（`gh` 実行不可）。コード上の見立て:

- 同一ラン・同一チャートバージョン → 同一の audit・戦略コードで検出された
  同族の zero_sortie。月 2-6 は 2-8 の 2 ヶ月前で、3 城喪失（ハドリバーグ・
  ドミノーラ・フーリック）の途上か直後の状態と推定される。
- 2-6 で既に zero_sortie が起き、2-8 でも同機構で止まったとすれば、
  同一の「保有城・将軍の減少 → 合法出撃源の枯渇」系列の継続を示唆する。
  異機構（dispatch 停滞等）かどうかは #5257 の監査 JSON
  （signals・decisions.counts・plan.orders）が必要で、現環境では判定不能。

### #5236

issue 本文未取得。git 履歴では v1417 系（`38cf843b0`、#5237）が
「出撃・帰還・野営・経路の停滞修正を現行コードへ再設計」で、この時期の
停滞系修正の一連に相当する可能性があるが、確定的な同定はできない。

### (b) 家族（#5190・#5191・#5213）との切り分け

「合法的に出撃源ゼロ」の判定基準はコード上明確:

- 全 owned 城で自由将軍 ≤1（`sender_min` 未満）→ 通常出撃不可（L519-571
  `_wave_pick`、L66-116 `_interim_source`、L1200-1204 `_boss_wave_pick`、
  全て `need = chart.sender_min(chapter)` または `need = 2`）
- 唯一城を空にする例外なし（2026-11-06 オーナー決定、`b87d0215d`）
- probe 系も `arrival_probe_due`/`roster_read_due`/`STALE_ROSTER_PROBE_INTERVAL`
  の条件を満たさなければ起票されず、起票されても発射しない

本件は上記すべてを満たす → **(b) 家族に属する**。VERIFY-5077（(a) 実欠陥）と
異なるのは、実行可能な pending 注文が 600 秒間一度も dispatch されなかった
という「出撃ディスパッチ停滞」が存在しない点（order_launched=0、
probe は完走、hold は設計上の待機）。

## 5. バージョン差分 v1423（ラン時点）→ HEAD（v1427）

`git log`（前スレッドで確認済み）によると、v1423（`6d00a1f15`、ch9 主人公移動
修正 #5255）から HEAD（`b649f8146`）までの差分は:

| コミット | 内容 | 本件との関連 |
|---|---|---|
| `b98c5486c`（v1424 #5262） | オシロガ発射確認調整（ch9 系） | 無関係 |
| `df9032190` | ゾンビ召喚系 | 無関係 |
| `b649f8146`（v1427 #5267） | 戦闘・中断後の出撃先セル固定 | ランのチャート名 `sortie-cell-after-battle` と同名の変更系列だが、本件の probe→defense_wait 経路は不変 |

**関連する防御待ち・probe 修正はすべて v1423 以前に main 入り済み:**

- `278915fa1` #2846: `chart_defense_wait` 本体（1 将軍制約での参入待ち）
- `b87d0215d` #3566（オーナー 2026-11-06）: 月次 break での空城化出撃を廃止
- `38cf843b0` v1417 #5237: roster probe 完走時の `arrival_probe_tick` armed
  （probe の再発行ループ抑止。deploy.py L2340）
- `a58f9162a` #5182: `probe_stuck` の行軍中将軍二重計上修正（#5153 の
  72 probe 多発の防止）— v1423 マージ（11-24 02:37）より前の 11-23 19:47 に
  main 入り

**よって現行コードが本件を「既に修正済み」という形ではなく、
本件の停止挙動自体が設計の意図どおり**である。main への修正要求なし。

## 6. 残る不確定点

- **監査 JSON・ランタイム成果物の未取得**: `g1342-1fe0734c` の
  `hanjuku_month_audit.json`/`hanjuku_decisions.jsonl`/`hanjuku_frames/` を
  本環境で直接確認できていない。issue 記載の 4 回の `order_start` がすべて
  probe 系であったか（例に挙がる `A:fd5def79:Me8253d` は確実に probe）、
  `plan.orders` の未終了ステータスが何であったか（`departure_failure` が
  primary になったことから 1 件以上存在する）は、issue スナップショットの
  記載値からの推論であり、実測確認にはラン成果物が必要。
- **#5257・#5236・#5190・#5191・#5213 の issue 本文**: 取得不可。
  §4 の比較はコードレベルの同定にとどまる。
- **fingerprint ハッシュ**: 計算式（§1）はコードで確認したが、
  `601cb9e3e8188b68` の再計算は本環境で未実施。

## 付録: issue 数値 → コード対応表

| issue の値 | 対応箇所 |
|---|---|
| order_start 4 | `flows/map.py` L2597-2601（`roster_probe` 注文でも記録） |
| order_launched 0 | probe は `_finish_order('done')`（deploy.py L2346）で終了し `launched` にならない |
| 経路の各決定 | §2 の表（navigation.py L135/L152/L186, deploy.py L2342/L2346, replan.py L2140） |
| 「将軍2人以上を残す制約」 | `hanjuku_chart.py` L1303-1305 `sender_min`、replan.py L110-113・L1201-1203・L1813-1816 のコメント |
| 在城 1 城・将軍どうしのみ | `_sole_starved` L2082-2097 の成立条件（全 owned ≤1 free かつ 1 城 =1） |
| 失城 3 城 | `_sole_starved` L2091 の「未占領の非ボス城が存在」条件を満たす背景 |
| fingerprint 601cb9e3e8188b68 | `_fingerprint` L2963-2965: `sha256("2\|zero_sortie\|departure_failure")[:16]` |
| signature「出撃開始後に発射されない」 | `PRIMARY_LABELS` L186 `departure_failure`、PRIMARY_SIGNALS L266-267 |
| `no_source` 併記 | `_signals` L2880-2883 |
| probe 有界化 | `routes.py` L17 `PROBE_INTERVAL`、replan.py L2001 `STALE_ROSTER_PROBE_INTERVAL`、deploy.py L2340 `arrival_probe_tick` |
