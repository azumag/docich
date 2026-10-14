# 実装設計書: 改善ループの統計的判定 + 即死ゲーム分離

- 作成: 2026-08-20（opus Plan サブエージェントによる設計。コードポインタ・VM 実測事実はメインセッションで裏取り済み）
- ステータス: **Phase 0 実装完了・VM反映済み。Phase 1（即死観測・quarantine）実装完了・opus 3回レビュー済み・VM反映済み・`INSTADEATH_SPLIT_ENABLED=1` で有効化済み（2026-08-20 14:35、本番データでの観測動作を実測確認）。Phase 2 以降は未着手**
- 背景: 戦績分析（handoff §32 参照）で「改善ループが統計的に区別できない差で採択・棄却・ロールバックを繰り返している」ことが判明したため

## Phase 0 実装状況（2026-08-20）

`lib/eval_stats.py`（新規）+ `tests/test_eval_stats.py`（50件）を実装。既存5箇所の quantile/metrics/composite_score 埋め込み実装とのビット互換性、`decide()`（統計判定ゲート）、`classify_instadeath()`/`fisher_one_sided()`（即死分類）を、opus による3回のコードレビュー（バグ発見→修正→確認レビュー→さらなる指摘発見→修正→最終確認）を経て検証済み。`core/config.sh` に STAT_*/DEAD_*/IMPROVE_FUTILITY_* を新設（全て off/0 既定）、既存の regression/ranking 系13変数を `${VAR:-default}` 化。`eloop.sh` に `LAST_TURNS` export、`strategy/improve.sh` の `_seed_current_strategy_run_from_rolling()` の `[-20:]` ハードコードを `CURRENT_RUN_SCORE_KEEP` に修正（§13 既知負債の解消、これのみ実挙動変更）。

**Phase 1 配線時に対応すべき次善事項（今回のレビューでは非ブロッキングと判定済み）**:
1. `classify_instadeath()`: 5番目の判別器（近全滅レート）追加により、`ref_flags` 付きで呼んでも HARNESS 判定が先に確定すると Fisher 検定（STRATEGY 判定）まで到達しない場合がある。配線時は Fisher を先に評価するか、`fisher_p` を常に detail に含めるよう変更を検討。
2. `decide()` の NOT_A_LOOK 新設分岐（HARD look 実行済みだが閾値未達）と `INSUFFICIENT_CURRENT`/`INSUFFICIENT_REFERENCE` の reason 文言を直接 assert するテストが薄い（動作はライブ確認済みだが回帰保護が弱い）。
3. `decide()` の PROMOTE reason 文言 "soft-layer lcb above delta_promote (at alpha_promote)" が若干不正確（実体は alpha_promote 側の値）。

VM への反映（`core/config.sh` を含むため全 worker 完全再起動が必要）は、作業中に**別セッションがこの同じ VM に並行してデプロイしている痕跡**（無関係なコミットが割り込み、未コミット編集4ファイルが一時消失する事故が発生。復旧・再コミット済み）を確認したため一旦保留したが、11:45時点で並行活動が収まっていることを確認した上でユーザー承認を得て **2026-08-20 12:10-12:19 に反映完了**。手順・実測検証結果は handoff.md §33 を参照。soren_loop/improve_daemon を SIGTERM→supervisor respawn で完全再起動し、反映後2試合の正常完了とエラーなしを確認済み。

## Phase 1 実装状況（2026-08-20）

`lib/instadeath_monitor.py`（新規）+ `tests/test_instadeath_monitor.py`（18件）+ `tests/test_instadeath_split.py`（15件、実バッシュ経由の統合テスト）を実装。`strategy/regression.sh`/`strategy/improve.sh` の両関数に `progress` 配列（scores と長さ不変条件）と quarantine 退避ロジックを対称に追加。opus 実装計画→実装→レビュー1（B1-B3ブロッキング3件+次善5件発見）→修正→レビュー2（全7件解消を実測確認、うち2件は「修正を一時的に戻すとテストが実際に落ちる」ことまで検証）という3ラウンドを経て収束。詳細は handoff.md §34、コミットメッセージ（`019738674`, `3071a6f91`）参照。

**Phase 2 で対応すべき事項（今回は非ブロッキングと判定済み、レビュー3回目で発見）**:
1. **【最重要】B3 の rate ゲート（`_classify()` の `rate <= dead_quarantine_rate → NORMAL`）が STRATEGY 判定も一緒に塞いでいる**。Phase 1 では `classify_instadeath(cur_flags, None, ...)` と `ref_flags=None` で呼ぶため実害ゼロだが、Phase 2 で `ref_flags`（anchor の即死率）を配線すると、即死率20%・anchor 1%のような「戦略起因の劣化」でも `rate(0.20) <= 0.30` でゲートに引っかかり Fisher 検定に到達せずサイレント no-op になる（R3=export漏れと同型の罠）。配線時は rate ゲートを「quarantine (`q["active"]`) の発動可否」だけに限定し、verdict 算出自体（STRATEGY判定含む）は塞がない形に分離すること。
2. `DEAD_QUARANTINE_CLEAR_WINDOW < DEAD_QUARANTINE_WINDOW` の設定だと start/clear が毎tickフラッピングする（既定値20/20では発生しない）。`CLEAR_WINDOW >= WINDOW` のクランプ、または「verdict が HARNESS のままなら再発動抑制」のガードを検討。
3. `_instadeath_observe`（monitor書き込み失敗時のフォールバック `divert=0`）と `_instadeath_read_state`（`state()` 読み取り成功時は `active` をそのまま反映）の非対称: monitor ファイルが「読めるが書けない」という狭い条件で rolling/current_run 間に n 非対称が起きうる（発生確率は低い）。
4. `INSTADEATH_MONITOR_UPDATE=0` の経路（wildcard-parallel・repair）は `diverted_total` をインクリメントしない（`quarantine_meta.count` 側は正しい）。診断表示にのみ影響。
5. `_merge_rolling_scores_on_normalize` は stale hash 側の `quarantined_scores`/`quarantined_progress` を保持しない（`games_total` は合算されるのに退避記録は失われる）。意図的な仕様として許容中。

## 0. 事前実測で判明した決定的事実（設計の前提）

### 0-1. 戦略間の「真の」スコア差は現状ゼロ（τ² ≒ 0）

`tmp/state/rolling_scores.json` の20 hash の分散成分分解（即死除外後）:

| 量 | 実測 |
|---|---|
| hash ごとの中央値の観測SD | 496 |
| 全 hash 同一実力と仮定した理論サンプリングSE（中央値, 平均n=27） | 838 |
| hash ごとの平均値の観測SD | 655 |
| 同上の理論SE（平均値） | 668 |
| τ²（真の戦略間分散）の推定値 | 負（平均ベース −17,736 / 中央値ベース −455,770） |

観測ばらつきが「全員同一実力」の理論ノイズより小さい。**現在プールの20戦略に統計的に検出可能な実力差は存在しない**。採択・棄却・ロールバックは実質コイン投げ。

**含意**: 検定導入後は「有意差なし」がほぼ常時の結論になるため、「有意差なし時のデフォルト挙動」（A-2）が本設計の最重要論点。

### 0-2. 現行ルールの誤ロールバック率は 17.8〜23.6%

直近クリーン期間（2026-08-11〜20, n=3,698）の実分布から同一戦略同士をブートストラップし、現行 `check_regression`（breach_count>=2, gap 1000/800/1600, n=12..100 で毎試合チェック）を3,000試行:

| anchor の n | 誤ロールバック確率 | 初回発火の中央値 n |
|---|---|---|
| 20 | 0.236 | 12 |
| 100 | 0.178 | 12 |

同一戦略でも約2割がロールバックされ、大半が n=12 の初回チェックで発火。多重比較の主因は「週20戦略」ではなく**1戦略あたり88回の逐次 peeking**。

### 0-3. anchor 昇格の winner's curse が閾値を単独で超えている

`_refresh_best_strategy_anchor()` は `MIN_GAMES_FOR_BEST_ROLLBACK=12` 以上の全 hash から comp 最大を選ぶ。同一実力の候補 K 個から最大を選んだ場合のバイアス（実測分布シミュレーション）:

| anchor の n | 候補 K | comp バイアス | p50 バイアス |
|---|---|---|---|
| 12 | 5 | +907 | +1,199 |
| **12** | **20** | **+1,585** | **+2,004** |
| 20 | 20 | +1,195 | +1,545 |
| **100** | 20 | **+511** | **+678** |

`REGRESSION_MIN_COMP_GAP=1000` / `REGRESSION_MIN_P50_GAP=800` に対し、**anchor の選択バイアスだけで両閾値を超える**。anchor と実力が同一の戦略でも期待値として breach 2つが立ちロールバックされる = 「改善→粛清」ラチェットの正体。

実例（VM `tmp/state/last_rollback_analysis.md`, 2026-08-19 04:11, メインセッションで実在確認済み）:

```
- reverted_from: 80e1c297a82a  reverted_to: 95b4310bee23
- target_note: anchor_top1 hash=95b4310bee23 comp=11904.8 p50=13205.0 p25=9641.5 n=12
- trigger: soft_fail+anchor_direct
- current: comp=10008.7 p50=10524.0 p25=9010.2 mean=11232.6 n=20
- current_gap_vs_anchor: comp=1896.1 p50=2681.0 p25=631.2 breaches=2/2
- metric_gap_vs_target: mean=-251.9   ← 平均差は誤差内
```

anchor は n=12 で p50=13205（プール中央値 +3,213 ≒ +2.9SE）。粛清された側の直近スコアには 22795/16555/14292 という右裾（T15到達）が含まれていた。

さらに `comp = 0.55*p50 + 0.30*p25 + 0.15*lcb` は右裾を構造的に捨てる。T15/T16 という上位目標のシグナルが乗る唯一の場所を無視する目的関数の設計ミス。

### 0-4. 即死ゲームはほぼ確実にハーネス障害。しかも既に収束済み

`score_history.txt` と `eval_score_history.txt` は 2026-03-23 以降 1:1 対応（offset 8,751）。

**(a) eval 空間で明確な二峰性**: 2026-06 以降 17,357 試合で eval [2500,3000) は 1件、[2500,4000) の谷全体で 4件（0.023%）。`DEAD_EVAL_THRESHOLD=3000` で誤分類は実質ゼロ。即死側の60%（1,504/2,504）は raw=0 かつ eval=0（1手も置けていない）。

**(b) 極端なバースト性 = ハーネス起因**: 即死率 p=0.1443 の iid 期待連長 1.17 に対し実測平均連長 **11.13**（比 9.5）、最大連長 711（≒41時間連続）。

**(c) 直近の即死率は 0%**: 8/6 が 67.3%（障害日、571/848）、8/7 50%、8/8 19%、**8/11 以降 0.0%**。「8月の即死12.7%」は8/6の1日でほぼ説明される。

**(d) 即死除外後も後退は残る**: 4月→8月で eval 中央値 11,394→10,009（−12%）、raw 中央値 1,416→1,112（−21%）。SD は 3,000〜3,700 で安定。即死分離は「平常時ゼロ、障害時に SD −40%」の保険。

---

## A. 統計的有意性の導入

### A-1. 推奨方式: Winsorized 平均差 + 固定 look 群逐次検定（Bonferroni）+ 二層閾値

**手法選定**（VM は scipy/numpy なし・Python 3.12.3 実測。標準ライブラリ完結が必須要件）:

| 方式 | stdlib | 逐次peeking耐性 | 効率 | 判定 |
|---|---|---|---|---|
| Welch t 検定 | ○ | ×（88回peekで実効α>30%） | 中 | peeking対策必須 |
| Mann-Whitney U | ○ | × | 右裾を順位に潰す（0-3の再現） | 不採用 |
| ブートストラップCI | ○ | × | 非決定的・shadow比較が再現不能 | 補助のみ |
| 常時有効信頼列（mSPRT） | ○ | ◎ | 半径が固定標本の2.1倍（n=100で1,320 vs 620）＝検出力半減 | 次点 |
| **群逐次（固定look+Bonferroni）** | ○（`statistics.NormalDist.inv_cdf`） | ◎（look数が有界） | CSより40%タイト（n=100で803） | **採用** |

**検定統計量: Winsorized 平均（5%/95%）**。SE のブートストラップ実測（n=100 時）: comp 276 / p50 334 / mean 310 / **wmean 267**（全 n で最小）。右裾（T15 の 23,000〜30,000）を捨てずにクリップ保持 → comp の右裾切り捨て問題を緩和。comp は廃止せずランキング・ログ互換用に温存、**判定ゲートだけ差し替え**。

**決定ルール（擬似コード）**:

```
alive_c = [s for s in current.scores if s >= DEAD_EVAL_THRESHOLD]
alive_a = [s for s in anchor.alive_scores if s >= DEAD_EVAL_THRESHOLD]

if current_hash == anchor_hash:            -> NOOP
if len(alive_a) < STAT_ANCHOR_MIN_N:       -> INSUFFICIENT_REFERENCE（スコア起因の粛清をしない）
if quarantine_active():                    -> QUARANTINED

lo, hi = winsor_limits(alive_a + alive_c, 0.05, 0.95)   # 両腕プールから1回だけ算出
mc, vc, nc = wstats(alive_c, lo, hi)
ma, va, na = wstats(alive_a, lo, hi)
delta = mc - ma
se    = sqrt(vc/nc + va/na)          # Welch

# HARD 層: 明らかな破壊を早く止める
if nc >= STAT_HARD_MIN_N and nc % STAT_HARD_LOOK_STRIDE == 0:
    zh = NormalDist().inv_cdf(1 - STAT_ALPHA_HARD / STAT_HARD_LOOK_K)
    if delta + zh*se < -STAT_DELTA_HARD:   -> REGRESSION(mode=stat_hard)

# SOFT 層: 事前登録 look のみ（既定 24,48,72,100）
if nc in STAT_LOOKS:
    zs = NormalDist().inv_cdf(1 - STAT_ALPHA_SOFT / len(STAT_LOOKS))
    if delta + zs*se < -STAT_DELTA_SOFT:    -> REGRESSION(mode=stat_soft)
    if delta - zs*se > +STAT_DELTA_PROMOTE: -> PROMOTE_CANDIDATE
    if (ma - mc) + z_ni*se < STAT_DELTA_HARMLESS: -> NONINFERIOR（futility stop）

-> INCONCLUSIVE
```

**運用特性**（実測分布・3,000試行、anchor n=100, LOOKS=(24,48,72,100), HARD は n≥16 の 8試合ごと）:

| δ_soft | δ_hard | FP(真差0) | P(δ=1500) | P(δ=2000) | P(δ=3000) |
|---|---|---|---|---|---|
| **500** | **2000** | **0.004** | 0.503 | 0.847 | 0.997 |
| 0 | 2000 | 0.044 | 0.910 | 0.990 | 1.000 |

推奨初期値 δ_soft=500, δ_hard=2000 → **誤ロールバック 23.6% → 0.4%（59分の1）**。

**訂正（2026-08-20、`lib/eval_stats.py` 実装への opus コードレビュー2回で段階的に再測定）**: 上表の FP=0.044（δ_soft=0）は実装確定前の見積りで、実際の `decide()` 実装（4つの look を Bonferroni α/K で補正）では以下の通り。

- 1回目レビュー: δ_soft=0・n=24 look・同一分布ペアを300試行実測 → FP≈0.011（公称 α_soft/K=0.0125 に近い）。
- 2回目レビュー（同一手続きをより大きなサンプルで再測定）: 300試行では 2/300 = 0.0067 と1回目と再現せず、300試行はこの規模のFP率（1〜2%）を測るには小さすぎる（1σで±0.008程度）と判明。3000試行×3シードで測ると 0.0177 / 0.0140 / 0.0203、プール 156/9000 = **0.0173**。

**真値は約0.017**（0.044 でも 0.011 でもない）。4つの look が独立ではなく相関した入れ子（nc=24の標本はnc=48にも含まれる）であるため、素朴な Bonferroni の想定（0.05相当）より保守的（=誤検出が少ない）方向にずれることは変わらない。Phase 2 の妥当性検証はこの ~0.017 を基準にすること。`tests/test_eval_stats.py::test_false_positive_rate_near_nominal_at_delta_soft_zero` は300試行・緩い上限0.08で固定化しているため flaky ではないが、この上限値自体は精密なキャリブレーション基準ではなくスモークテストである点に注意。旧稿の 0.044 や中間稿の 0.011 を基準にすると誤読する。

### A-2. 「有意差なし」時のデフォルト挙動（τ²≒0 のため常時の帰結）

1. **ロールバックしない（現状維持）**。「有意に悪いと言えない」は据え置きの理由であり戻す理由ではない。
2. **非劣性確立で評価打ち切り→探索解放**。`UCB(anchor−current) < STAT_DELTA_HARMLESS` で NONINFERIOR 確定。δ_harmless=1500/α=0.05 なら真差0のとき中央値 **n=12**、90%以上が n≤40 で決着（未確定率 0.1%）。これが探索速度の解放レバー。
3. **引き分けの決着はスコアでなく目的進捗で**: soviet_count → frontier 再現数 → best_max_type → current 維持。
4. **anchor は有意差なしでは動かさない**。ただし `STAT_ANCHOR_MAX_AGE_GAMES`（既定2,000）超過かつ current が NONINFERIOR なら drift re-anchor（`reanchor_stale` を明示ログ）。

### A-3. 多重比較への対処

| 多重性の源 | 規模 | 対処 |
|---|---|---|
| **逐次 look** | 戦略あたり88回 | 固定 look（K=4）+ Bonferroni（α/K）。look 以外の n では score 判定をしない |
| 戦略横断 | 20決定/週 | FWER 制御しない（各判定はローカル行動。実効 0.4%/戦略 = 誤粛清 0.08件/週） |
| **PROMOTE** | 週20候補から max | ここだけ厳格に: STAT_ANCHOR_MIN_N=100 必須 + α_promote=0.01 + δ_promote=500 + 昇格後 holdout 48試合で再検証、消えたら自動 demote |
| 目的進捗ゲート | — | 対象外（現行どおり即時発火。ノイズ源ではない） |

### A-4. スループット制約下のトレードオフ（400試合/日, σ_w=2,667, anchor n=100）

| current n | 所要 | 検出可能 δ（K=4） | 改善サイクル/日 |
|---|---|---|---|
| 24 | 1.4h | 1,867 | 16.7 |
| 48 | 2.9h | 1,442 | 8.3 |
| 100（現行） | 6.0h | 1,162 | 4.0 |

anchor 腕 n=100 固定のため検出可能 δ には床 822 がある → `STAT_ANCHOR_SCORE_KEEP≥200` で anchor 腕も拡大。**0-1 より真の戦略間差は 500 未満なので n=100 でも n=24 でも検出できない。検出力軸は無価値で試行回数軸だけが価値を持つ** → n を 100→32 なら発見速度3倍。ただし B（即死分離）完了が前提（n=100 化の元の動機が即死問題だったため）。

---

## B. 即死ゲームの分離

### B-1. 閾値

**eval 空間で `DEAD_EVAL_THRESHOLD=3000`**。根拠: 0-4(a) の二峰性（谷への誤分類 0.023%）。rolling/current_run に保存されているのは eval スコア（`eloop.sh:654` 付近で raw + TYPE_BONUS）なので、eval 閾値なら**既存データを再解釈でき移行不要**。raw==0 かつ eval==0 は `DEAD_HARD` として別カウント。

### B-2. データ構造（後方互換）

- 既存 `scores` 配列は変更せず、**読み出し時に `alive()` フィルタ**。マイグレーション不要。
- `n_alive` と `n_total` を両方ログ・JSON に出力。
- **`_recent_archives` 依存の解消**（handoff §13 残存リスク2への回答）: `scores` と並列の `progress` 配列を記録時点で1件確定追記（s/raw/t/r/v/d/turns/ts、keep は scores と同一）。progress 窓と score 窓が構造的に一致し、`infra/cleanup.sh` の保持数と無関係になる。既存 hash はレガシー経路にフォールバック。nation_progress 再パースは新規1件のみになり CPU 減。

### B-3. 監視

`tmp/state/instadeath_monitor.json`（新規）: global window（直近400件）/ by_hash / runs / quarantine。
- WARN: 直近50件の即死率 > 0.10
- QUARANTINE: 直近20件の即死率 > 0.30

### B-4. 戦略起因/ハーネス起因の切り分け（4判別器の重み付き投票）

| # | 判別器 | ハーネスのサイン | 実測 |
|---|---|---|---|
| 1 | バースト性（連長比） | 比 > 3.0 | 9.5 → ハーネス |
| 2 | hash 境界跨ぎ | run が hash 変更を跨ぐ | 要 instrumentation（progress に hash を記録） |
| 3 | raw==0 比率 | > 0.5 | 0.60 → ハーネス |
| 4 | turns 数 | 即死の中央値 turns ≤ 3 | 要 instrumentation（LAST_TURNS） |

- `HARNESS`（2票以上）: quarantine 発動。quarantine 中の試合は scores に append せず `quarantined_scores` に退避（データを失わない）。全 score 起因ゲート無効化 + WARN 通知。8/6 型の障害で rolling 窓が壊滅する事故を構造的に防ぐ。
- `STRATEGY`: current と anchor の即死率を Fisher 正確検定（`math.comb` のみで実装可）、p < 0.01 で `mode=instadeath_regression` 発火（スコア比較と独立）。
- 解除: 直近20件の即死率 < 0.05。

**注: 即死は 8/11 以降ゼロのため、B は「再発時の保険 + 過去データの再解釈」。優先度は A より下げてよい。**

---

## C. 変更対象（要点）

### C-0. 新設 `lib/eval_stats.py` + `tests/test_eval_stats.py`

quantile/metrics/composite が regression.sh 内に多重実装されている（メインセッション実測: def quantile ×5（40/1116/2430/2675/3426行）、def metrics ×4（52/1128/2442/3438行）、composite_score ×1（2687行）＝ metrics系5バリアント）。単一モジュールに集約し、既存 comp と bit 互換を回帰テストで保証（`tests/test_eval_stats.py` で5バリアント全てを個別に参照実装と突き合わせ済み、2026-08-20 opus レビューで3438行の見落としを検出・追加）。

**訂正（2026-08-20）**: import 方式は当初 `from lib.eval_stats import ...` を想定していたが、`lib/` に `__init__.py` が無く、既存テスト（`tests/test_comment_bilingual.py` 等）の規約は `sys.path.insert(0, str(REPO_ROOT/"lib"))` + `import eval_stats`（bare import）。実装はこの既存規約に合わせた（opus レビューで確認・是認済み）。

主要 API: `alive() / winsor_limits() / wstats() / welch_bounds() / decide() / fisher_one_sided() / classify_instadeath() / run_lengths() / burst_ratio()`。

テスト: comp 数値一致回帰、真差0で FP≒α、既知δで検出力設計値、空/全即死/単一要素で不落、Fisher 既知値、0-4 実測パターンで HARNESS 判定。

### C-1. `strategy/regression.sh`

- `update_rolling_scores()`（2895行〜）: progress 1件追記、instadeath_monitor 更新、HARNESS 時は quarantined_scores へ退避、`_recent_archives` 全走査廃止（新規1件のみパース+レガシーフォールバック）、n_alive/n_total ログ。
- `check_regression()`（3095行〜）: **追加のみ・既存ゲート温存（AND 合成 = 単調に粛清が減る方向）**。`STAT_GATE_MODE=off|shadow|enforce`。enforce では既存ゲートが REGRESSION でも stat_verdict が REGRESSION でなければ `OK:stat_inconclusive` に降格。目的起因ゲートは対象外。quarantine 中は score 起因ゲート全スキップ。`[STATGATE]` ログに legacy=/stat=/agree= を並記（shadow 集計をワンライナー化）。新規 `mode=instadeath_regression`。
- `_refresh_best_strategy_anchor()`（948行〜）: min_games 12→`STAT_ANCHOR_MIN_N=100`（候補0件時は旧値フォールバック+WARN 必須）、alive フィルタ、anchor JSON に alive_scores を 200件保存。
- `_promote_current_strategy_to_anchor()`（1494行〜）: 統計ゲート（n≥100 + lcb>δ_promote + α_promote）、holdout 48試合で自動 demote、drift re-anchor 経路新設。
- ランキング系（954/2397/2660行）: lib.eval_stats.composite に差し替え（数値互換）、alive 入力。順序ロジックは不変。

### C-2. `strategy/improve.sh`

- **2277行 `seed_scores = scores[-20:]` → `[-CURRENT_RUN_SCORE_KEEP:]`**（同ブロックの `[-20:]` リテラル7箇所も。handoff §13 既知負債の解消）。progress も seed 対象に。
- `_update_current_strategy_run()` は `update_rolling_scores` と対称に**必ず同時変更**（§13 の n 非対称バイアス事故の再発防止）。
- 2604行: LAST_RAW_SCORE/LAST_TURNS の引き渡し。quarantine 中は accumulate もスキップ。
- NONINFERIOR での早期解放パス `IMPROVE_FUTILITY_RELEASE_ENABLED`（Phase 4 まで既定0）。

### C-3. `eloop.sh`

676行付近: `export LAST_RAW_SCORE` に加え `export LAST_TURNS` を追加（判別器 #4 用）。

### C-4. `core/config.sh`

348行 `MIN_GAMES_FOR_BEST_ROLLBACK=12`、367行 `REGRESSION_MIN_BREACH_COUNT=2` 等の**env 上書き不可の直代入を `${VAR:-…}` 化**（メインセッションで直代入を実測確認済み）。以後の調整を `.env` だけで完結させ、config.sh デフォルト変更→worker 完全再起動の事故パターン（AGENTS.md 既知）を回避。

---

## D. 新設 config（既定値。全て Phase 制御で off/0 スタート）

```bash
STAT_GATE_MODE="${STAT_GATE_MODE:-off}"            # off | shadow | enforce
STAT_STATISTIC="${STAT_STATISTIC:-wmean}"
STAT_WINSOR_LO_Q=0.05  STAT_WINSOR_HI_Q=0.95
STAT_LOOKS="24,48,72,100"
STAT_HARD_MIN_N=16  STAT_HARD_LOOK_STRIDE=8  STAT_HARD_LOOK_K=11
STAT_ALPHA_SOFT=0.05  STAT_ALPHA_HARD=0.01  STAT_ALPHA_PROMOTE=0.01  STAT_ALPHA_NONINF=0.05
STAT_DELTA_SOFT=500  STAT_DELTA_HARD=2000  STAT_DELTA_PROMOTE=500  STAT_DELTA_HARMLESS=1500
STAT_ANCHOR_MIN_N=100  STAT_ANCHOR_SCORE_KEEP=200  STAT_ANCHOR_MIN_N_FALLBACK=1
STAT_ANCHOR_MAX_AGE_GAMES=2000  STAT_PROMOTE_HOLDOUT_N=48
STAT_TIEBREAK_ORDER="soviet,frontier,best_max_type,keep_current"

INSTADEATH_SPLIT_ENABLED=0        # Phase 1 で 1
DEAD_EVAL_THRESHOLD=3000  DEAD_RAW_THRESHOLD=300
DEAD_MONITOR_FILE="tmp/state/instadeath_monitor.json"  DEAD_MONITOR_WINDOW=400
DEAD_ALERT_WINDOW=50  DEAD_ALERT_RATE=0.10
DEAD_QUARANTINE_ENABLED=1  DEAD_QUARANTINE_WINDOW=20  DEAD_QUARANTINE_RATE=0.30
DEAD_QUARANTINE_CLEAR_WINDOW=20  DEAD_QUARANTINE_CLEAR_RATE=0.05
DEAD_BURST_RATIO=3.0  DEAD_HARD_RATIO=0.5  DEAD_MAX_TURNS=3  DEAD_ALPHA=0.01
DEAD_REGRESSION_ENABLED=0         # Phase 3 で 1

IMPROVE_FUTILITY_RELEASE_ENABLED=0  IMPROVE_FUTILITY_MIN_GAMES=32   # Phase 4
```

---

## E. 移行・ロールアウト（各 Phase: git commit/push + VM デプロイ同時、最低3日≒1,200試合観測）

| Phase | 内容 | 期間 | 検証・進行条件 |
|---|---|---|---|
| 0 | 基盤（挙動変更ゼロ）: eval_stats.py 新設+重複集約、config `${VAR:-}` 化+新設（全て off）、improve.sh:2277 リテラル解消、LAST_TURNS export | 1日 | pytest PASS。24h で rolling の comp/p50/p25 が bit 一致。**config.sh を触るので worker 完全再起動必須** |
| 1 | 即死分離+監視（観測のみ）: INSTADEATH_SPLIT=1、progress 追記開始、monitor 生成 | 3日 | 直近は即死0なので dead_rate=0 が期待値。alive 適用前後で comp 不変（恒等） |
| 2 | 統計ゲート shadow: STAT_GATE_MODE=shadow、[STATGATE] ログ | 7日 | 不一致の8割以上が legacy=REGRESSION & stat=INCONCLUSIVE であること。**逆向き不一致（legacy=OK & stat=REGRESSION）が0件**であること（出たら δ/α 見直し） |
| 2.5 | anchor 是正: MIN_GAMES_FOR_BEST_ROLLBACK 12→100、alive_scores 保存 | 3日 | anchor の n が常に100以上に。**0-3 の +1,585→+511 圧縮はこれ単独で効く可能性が高い（Phase 3 より効果大の見込み）** |
| 3 | enforce（粛清側のみ。昇格側は shadow のまま）+ DEAD_REGRESSION=1 | 7日 | 週ロールバック 1/5 以下。eval 中央値が悪化しない。**ロールバックは `.env` に STAT_GATE_MODE=shadow で1コマンド即復帰（再起動不要）** |
| 4 | 昇格側 enforce + 探索解放: FUTILITY_RELEASE=1、MIN_GAMES_BEFORE_IMPROVE 100→48 | 7日 | 改善サイクル/日 4→8-12。即死率が上がったら即戻す |

**負の対照（必ず実施）**: Phase 2 中に `decide()` を過去の rolling スナップショット（strategy_versions_archive/ + git 履歴）にオフライン適用し、「enforce だったら何回粛清が抑止されたか」を事前算出。実運用の観測がこの予測から大きく外れたら実装バグを疑う。

---

## F. リスクと成功基準

**主要リスク**: ①真の劣化の見逃し（Type-II。δ=1500 は50%検出）→ HARD 層が破壊的変化を1.5h以内に85%捕捉+目的ゲートは即時発火+trend monitor 独立維持 ②anchor 固着 → drift re-anchor ③悪い戦略の居座り → eval 7日移動中央値が shadow 比 −500 超で δ_soft を 500→0 へ ④anchor 候補枯渇 → フォールバック+WARN ⑤winsor 限界の腕間ずれ → 両腕プールから1回だけ算出をテストで固定 ⑥quarantine 誤発動 → quarantined_scores 退避でデータ不喪失+開始/解除を通知 ⑦config stale env → Phase 0/2.5 以外は .env のみで完結。

**「効いた」の判定（Phase 3 完了時）**:

| # | 指標 | 成功基準 |
|---|---|---|
| 1 | shadow 期間の legacy=REGRESSION & stat=INCONCLUSIVE 率 | 全 REGRESSION の 60% 以上 |
| 2 | 戦略 churn | 20/週 → 6/週以下 |
| 3 | anchor 選択バイアス（昇格時 comp − 実現 comp） | +1,500 → +600 以下 |
| 4 | eval 即死除外 7日移動中央値 | shadow 比 −300 以内 |
| 5 | **τ² の月次再推定が正になる** | 選択圧が実力に効き始めた唯一の直接証拠 |

shadow 集計ワンライナー:

```
grep '\[STATGATE\]' <log> | sed 's/.*legacy=\([A-Z]*\).*stat=\([A-Z_]*\).*/\1 \2/' | sort | uniq -c | sort -rn
```

---

## G. 触らないもの

strategy.py 本体 / 目的進捗ゲート / comp の定義と RANK_WEIGHT_* / branch 予算 / .env の無効化済みトグル4件（B-2 完了後に再有効化の前提が満たされるので別途判断）。

## 検証メモ（メインセッション、2026-08-20）

- **実在確認済み**: improve.sh:2277 `seed_scores = scores[-20:]`、config.sh:348/367 の直代入、eloop.sh:654 EVAL_SCORE / :676 LAST_RAW_SCORE export、regression.sh:948/1494 の両関数、quantile/metrics の多重実装、VM の scipy 不在・Python 3.12.3、last_rollback_analysis.md の内容（§0-3 引用と一致）、8/11 以降の即死ほぼゼロ（日次中央値の全件=生存のみ一致から裏付け）。
- **未再実行（委任先のシミュレーション結果）**: τ²推定、誤ロールバック率 17.8-23.6%、winner's curse の数値、閾値スイープ FP/検出力、非劣性到達 n。手法は本文に記載どおり再現可能。SD 2,200-3,400 等の基礎統計はメインセッションの独立実測と一致。
