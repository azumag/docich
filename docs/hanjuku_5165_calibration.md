# 半熟英雄 #5165 切り札告知署名・卵落下直接署名 校正成果物アーカイブ

- Issue: azumag/docich#5165（切り札使用告知署名＋卵落下の直接画面署名の実フレーム校正）
- アーカイブ issue: azumag/docich#5511（candidate／egg_drop 収集分の永続保存先退避）
- アーカイブ作成: 2026-11-25（VM Devin CLI レーン、kanban t_b3cc168c）
- 関連 GitHub コメント:
  - opencode read-only 校正分析（署名候補 A〜E + 判定マトリクス）: https://github.com/azumag/docich/issues/5165#issuecomment-7087712751
  - DSH レビュー記録（フレーム保持窓 約26分、即時回収計画）: https://github.com/azumag/docich/issues/5165#issuecomment-7088095475

本ドキュメントは #5165 の校正成果物を VM 置き換えで失われないようリポジトリ内へ退避したものです。#5165 本体は external wait（次回 `battle_card_candidate` 発生時の即時フレーム回収）のまま、本ドキュメントは退避のみを対象とします。

---

## 1. opencode read-only 校正分析（署名候補 A〜E + 判定マトリクス）

opencode による read-only 校正分析の草案（Bot が代理投稿、secret_scan: clean）。実装は行っていません。

### 結論

コード側の構造的ギャップは再確認できました。告知署名・卵落下直接署名・青バー画素のいずれも現行 parser に校正済みの値がなく、v129 の fail-closed が働いています。ただし本環境には #5098 のフレーム（`g1314-e2e9ce62` / 11:58:00–35Z）が存在せず、ピクセル値の確定と実フレームとの突き合わせは未完了です。以下に署名候補と突き合わせ手順を記録し、フレーム入手後の確定に備えます。

### 1.1 コード確認（ローカルの現行コードで照合）

- `flows/battle_menus.py` の `card_list_step`: `battle_card_candidate` は `expected_metric='実画面とparserで校正済みの切り札使用証拠'` / `observed_metric={'confirmation':'unclassified','screen_kind': screen.kind}` を記録するだけで、`card_consumption_complete=False` のままです（docich-581-market-bridge の最新版では 1937–1944 行目付近）。
- 消費確定の代替経路 `battle_card_effect_confirmed` は既に実装済みで、`enemy_hp_drop` / `card_row_absent`（異なる2フレーム）/ `enemy_soldiers_drop` の3根拠があります（同 1875–1914 行目付近、#4977/#4981 の成果）。
- 一方 `judgment/battle.py` の卵落監視（`_watch_egg_drop` → `_observe_egg_drop_candidate`）は、選択後の敵 HP 低下を `battle_egg_drop_unconfirmed` として記録するだけで、`enemy_egg_dropped`（直接 receipt）は立てません。v129 の過剰確定防止です。
- `IMPLEMENT-4977.md` の「残課題」は、改善案3（実フレームでの OCR 校正）を「実機画像が必要で本変更の範囲外」と明記し、校正済み署名が得られた場合の `battle_card_candidate` 解消を別 issue に委譲しています。
- 青バーは `CLASH_GAUGE_BLUE` が空のまま出荷され、parser は `'blue'` を一切報告しません。`hanjuku_clash_gauge.py` に `--blue-calibrate <png>:<enemy|ally>` の校正ツールがあり、既知の赤/緑/暗赤表で説明できない最頻色を候補として出力する設計です（保存実機フレーム480枚に接触窓が無かった旨も `docs/hanjuku_egg_safe_melee.md` に記載）。

補足（仮説・要確認）: #5098 の監査では選択 11:58:03Z → 未確定 11:58:04Z → HP低下 11:58:13Z です。`CARD_ANNOUNCE_LIMIT=6` 観測（月次 ≈1.26 秒/観測）なので、HP低下観測時には announce 段階を既に抜けて代替証拠経路の判定対象外になっていた可能性があります。フレームと decisions の step で確認したい点です。

### 1.2 校正すべき署名候補（実フレームで確定する項目）

#### (i) 切り札使用告知署名

- #5098 監査の `battle_card_selected` / `battle_card_candidate` は `screen_kind: 'text'`（11:58:03Z / 11:58:04Z）。使用告知は戦闘パネルではなくテキスト画面として出ている可能性が高いです。
- 候補A: カード名（本件は `イッテツーン`）を含む告知テキスト行の定型パターン（表示座標帯 + 固定文言 + 枠）。`os_layer/screen.py` の行スパン/OCR 出力から `span(y_range, x_range)` と既知トークンを抽出し、既存の `text` 判定と衝突しない `egg_ritual_recitation_panel()` と同型の dedicated parser として追加する想定。
- 候補B: 一覧から選択札の行が消える `card_row_absent`（既存の代替証拠を正面化）。1フレームではなく異なる2フレーム一致を必須とし、グレー行は誤確定しない（IMPLEMENT-4977 の残課題どおり、グレー読み取りは別途 OCR）。
- 候補C: 告知後の `screen.kind` 遷移（text → battle / world_map）と `frame_sha256` の連続性。同一 sha の待ち時間除外は既存実装を流用。

#### (ii) 卵落下の直接画面署名

- 候補D: `screen.egg_rows` の敵側行の消失（卵あり → 卵なし）を `egg_battle_menu` フレームで直接観測し、`enemy_egg_dropped` を立てる receipt。HP低下は前提条件としてのみ使い、単独では確定しない（v129 維持）。
- 候補E: 青バー画素パレット。既存の `--blue-calibrate` で敵側/味方側の接触窓フレームから候補色を抽出し、`CLASH_GAUGE_BLUE` へ転記して初めて `state='blue'` を流す。青＝接触窓か卵落下で消えるかの意味関係は、校正完了まで判定しない（現行設計どおり）。

### 1.3 実フレームとの突き合わせ手順（フレーム入手後）

1. `g1314-e2e9ce62/hanjuku_frames/` を入手（暗号化 evidence export の受領、または owner-only の read-only diagnostics 経由）。
2. 監査時刻 → フレーム対応: 11:58:03Z `battle_card_selected` / 11:58:04Z `battle_card_candidate` / 11:58:13Z `battle_egg_drop_unconfirmed` / 11:58:33Z `battle_card_unclassified` + `battle_result`。この帯の前後数フレームを対象に、候補A〜Eの出現/非出現を 1 フレーム単位で記録する。
3. `python3 -m docich.hanjuku_clash_gauge <runtime>/hanjuku_frames --json` で全フレームのゲージ観測を再現し、11:58:03–13Z の敵側 `tones` / `filled` / `state` を確認。青窓らしきフレームがあれば `--blue-calibrate` で候補色を出す（既知表で説明できる色は候補から除外される）。
4. 判定マトリクス:
   - 告知署名あり + 敵卵行の消失あり → 誤発火。#4997/#4977 前例どおり発生記録 close（補助表記「消費確定不能・署名未校正」を外す）。
   - 告知署名なし + 選択入力が実効していない → 実ギャップ。代替証拠拡張／署名校正の実装課題として起票（#5157 の先回り使用トリガ設計とも整合）。
   - 署名自体がフレームに残っていない（text 画面のみ等）→ 結論保留。収集対象フレームの拡張を owner と調整。

### 1.4 受入条件との対応

- (a) 誤発火 close: 候補A/Bが校正されれば `battle_card_candidate` を確定経路へ昇格でき、監査が消費を確定可能。（現状は不可）
- (b) 実ギャップ検出: 告知 none + 選択 none を直接署名で区別可能になる。
- (c) 卵消失を根拠とする安全な押し込み: 候補D（+E）が揃って初めて接続可能。v129 の「選択予定 + HP低下だけでは確定しない」は維持する。

### 1.5 補助案

DSH 提案の監査表記「残資源（消費確定不能・署名未校正）」に同意。bot 挙動を変えず、未校正札を通常の残資源と区別することで、誤発火分の close 判定を誤らないための最低限のガードになります。

### 1.6 残件（所有者・運用への依頼）

- opencode 環境では `gh` が 401 で直接投稿できなかったため、Bot 経由で代理投稿済み（2026-11-22T23:37:33Z）。
- 校正作業は read-only のまま、フレーム入手後に署名値を確定する第二段が必要です。

---

## 2. DSH レビュー記録の結論（issuecomment-7088095475）

Muse の配信 VM 実測（2026-11-23）を受けて、本 issue の校正計画について次を記録します。

### 実測事実

- `g1314-e2e9ce62` の `hanjuku_frames/` は直近約 120 枚（約 26 分）のローテートで、#5098 帯（11/22 11:58:00–35Z）の `battle_card_candidate` 収集分は残存していません。保持ログに残る candidate 4 件（25423・25435・28901・28919）の frame_sha256 も現バッファと不一致（ローテート済み）です。
- 第二段サンプル（decision-084 / decision-116）は、ファイル名の decision 番号（24684 / 24596）のフレームではなく、ローテートで上書きされた別 decision のフレームでした（`at` と mtime・sha256 が不一致）。よって以前の「本サンプルでは校正不能」は当該 decision に対する結論としては取り消しとします。視覚事実（写っていたフレームに告知バナー・卵アイコンが無い）自体は変わりません。

### 校正計画の修正

- 校正材料は「次回 `battle_card_candidate` 発生の即時回収（保持窓 約 26 分以内）」で確保します。card 系は candidate decision（±2 観測、announce 窓は `CARD_ANNOUNCE_LIMIT = 6` 内）、egg 系は落下完了側（青バーが写る観測 — 未校正時は fail-closed で青を報告しないため、`--blue-calibrate` の前提フレームになります）。
- 校正対象は (a) 告知バナーの画素署名、(b) 卵アイコン／青バー署名の 2 点に絞られます（「異なる 2 フレームで札行が消える」経路は既に消費受領として機能済み）。

### 過去事案（#5083 / #5087）への遡及適用は時間切れ

- 両件の candidate 収集分も保持窓を過ぎているため、「校正署名を収集済みフレームへ適用する」経路は成立しません。両件の確定手段は次のいずれかです:
  - (a) 当該ランの decision ログ（jsonl）に最終選択窓の敵 HP 観測が保持されていれば、ログ上の HP 推移で消費を確定する（フレーム不要）。保持の有無を要確認。
  - (b) 保持されていなければ、「証拠保持窓内に確認手段がなかった発生記録」として扱い、本件の校正完了＋監査表記修正（補助案: 「残資源（消費確定不能・署名未校正）」）の初回運用で今後を確定可能にした上で、両件を発生記録として close する判断を owner / DSH で行う。

### 運用要件のフラグ（実装判断時の材料）

- 保持窓 約 26 分は、監査起票→人間/エージェント確認のレイテンシ（数時間）に届きません。校正後も「起票時に重要フレームが消えている」問題が残るため、candidate・egg_drop 系の収集分だけでも永続保存先へ退避する（または監査イベントに sha256 と保存先を記録させる）措置を、本 issue の実装スコープに含めるか別起票にするか、実装判断時に検討してください。

（DSH レビュー: Muse 実測報告＋ opencode のコード確認（`CARD_ANNOUNCE_LIMIT=6`、代替証拠 3 経路、fail-closed 青バー）の照合。ローカル実行環境なし）

---

## 3. 回収済みフレーム 14 枚のアーカイブ状況

### 3.1 元の回収記録

Muse の VM で 2026-11-22 に本番 g1314 の decisions からローテート前に回収したと記録されています:

- 切り札系: decision 24684（`battle_card_candidate` / `battle_card`）snapshot `decision-084.png` ＋前後3枚 = 7 枚（当該フレームは札／卵のアイテムメニュー表示中）
- 卵系: decision 24596（`egg_battle`）snapshot `decision-116.png` ＋前後3枚 = 7 枚（戦闘画面、両軍ステータスに卵アイコン）
- 保存先（記録上）: Muse の VM の `~/workspace/docich-5165-frames/card/` と `egg/`

### 3.2 中心フレームの既知 sha256（decisions.jsonl より）

| 系 | decision id | snapshot | sha256 |
|---|---|---|---|
| card | 24684 | decision-084.png | `241e7a3a171a88a8f956e4bb6ddb0d6ac7b1938dc651b5fe4988fcf222032e56` |
| egg | 24596 | decision-116.png | `91d909420e3e15cdf1bb7795aa241fdf72958841c77fa69f34e0ffbfc826385f` |

前後 ±2 枚（context）の sha256 は decisions ログに記録されていません。

### 3.3 アーカイブ時の実測（2026-11-25）

退避実施時に `~/workspace/docich-5165-frames/` を確認したところ **ディレクトリは空**でした（作成日 2026-11-22、最終更新 2026-11-23 00:04、card/egg サブディレクトリも存在せず）。`frame_watch_5165_state.json` の `captured` も空です。

さらに VM（docich-vm `g1314-e2e9ce62`）の現行 `hanjuku_frames/`（240 枚のローテートリング）および VM 全体・ローカル VM の全 PNG を sha256 で照合しましたが、中心 2 枚の既知 sha に一致するファイルは **見つかりませんでした**（ローテートで上書き済み）。

したがって、本アーカイブにフレーム画像本体は含められません。テキスト成果物（署名候補 A〜E、判定マトリクス、DSH レビュー結論）のみを永続化しました。フレームは将来の再回収時に上記 sha256 で照合・検証できます。

再回収の参照手順（#5165 external wait の次回 candidate 発生時）:
- `battle_card_candidate` 発生から保持窓 約 26 分以内に `hanjuku_frames/` を取得し、sha256 を decisions ログの `frame_sha256` と照合してから保存する（frame_watch_5165.py v2 の方式）。
- 保存先は本リポジトリの `docs/evidence/hanjuku-5165-frames/`（新設）または `run/` 配下の ignored evidence ディレクトリ。永続化が必要な場合は本ドキュメントと同 PR で追加する。

---

## 4. 受入条件との対応

- 成果物の退避: テキスト成果物（署名候補 A〜E + 判定マトリクス + DSH レビュー結論）を本ドキュメントとしてリポジトリに永続化しました。フレーム 14 枚は VM 上で失われておりアーカイブ不能（上記 §3.3）。既知 sha256 を記録し、再回収時の照合に使用できます。
- 退避先の記録: 本ドキュメントのパス（`docs/hanjuku_5165_calibration.md`）および本ブランチを、issue #5511 と #5165 へコメントします。
- #5165 本体は external wait のまま変更していません。
