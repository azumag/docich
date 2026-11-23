# FIX-5065-REVIEW — PR #5073 レビュー指摘対応（azumag COMMENTED, 2026-11-22）

対象: docich#5065 / PR #5073（branch `feat/lawsim-5065-view-revamp`, review 時点 HEAD `6bf981f4`）。
変更範囲はレビュー制約どおり `src/docich/lawsim_view.py`・`IMPLEMENT-5065.md`・`docs/features/communist-law-simulator.md` と本ファイルに限定（エンジン `communist_law_simulator.py`・narration・テストは無変更）。commit / push / PR 更新は実施していない。

## 指摘1: テスト実行記述の矛盾解消

### 事実

「誰が・どこで・何を実行し、結果が何だったか」は以下の3系統であり、いずれも矛盾しない。

| 主体 | 環境 | 実行内容 | 結果 |
|---|---|---|---|
| Devin セッション | この作業環境 | pytest / python3 の実行を試みたが環境が拒否（git コマンドのみ許可） | **未実行**。コード読みによる整合検証のみ |
| 検品ゲート | Muse の VM 環境 | `tests/test_lawsim_corner.py`・`tests/test_communist_law_simulator_contract.py`・`tests/test_broadcast_view_standard.py` | **68 passed**（PR 本文の数値の根拠） |
| PR CI | GitHub Actions | run `44996156766`（HEAD `6bf981f4`）: `test-scope` / `python-syntax` / `full-suite (1/3)(2/3)(3/3)` / `ci-required` | 全 green。full-suite は hermetic 契約（issue #79）により `tests/test_*.py` 全件を含み、上記3ファイルもカバー |

### 対応

`IMPLEMENT-5065.md` の「## テスト」節を書き換え、「本環境では未実行」を Devin セッション内の話として限定し、検品ゲート（Muse VM）の 68 passed と CI run `44996156766` の全 green を別行で明記した。実行コマンドの行も「検品ゲートで実施済み」と修正した。PR 本文側はレビュー指摘どおり Muse が別途対応するため、この対応では触れない。

## 指摘2: 960×540 レイアウトの収まり検証（コード上の計算）

### 外側ジオメトリ

- `#stage`: 960×540、`box-sizing:border-box`、`padding:12px 22px 14px` → コンテンツ領域 **916 × 514**。
- `canvas#scene`・`#footer`・`#unavailable` は `position:absolute` でフロー高を消費しない。
- board より上の帯: `header` 固定 52 ＋ `#status-line` 固定 22 ＋ `#lawline`（margin-top 2 ＋ 14px×1.3 = 18.2）≈ **94.2px**。
- `#board{height:calc(100% - 122px)}`: パーセントは `#stage` のコンテンツ高 514 に対して解決 → board 外寸 **392px**。board 下は `#page-dots`（7px）＋余白で 514 − 94.2 − 392 ≈ 28px の余裕。
- **board 実効コンテンツ高 = 392 − border 2 − padding(8+6) = 376px**、実効コンテンツ幅 = 916 − padding 28 − border 2 = **886px**（13.5px CJK 換算 ≈65 文字/行）。

### turn frame 最悪ケース（エンジン契約上の上界）

入力上界（エンジン・セッションバンクから確定）:

- summary 最長 26 文字＋「法律内容: 」≈33 文字 ≈413px → law-main 幅 746px（886 − vote 130 − gap 10）で常に 1 行。
- risks は最大 2 件、最長結合 ≈30 文字 ≈330px → 常に 1 行。
- `#deltas` は最長でも ≈50 文字 ≈675px → 実質 1 行。安全側で **2 行上界**で計算。
- 警告イベントは turn あたり **最大 3 件**（警告対象5指標のうち 1 法案が触れるのは最大3指標 = `grain-procurement-tightening` / `five-year-plan-steel` / `foreign-trade-opening` 等）。各行 ≈38 文字 ≈475px → 1 行/件。著作済みバンクの実測最悪は 1 件/turn。
- agenda・statusline2 は各 1 行。`#verdict`・`#rules` は turn frame では hidden。

| 要素 | CSS 根拠 | 高さ(px) |
|---|---|---|
| `#lawinfo` | max(law-main 53.5, .vote 57.6) | 57.6 |
| ↳ law-main | summary 18.125+2 ＋ risks 15.4 ＋ reaction (mt3+14.95) | 53.5 |
| ↳ .vote | border 2 ＋ padding 10 ＋ num 22×1.1=24.2 ＋ label 9.5×1.3=12.35 ＋ track 4+5 | 57.6 |
| gap lawinfo→deltas | margin collapse max(6,2) | 6 |
| `#deltas` | 13.5×1.5=20.25/行 × 2行上界 | 40.5 |
| gap deltas→bars | max(4,4) | 4 |
| `#bars`（6行） | 行高 max(track 15, value 13.5×1.2=16.2)=16.2 ×6 ＋ 内部 gap 5×4 | 117.2 |
| gap bars→events | max(4,4) | 4 |
| `#events`（警告3行） | 12.5×1.45=18.125 ×3 ＋ warn margin 1×2 | 56.4 |
| gap events→agenda | max(1,3) | 3 |
| `#agenda` | 11.5×1.3=14.95 | 14.95 |
| gap agenda→statusline2 | 3 | 3 |
| `#statusline2` | 12×1.3=15.6 | 15.6 |
| `#board` padding | 8 上 ＋ 6 下 | 14 |
| **合計** | | **≈336** |

**336px ≤ 376px（余裕 ≈40px）** → turn frame は最悪ケースでも board 内に収まり、欠落しない。

参考境界: エンジン上到達不能な 5 警告を仮定しても +2×18.125+2 ≈ **374px** で依然収まる。著作済みバンクの到達最悪（警告1件・deltas 1行）は **≈278px**。

### start / end frame

- start: padding 14 ＋ `#rules` ≈89 ＋ gap6 ＋ `#deltas`（start text は republic ≤80 文字を含み最悪 3 行で 60.75）＋ gap4 ＋ bars 117.2 ＋ 空 agenda/statusline2 の残り margin ≈ **295px** ≤ 376 ✓
- end: padding 14 ＋ `#verdict` ≈72 ＋ gap6 ＋ deltas 空 ＋ bars 117.2 ＋ agenda ≈18 ＋ 空行 margin ≈ **240px** ≤ 376 ✓

### 変更前の評価（なぜ CSS を調整したか）

変更前は `.vote-label`/`#reaction`/`.bar-*`/`#agenda`/`#statusline2`/`#lawline`/`.rules-title`/`.verdict-sub` が `line-height` 未指定（`normal`）で、CJK フォントでは ≈1.4–1.5 のフォント依存値になる。同じ計算を `normal≈1.45` で行うと turn 最悪 ≈**377px** と board 実効 376px をわずかに超え、`normal≈1.5` のフォールバックフォントでは ≈**383px** となり `#statusline2` や `#agenda` が `overflow:hidden` で欠けうる状態だった（余裕がフォントメトリクス依存だった問題）。そこで:

- 上記要素すべてに `line-height` を明示指定し、フォントメトリクス依存の不確定性を除去（描画値もほぼ不変）。
- 収まり余裕を稼ぐため `.bar-row` margin 5→4、`#deltas` margin-bottom 6→4、`#events` line-height 1.55→1.45、`.warn` margin 2→1、`#agenda`/`#statusline2` margin-top 4→3 に縮小。
- `#board` の `calc(100% - 122px)`・`overflow:hidden`・`#lawinfo` の `min-height:0` は据え置き（上記調整で収まるため変更不要）。

### 合成採決率の明示の維持

- header の `<span class="tag">合成シミュレーション・非本番</span>` は全 frame で常時表示（変更なし）。
- 採決ボックスのラベルを `場内採決 賛成` → `場内採決 賛成（合成）` に変更し、数値の表示箇所でも合成であることを明示（9.5px で 10 文字 ≈95px ≤ vote 内幅 112px、1 行に収まる）。
- `votesFor` は `_votes_for()` でエンジン検証済み turn データから 25–99 にクランプされた決定論的派生値である点は不変（実投票ではない合成の演出、IMPLEMENT-5065.md 記載どおり）。

### 契約テストとの整合（コード読み）

- `test_view_html_shows_rules_goal_law_card_and_next_agenda`: `"場内採決"`・`"法律内容:"`・`"次の議題"`・`"この会期のルール"`・`"data.rules.goal"` はすべて維持（`場内採決 賛成（合成）` は `"場内採決"` を含む）。
- `test_view_html_hides_final_status_until_the_end_frame`・cover-fit・canvas・notranslate・CSP の各ピン留め文字列は無変更。
- CSS 数値（line-height・margin・calc(100% - 122px)）を文字列でアサートするテストは存在しないことを確認（`tests/test_lawsim_corner.py`・`tests/test_broadcast_view_standard.py` 内を走査）。

## 検証の制約

- 本環境では pytest / python3 の実行が拒否されるため、上記はすべてコード読みと CSS 数値からの算出。実表示の目視確認（フォント実測・欠けの有無）は owner 側の受入レーンに残す。
- `FIX-5065-REVIEW.md` の数値は CSS 宣言値からの決定的計算であり、ブラウザのフォントメトリクス実測ではない点に留意（line-height の明示指定で残る不確定性はテキスト行の折り返しのみで、上記の各最長入力は 1 行幅に十分収まることを確認済み）。
