# docich#2454 PAPERコーナー: ニュースを解説し、約定・損益・実績を前回説明からの区間で振り返る

## 決定日
2026-11-22（DSH回答受領・記録 #issuecomment-7077954226）。
2026-11-22（DSH⑤⑥条件付き確定・条件1〜3、カーソル単一キー）。

## 確定済み設計判断（owner委任 → DSH受領）

1. **「多い」の閾値**
   - 全件読み上げ上限 **5件**。
   - 区間内約定 **6件以上** → 選抜解説に切替。
   - 0件 → 「約定0件」と明言して終了。
   - 1〜5件 → 時系列で全件説明。

2. **ピックアップ数・選定基準**
   - 上限 **3件**。
   - 優先順:
     ① 区間内で実現損益の絶対値が最大
     ② 金額・数量が最大
     ③ ポジション構築・全解消等の状態変化
     ④ 損切り・リスク拒否・逸脱
   - 各候補に根拠（数値比較）を必ず添える。
   - 集計・損益・実績は選抜に限定せず、区間全体ベース。

3. **初回（前回説明なし）**
   - 下限境界 = 台帳上の最初の約定（運用開始からの母集団）。
   - 解説は#2の選抜を適用し、「運用開始からの統計」と明示。

4. **区間の確定タイミング・境界方式**
   - 境界は**説明原稿生成時に確定**。
   - 境界は **単一キー**（filled_at + fill_id、最終約定1件）。fill_id は台帳の主キーなので、同時刻の時系列も自然に決まり、集合ではなく1キーで管理する。
   - 確定済みの最終約定のキーを「含めた最後の約定」として、台帳（paper.sqlite3）の `paper_corner_explanation_state`（corner_key, high_water_ts, high_water_fill_id, updated_at）に保存。
   - 区間 = 半開区間 [カーソル後, 当回生成時刻)。生成時刻の壁時計を上端とし、保存した約定キー基準で約定を取りこぼさない。
   - 受入条件は、カーソルと説明記録を**同一トランザクション**で更新し、**そのcommitを「説明完了」の定義**とすること。中断・再試行でも重複・取りこぼしを防ぐ。日跨ぎ・同日複数回は時刻キーで自然分離。
  - **実装未達**: 現在の `PaperCornerManager → generate_next_narration` は、永続的な説明記録を同じSQLite transactionで保存していない。生成前にcursorだけ進める処理は安全性のため撤去し、M2をDraft blockerとしている。

5. **新名称**
   - 表示：**「区間損益」「区間実績」** ＋期間併記（例: `区間損益 +1,234円（11/22 10:05〜19:00）`）。
   - 音声：**「前回の解説からの〜」**。
   - 「本日」を含む名称・文言は廃止。

## ⑤ P&L interval 算定（DSH確定・条件付き）

- `build_performance` に interval_start 追加、区間内実現損益を `interval_realized_pnl_jpy` として返す。`today_realized_pnl_jpy` は後方互換エイリアス（= interval 値）。
- **条件1**：エイリアスは interval 値のみ返す。日次リセット計算を別途残さない。
- **条件2**：`interval_start` 未指定時の day-start fallback は暫定互換としてのみ許容。コーナー経路（corner_script / status / dashboard）は必ず実カーソル値を渡し、fallback に到達させない。
- **条件3**：`today_*` 系キーは deprecated を明記し、参照移行後に削除する方針を残す。表示・音声から「本日」を廃止（ラベルは「区間損益＋期間」のまま）。
- 含み損益を区間統計に含める場合は**区間末時点のスナップショット値**と定義（区間累積ではない）。
- 理論ベンチマーク窓の interval 切替は可。窓の変更のみで、実現/含み・費用・件数の指標定義は既存仕様を維持。

## ⑥ ニュース本文取得（DSH確定・受入条件付き）

- 本文取得が wired 済みなら追加実装より prompt 動作指定を先に行う。
- prompt に最低3点を含める:
  1. 見出しのみの項目で短絡終了しない。本文・裏付けが取れた範囲で「何が起きたか／背景／相場・対象資産への関連」を説明して継続する。
  2. 取得済みの事実と分析・見通しを区別し、出典を示す。
  3. 本文未取得部分を読んだことにしない。見出しからの記事内容・因果の創作を禁止し、限界を明示して確認できた材料だけで説明する。
- 受入は「本文取得成功／見出しのみ／取得失敗」の3ケーステストで、説明内容（事実/分析の区別・限界明示）と終了判断（短絡していないか）を確認。

## 変更対象（index.html に訂正）

- src/docich/trading/performance.py
  - build_performance に interval_start 追加。`interval_realized_pnl_jpy` 新規 + `today_realized_pnl_jpy` エイリアス。
    - **deprecated 明記**（条件3）：`today_*` 系は deprecated として残し、参照移行後に削除する方針をコメントに残す。
  - build_theoretical_benchmark に interval_start 追加。履歴窓を [interval_start, now] 相当に。
- src/docich/trading/corner_interval.py（新規）
  - paper_corner_explanation_state テーブル（corner_key, high_water_ts, high_water_fill_id, updated_at）。
  - 単一キー（filled_at + fill_id）でカーソル読み書き。同一トランザクションで説明記録と原子的に保存。
- src/docich/trading/corner_script.py
  - build_prompt の「本日」文言を「区間」に、interval_realized を反映。
  - news の「見出しのみでも短絡終了しない」旨を追加（③：事実と分析・出典の区別＋限界明示）。
  - 生成前にcursorだけをcommitしていた暫定処理は撤去。説明記録がdurableになる前にcursorを進めない fail-closed 方針。
- src/docich/comment/paper_corner_profile.py
  - 52/64/72/318/328/343 行。318 行目はフォーマット実体 `f"本日損益(確定分) {_yen(f.today)}"` → interval キー（interval_realized_pnl_jpy）参照へ配線し直す（文言置換だけでは統計が日次残る）。
- src/docich/trading/dashboard_assets/index.html（dashboard.js ではなくこちら）
  - 26/27 行のラベル「本日損益 確定分」「本日実績 / 理論値」→「区間損益」「区間実績」＋期間併記。音声は「前回の解説からの〜」。
- src/docich/trading/status.py / soren_corner_stats.py / soren_corner_text.py
  - today_realized パスはエイリアス経由で維持（後方互換）。deprecated 明記済みエイリアス経路で参照継続。
- テスト
  - performance: interval_start ケース、0件/少数/多数の区間、day-start fallback に到達しないこと、新ラベル。
  - paper_corner/corner_script: 3ニュースケース（成功/見出しのみ/取得失敗）。
  - comment/paper_corner_profile テスト: 新ラベル＋期間併記のアサートへ更新。
  - trading_corner_script テスト: 新ラベル＋期間併記のアサートへ更新。

## 旧HEADでの検証（2026-11-22）

- python -m pytest tests/（対象: performance, paper_corner, corner_script, comment, dashboard）→ green。
- CI (ci-required, python-syntax, full-suite) green。これは旧HEADの結果であり、最新HEADの検証を代替しない。
- 完了報告は git diff ＋対象ファイルの `grep 本日` = 0 件の証跡（対象ファイル単位。スコープ外は残る）。

## 作業場所

/home/hatch/work/wt/docich/2454 (branch feat/2454-paper-corner-interval, base origin/main)

## 2026-11-23 実装状態（PR #5156）

- **実装済み**: interval ledger helpers、FIFO basis、same-second key ordering、pick selection、profile/dashboard/statusの一部配線、ニュースpromptの3状態。
- **未達・merge blocker**:
  1. `PaperCornerManager._generate_narration_text() → generate_next_narration()` で区間fills/picksと `interval_realized_pnl_jpy` を使う配線。
  2. 生成segment/説明recordのdurable保存とcursor更新を同一transactionにし、再起動時に生成済みrecordを再利用すること。
  3. dashboardに開始〜終了期間を表示し、JSがdeprecated `today_realized_pnl_jpy` ではなくinterval fieldを参照すること。
- **安全対策**: 生成前にcursorを進める2経路を撤去。dry-runおよびAI出力失敗で未説明fillsが飛ばない回帰テストを追加。
- CIで見つかった `test_performance_summary_is_scalar_allowlisted` の期待値不足は修正済み。最新の上記安全対策コミット後はCI再実行中。
- 受入条件4・5を満たすまではDraftを維持し、productionへmergeしない。

