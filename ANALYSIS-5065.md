# ANALYSIS-5065 — 共産主義法律シミュレーター「何をしているかわからない / エンターテイメント性がない」の原因分析

対象: docich#5065（owner 指摘: 「何をしているか全然わからない / エンターテイメント性がない / 改善して」）
対象コーナー: `communist-law-simulator`（adapter=`lawsim`, game=`lawsim-view`、#697 系）

## 構成の確認

| 層 | ファイル | 役割 |
|---|---|---|
| エンジン（正典・決定論） | `src/docich/communist_law_simulator.py` | `LAWS`（14法案・summary/risks/category 保持）、`apply_turn`/`simulate`、`progress_frames`（start→法案→end） |
| セッションバンク | `src/docich/lawsim_sessions.py` | generation ごとに回転する著作済みセッション 3 本（達成/崩壊/未達） |
| view（配信画面） | `src/docich/lawsim_view.py` | loopback HTML。payload にはセッションのみ保持し、表示時にエンジンで完全再導出（fail-closed）。1 frame 12 秒でループ |
| program adapter | `src/docich/adapters/lawsim_program.py` | preflight でセッション構築・検証・payload 書き出し、readiness で identity 検証 |
| ライフサイクル | `src/docich/lawsim_corner.py` / `corner_adapters.py` / `corner_catalog.py` | GameSwitch 経由の bounded 会期（既定3分・上限14分） |
| narration | `src/docich/lawsim_narration.py` | frame ごとの overlay/audio 実況（audio_enabled opt-in。本番 config は true） |

画面構造（`lawsim_view.py` の HTML）: header（title/republic/finalStatus）→ status-line（開会/第N回会議/閉会 + `finalStatusText`）→ lawline（frame heading）→ board（deltas テキスト列・6指標バー・警告イベント・statusline2）→ page-dots・footer（caveat + n/m カウンタ）。背面に装飾 canvas。

## 「何をしているかわからない」の原因の具体化

1. **法案の中身が表示されない（最重要）**
   `LAWS` には各法案の `summary`（何をする法律か）、`category`（経済/社会/保安/外交/文化）、`risks`（代償）が定義済みだが、view の payload・HTML のどこにも出力されない。視聴者に見えるのは法案名（「穀物調達の強化」）と数値の羅列だけで、「何をする法律か・何が代償か」が読み取れない。

2. **目標・勝敗条件が画面のどこにも無い**
   ルールは「計画ゲージが100で五カ年計画達成。支持・党規律・外貨が0、または闇市場が100で崩壊」だが、この条件は start frame のテキストにも常駐表示にも現れない。「計画」バーがあっても、なぜ上げているのか・何に近づいているのかが伝わらない。

3. **結果を先出しして混乱させている（スポイラーの常時表示）**
   - `load()` が `#republic` に `republic ／ finalStatus`（例: `plan_achieved`）をロード直後から表示。
   - `render()` が `#status-text` に `data.finalStatusText`（例: 「五カ年計画を達成した」）を**全フレームで**表示する。
   つまり開会中・審議中でも「達成した」と書かれており、視聴者は「もう終わったのか？」と読み違える。進行中の悬念も消える。

4. **変化の因果がバーに結びつかない**
   turn frame の差分は `#deltas` のテキスト1行（「法案の効果: 生産+6 支持-10 闇市場+12 ／ 計画の前倒し +4」）のみ。バーには現在値しかなく、どの指標がこの法案で動いたか視覚的に追えない。また `planGain`（計画前倒し）が生産由来の間接効果であることも説明がない。

5. **警告イベントの閾値・因果が見えない**
   `⚠ 闇市場が計画物資の半分を流し始めた` は出るが、「闇市場が50を超えたから」という発火条件（= 直前の法案の代償との因果）は表示されない。

6. **進行の見通しが弱い**
   位置表示は小さな page-dots と `5/13` カウンタのみ。「全11法案のうち今3本目」「残りあと何本」「次の議題は何か」が明示されない。

7. **narration が英語キー混じり**
   `lawsim_narration._session_text` の delta は `production+6` のような英語 metric キーのままで、さらに `metricsAfter` の全指標を `production46 support30...` と英語羅列する。TTS・overlay ともに日本語として読めない。

## 「エンターテイメント性がない」の原因の具体化

1. **全フレーム同一レイアウト・12秒均等回転・無限ループで単調**
   start / turn / end が同じ見た目で進み、山場がない。1周最大168秒をそのまま繰り返す。

2. **採決という「山」の演出が無い**
   法案が可決される瞬間に数値だけが変わる。賛成率・議場の反応といった一瞬のドラマが無く、毎回が平坦。

3. **結果のフィードバックが弱い**
   end frame も同じ装飾。達成（plan_achieved）と崩壊（collapse_*）が視覚的に区別されず、「勝った/負けた」のカタルシスがない。

4. **期待を作る要素がない**
   次の議題が見えないため「次はどうなるか」という予感・継続視聴の動機が生まれない。

5. **物語性の薄さ**
   共和国名と会期タイトルはあるが、各ターンに世情・議場の反応が無く、3本の固定セッションが同じ温度で回転するだけに見える。

## 改善方針（IMPLEMENT-5065 で実施）

エンジン（`communist_law_simulator.py`、正典・契約テストで固定済み）は**変更しない**。すべて表示層（`lawsim_view.py` の payload 構築と HTML）と narration 層（`lawsim_narration.py`）で、既存の検証済みデータから**決定論的に派生**させる。fail-closed 契約・`validate_status` 同一性検査・`progress_frames` の出力は不変のままにできる。

- 可読性: 法案カード（カテゴリ・要約・リスク）、目標ルールの明示、結果の先出し廃止、バーへの差分バッジ・危険帯強調、残り法案数と次の議題、警告への閾値表示。
- 因果: 法案効果→指標バッジ→警告（閾値跨ぎの理由付き）→世情変化の流れを1フレームで追える構成。
- 娯楽性: 決定論の場内採決率・議場リアクション、start のルール提示、end の勝敗ビジュアル（達成=金/崩壊=赤/未達=琥珀）、narration の日本語実況化。
