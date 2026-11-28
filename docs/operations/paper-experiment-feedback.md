# PAPER 戦略実験の評価・改善ループ

2026-10-03 の調査と修正。対象は crypto PAPER の宣言型戦略実験で、実売買への昇格は含まない。本メモの修正は PR 段階であり、main・VM への反映や収益改善は未確認。

## 観測した失敗

終了済みの本番 diagnostics で、2 日とも改善ジョブは `spawned=true` まで進み、その後 `status=failed / phase=generate / progress=35 / detail=ai-error / reason_code=unknown` になっていた。時刻は状態ファイルの epoch 値を JST に変換したもの。

| 本番ログ | 生成開始 JST | 終了 JST | 経過 |
| --- | --- | --- | --- |
| [2026-10-02 の診断](https://github.com/azumag/docich/actions/runs/37010751935/job/110849624532) | 10-02 08:48:04.451 | 10-02 08:59:05.279 | 660.827 秒 |
| [2026-10-03 の診断](https://github.com/azumag/docich/actions/runs/37103874639/job/111148537014) | 10-03 09:11:24.523 | 10-03 09:22:25.007 | 660.484 秒 |

約 660 秒は既定の生成全体予算 `600 + 60` 秒と整合する。ただし、このログでは provider timeout・queue 待ち・gate 待ちの内訳を断定できない。ジョブ起動の成功や後日の lock 解放だけで、戦略生成・採用の成功とは判断しない。

コード上は、保存済み pending の採用判定が新しい AI 生成の成功後に置かれていた。そのため、交代条件を満たした候補が既にあっても生成失敗に巻き込まれた。また、評価はコーナー終了時に偏り、常駐 worker の約定で確定した損失を次の買い注文へ反映できなかった。関連課題は [#1216](https://github.com/azumag/docich/issues/1216)。

## 評価・停止・交代の動作

[`experiment_control.py`](../../src/docich/trading/experiment_control.py) を worker と改善ジョブが共有する。市場取得や AI 呼び出しの間は実験 lock を保持せず、評価・約定・候補採用のローカル処理を同じ lock で直列化する。

1. 改善ジョブは AI 生成より先に現行実験を評価し、交代可能な保存済み pending を採用する。まだ評価待ちの有効候補は `pending-exists` として保持し、上書きしない。
2. [`worker.py`](../../src/docich/trading/worker.py) は通常の取引周期の売買判断前、売り処理後の最初の買い注文前、約定処理後に評価する。売却で停止条件が成立した周期には、その後の買いを通さない。途中で実験が交代した場合も旧実験で計画した買いを破棄する。
3. 交代準備中は新規エントリーを止め、読み取り可能な旧実験の exit ルールで残存建玉を決済する。建玉ゼロ（flat）を確認して pending を採用し、次の評価期間を開始する。

決済数は評価対象の売り約定数 `closed_sells` であり、部分売却も含む。

| 条件 | 理由 | 動作 |
| --- | --- | --- |
| 20 決済以上 | `sample-complete` | 候補があれば新規停止して交代準備 |
| 開始後 48 時間以上 | `max-age` | 候補があれば新規停止して交代準備 |

20 決済と 48 時間は **いずれか** でよい。候補がなければ、損益によらず実験は観測を継続する。

損失だけを理由に新規を止める早期停止（旧 `early-stop`: 8 決済以上・実現損失・PF 0.75 未満）は 2026-11-12 に廃止した。PAPER の損益は仮想で守る資金が無く、候補は 1 日 1 回しか来ない（[`paper_improve_schedule.py`](../../src/docich/trading/paper_improve_schedule.py)）ため、止めても翌朝まで PAPER のデータが増えないだけだった（2026-11-11 に `snapback_harvest_v27` が 8 決済で止まり、約 19 時間売買が止まった）。8 決済では偶然の負けと区別できない。実損失を止める仕組みは LIVE 側の停止ルール（Issue #3623 §2.6）で扱う。control に既に `early-stop` で書かれた交代準備は読める理由として残し、次の候補まで旧 exit での決済を続ける。一度交代準備へ入った同じ実験は、停止理由を control に保存するため、一時的な評価失敗や残存建玉の損益改善では新規エントリーを再開しない。

既知の実験の active ファイル欠落・破損、control 破損、評価不能、原価不足は新規停止に倒す。旧ルールが読めれば exit は継続し、ルール自体が不明なら legacy exit に置き換えない。実験を一度も開始していない legacy 状態は区別する。現行と同じ ID でルールだけ変える候補は拒否し、同一候補の再提出でも評価開始時刻をリセットしない。AI 処理中に現行実験が変わった場合は `baseline-changed` で古い基準からの書き込みを止める。

## 銘柄別スロット（Issue #3623 §2.9）

銘柄ごとに別の実験を動かすための置き場。**専用スロットが 1 つも無い間は、上の動作から何も変わらない。** 専用スロットは、下の「銘柄ごとの AI 改善」が候補を保存したときに作られる。

- **共通スロット** = 今の `trading/`。専用スロットを持たない銘柄をすべて受け持つ。
- **専用スロット** = `trading/slots/<BASE>_<QUOTE>/`。`slot.json` が銘柄を宣言し、実験・保留・control・評価の各ファイルを同じ名前で持つ。台帳は共有（[`symbol_slots.py`](../../src/docich/trading/symbol_slots.py)）。
- **評価**：専用スロットはその銘柄の決済と保有だけ、共通スロットは専用を持たない銘柄だけを数える。
- **交代**：専用スロットは **10 決済、または 7 日**（銘柄ごとの AI 改善の対象条件と同じ日数）（共通は 20 決済 / 48 時間のまま）。交代準備はその銘柄だけで、ほかの銘柄は止めない。
- **売買**（[`slot_runtime.py`](../../src/docich/trading/slot_runtime.py)）：専用スロットの銘柄は、買いも売りもそのスロットの実験のルールで判断する。ほかの戦略（相対価値など）がその銘柄を買うかどうかも、そのスロットの新規可否に従う。
- **最初の実験を待つスロット**（保留だけある）：その銘柄の新規を止め、保有は共通スロットの売りルールで手じまいし、保有が 0 になってから保留を採用する。
- **壊れたとき**：スロットの実験が読めないときは、その銘柄だけ新規も決済も止める（他人のルールで売らない）。`slots/` に読めない項目があるときは、どの銘柄の持ち主か分からないので、全スロットの新規を止めて決済だけ続ける。

### 銘柄ごとの AI 改善

毎日 1 回の定期実行（`paper-improve-scheduled`）の中で、共通スロットの改善のあとに動く（[`paper_improve_slots.py`](../../src/docich/trading/paper_improve_slots.py)）。コーナー終了時の `paper-improve` では動かない。

- **対象**：銘柄数の上限は無い。スロットに保留の候補が無く、次のどれかに当たる銘柄をすべて対象にする。(1) 銘柄向けの候補をまだ一度も受け取っていない、(2) 前回の候補から **10 決済**、(3) 前回の候補から **7 日**。売買の少ない戦略も日数で対象になる。
- **AI に渡す事実**（銘柄ごと）：直近 14 日の決済数・1 日あたりの決済数・勝ち数・実現損益、直近の決済、現行の買い条件が直近の 5 分足で成立した本数（`market_cache.json` の履歴でルールを再生した頻度の見積もりで、損益のバックテストではない）、値動きの大きさ。決済が少ない銘柄では、買い条件が成立する回数を増やすことも目的として明示する。共通スロットの改善にも `trade_activity`（決済の頻度と買い条件の成立本数）を渡す。
- **呼び出し**：1 回の呼び出しは最大 4 銘柄。対象が多い日は同じ実行の中で呼び出しを分ける。開始から 30 分を過ぎたら新しい呼び出しを始めず、残りは翌日に回す（決済の多い銘柄から先に処理する）。AI の失敗が 2 回続いたら、その日は打ち切る。
- **検証と保存**：候補は銘柄ごとに検証し、1 つが不正でも、ほかは通す。実験 ID はシステムが付ける（`<銘柄>_<日付>`）。保有時間だけで決済する条件が無い候補には `hold_minutes >= 360` を足す（スロットは保有が 0 にならないと交代できないため）。通った候補はその銘柄の専用スロットに保留として入り、スロットが無ければここで作る。
- **1 日 1 回**：`paper_improve_slots_status.json` の開始時刻で数える（20 時間）。AI を呼ばなかった回は数えない。停止用のファイル `paper_improve_scheduled.disabled` は、この処理も止める。
- **共通スロットが空になったら**：売買している銘柄がすべて専用スロットを持つと、共通の実験はどの銘柄も受け持たないので、共通の候補の生成（AI 呼び出し）を飛ばす。
- 記録：`paper_improve_slots_status.json`（件数と銘柄ごとの結果）、`paper_improve_slots_state.json`（銘柄ごとの前回の候補の時刻）。

### 表示（画面・放送・診断）

`status.json` の `worker_summary` にある `experiment_status` / `experiment_reason_code` / `experiment_entries_allowed` は、**共通スロットだけ**の状態である。専用スロットの様子は次の場所で見る（読み取りだけで、売買の判断には使わない。[`slot_overview.py`](../../src/docich/trading/slot_overview.py)）。

- **`worker_summary`**：`slot_count`（専用スロットの数）、`slot_active_count`、`slot_entries_allowed_count`、`slot_blocked_count`、`slot_layout_invalid`、`common_owned_symbol_count`（共通スロットが受け持つ銘柄の数）。
- **放送の状態表示**：`Slots: <数> on=<買い可> blocked=<停止> common=<共通の受け持ち>`。共通スロットの受け持ちが 0 銘柄のときは、共通の `Experiment:` 行を出さない。
- **画面（ダッシュボード）**：「銘柄別戦略」の欄に、スロットの数と買い可の数、直近に約定した銘柄の戦略名と、交代までの進み具合（決済 n/10、経過 n/7 日）を出す。表示枠の都合で 1 行だけ。
- **放送の「戦略」の説明**：専用スロットがあると、その銘柄の戦略（名前と `thesis`）を AI に渡し、共通の既定パラメータ（`policy`）を銘柄の戦略として語らないよう指示する。AI を使えないときの定型文は、戦略名と決済件数だけを読む（`thesis` は改善 AI の作業メモで、bps や特徴量の名前が多いため）。
- **診断**（`collect_diagnostics.py` の `paper_slots`）：状態ごとの数、買い可の数、決済数の合計、止まっているスロットと交代が近いスロットを最大 3 件、銘柄ごとの AI 改善の件数。戦略の名前やルールは出さない。
- 決済の理由は、約定の `signal_context.rule_id`（例 `exit-3`）と、そのスロットの `exit_rules` を突き合わせると分かる。

### 昇格の判定器（シャドー、Issue #3623 §2.6・ルール v4）

[`promotion_shadow.py`](../../src/docich/trading/promotion_shadow.py) は、専用スロットの実験（`<銘柄>_<yymmdd>`）のセルごとに、「PAPER で成績が良いか」を判定して**報告するだけ**の読み取り専用の判定器である。台帳は `mode=ro` で読み、何も書かず、LIVE にもスロットにも触れない。LIVE はまだ存在しない。

- 方式はルール v3 の e-process と同じ。日本時間の日ごとの純リターン（手数料補正後）から、昇格の証拠 E を更新する。
- v3 との違いは、使う日ブロックを `cell_metrics.return_blocks_v3`（`paper-cost-v3` の売りと、v3 の買いだけで構成された在庫から作ったブロック）に限り、日付による足切りを廃止したこと。v3 以前は5分足終値で約定し、bid/ask のスプレッドを払っていなかったため、OAS のような広いスプレッドの銘柄では往復の値動きが利益に見えていた。
- しきい値は **5**（v2 は 430）。専用スロットの実験は7日で替わり、日ブロックが最大7個なので、430 では、効果が大きくても昇格しない（日ごとの標準偏差 0.5% のシミュレーション）。しきい値 5 なら、日ごとの効果 +1.9% は約100%、+1% は約60%、+0.5% は約1%、損益ゼロ近辺は約0% が7日以内に昇格する。v3 反映直後は v3 の約定が無いため、5日分の日ブロックが溜まるまで昇格は起きない。
- 理論の保証は、「実質トントン以下の戦略が昇格する確率はセルごとに 1/5 以下」まで。多数のセルを順に評価するので、偶然の昇格は少し混ざる。LIVE の資金が約1万円であることを前提に受け入れている（オーナー決定 2026-11-16）。シミュレーションは日ブロックの独立を仮定しており、実データでは未検証。
- 実行：`python3 -m docich.trading.promotion_shadow --state-dir <trading dir>`（trading dir そのもの）。出力は JSON 1行で、判定したセルの数、最小ブロック数を満たしたセル、昇格できるセル（最大10件）、E の最大値。

## 戦略判断と成績の補正

- **売却コストを含む exit 判断:** [`paper.py`](../../src/docich/trading/paper.py) の共通約定価格計算を使い、売却時の手数料・スリッページ控除後価格を購入原価と比較する。既定は片道 12 bps の手数料と 5 bps のスリッページ。購入原価には買い側コストが既に含まれるため、重複控除しない。新しい理由表示の単位は `net_pnl_bps`。
- **OR 条件の採点:** [`strategy_lab.py`](../../src/docich/trading/strategy_lab.py) は成立した条件の方向付き余裕をスコアへ使う。不成立条件の大きな乖離を絶対値で加点しない。
- **観測不足の修正:** 最大 lookback は 24 期間のまま、worker は 25 本の終値を取得する。24 期間のリターン・RSI 等に必要な先頭の終値を確保する変更で、lookback を 25 へ拡張するものではない。

[`strategy_metrics.py`](../../src/docich/trading/strategy_metrics.py) の評価は schema 2。SQLite の単一スナップショットから全履歴を平均原価方式で再構成し、他戦略の売買も在庫・原価の更新へ反映する。評価対象は現行実験の開始後に、その実験の exit 判断で売却した約定（`accounting_scope=experiment_exit_decisions`）。損益を今回の実験が買った分 `self_entry_realized_pnl_jpy` と、前期間・他戦略由来の分 `carry_in_realized_pnl_jpy` に比例配分し、両者の合計を `realized_pnl_jpy` とする。

混合原価の売却は self/carry 両方の内訳件数に含まれるので、内訳件数の単純合計は総決済数とは限らない。原価が復元できないときは `partial / ledger_incomplete_basis` とし、ゼロ損益の正常評価にしない。PF とドローダウンもこの実現損益の範囲であり、含み損益や戦略全体のリターンを表さない。carry の利益を今回の entry の成果とみなさない。今回確認したのは会計・制御の整合性であり、将来の収益改善は継続観測が必要。

## 定時改善 timer（1日1回、コーナー非依存）

コーナー終了時の改善起動（`paper_corner.py::_spawn_improve_once`）は変更しない。PAPERコーナーが長期間完了しないと改善が止まるため、別系統として user timer `docich-paper-improve.timer` を追加した（毎日 06:20 JST、`Persistent=true`、`AccuracySec=1min`。05:10 JST の `docich-nethack-daily-improve.timer` と重ねない）。`docich-paper-improve.service` は `ops/vm_actions/run_paper_improve_with_ai_env.sh` 経由で `docich trading paper-improve-scheduled` を1回実行する oneshot（`TimeoutStartSec=3600`）。production deploy 時に `ops/vm_actions/ensure_paper_improve_timer.sh` が配置・enable する。

`paper-improve-scheduled` は AI を呼ぶ前に、ローカル状態だけを読む次のgateを上から順に評価し、最初に該当した理由でスキップする（`reason` を JSON 出力。`trading/` 配下 = `<state_dir>/trading/`）。

| 順 | `reason` | 条件 |
| --- | --- | --- |
| 1 | `disabled` | 緊急停止ファイル `trading/paper_improve_scheduled.disabled` が存在する |
| 2 | `already-running` | `paper_improve_status.json` が `running` で `updated_at` が70分以内 |
| 3 | `pending-exists` | 検証済みの有効な pending 候補が既にある（採用は常駐 worker の `assess_experiment` が行う） |
| 4 | `daily-limit` | `paper_improve_status.json` の `started_at` が20時間以内（`skipped` と `dry-run` は試行に数えない） |
| 5 | `run` | 上記以外。コーナー終了時と同じ `trading paper-improve` の経路（検証エラー1回修復リトライ込み）を1回実行 |

`daily-limit` は状態ファイルだけで判定するため、コーナー起動の改善も数え、AIの呼び出しは全体で概ね1日1回に収まる。失敗した日は翌日まで待つ（別のbackoff状態は持たない）。コーナー起動との重複は既存の singleflight lock と improve lane が防ぐ。

**停止方法（unit変更・再deploy不要）:** `touch <state_dir>/trading/paper_improve_scheduled.disabled` で次回tickからAIを呼ばない。再開は同ファイルを削除する。完全に外す場合は `systemctl --user disable --now docich-paper-improve.timer`。手動確認は `docich --config config/docich.soren-live.toml trading paper-improve-scheduled --dry-run`（gateを通さずAIも呼ばない）。

注意: この timer が再開すると、評価待ちが解消した現行実験は建玉が flat になった後に交代する。ルール v2 セルの前向き評価（Issue #3623 §2.8）はその時点で終わる。

## 次回の観測手順

正規の [read-only diagnostics](runtime-diagnostics.md) で以下を確認する。診断と同時に lock 削除、再起動、state 修復を行わない。

| 観測先 | 確認すること |
| --- | --- |
| `corners.paper_improve` | `status / phase / reason_code / changed` と開始・終了時刻。生成失敗と採用成功を分けて読む |
| `corners.paper_experiment` | `present / readable / age_sec`、`status / reason_code`、`entries_allowed / pending_available / open_position_count`。改善ジョブの状態ファイルがなくても独立に観測可能 |
| `corners.paper_experiment.evaluation` | `matches_active=true`、`status=ok`、`inventory_complete=true` を確認してから、決済数・実現損益・PF・self/carry 内訳を読む |
| trading の `status.json` | 既存 heartbeat・snapshot に加え、`worker_summary.experiment_status / experiment_reason_code / experiment_entries_allowed` |

交代中は `draining` で新規停止・建玉減少、flat 後は `active / pending-activated`、候補待ちは `waiting-candidate` が目安になる。評価と control の実験・開始時刻が一致しない一時点は `evaluation_identity_mismatch` として数値を非表示にする。`matches_active=null` は現行との一致を確認できていない状態である。control が `blocked` ならその理由を先に調べる。

AI は既定で各試行を最大 180 秒、1 回の生成呼び出し全体を最大 660 秒にする。実際は `各=min(timeout, 180)`、`全体=timeout+60` で設定に依存し、待機を含む残り予算が優先する。検証エラー後の修復生成にも同じ予算を適用する。180 秒化は fallback に時間を残す修正であり、複数回の修復を含むジョブ全体を 660 秒に制限するものではない。

AI 失敗は `timeout / rate-limit / queue-giveup / gate-giveup / provider-failed` 等の固定 `reason_code` へ正規化し、未分類は `unknown` とする。保存済み候補を先に採用した後で次候補の生成が失敗した場合は `improved / changed=true` に生成失敗の理由が併記される。これを「新しい生成も成功した」と解釈しない。

ネイティブ dispatcher の既定統計保存先は `<docich>/tmp/state/llm/stats/`、既存 AI 集計 collector の参照先は `<soren>/tmp/state/ai_stats/` で別である。環境設定による変更もあり得るため、同時刻の radio/prepass の失敗や既存 15 分 AI 集計を PAPER の原因へ直接帰属しない。prompt・生成本文・provider の生エラーを公開診断へ追加せず、固定分類と対象時刻で切り分ける。

## 検証・反映時の確認

主なオフライン回帰は、依存関係を導入済みの開発環境で次のように実行できる。

```sh
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trading_experiment_control.py \
  tests/test_trading_strategy_metrics.py \
  tests/test_trading_strategy_lab.py \
  tests/test_trading_worker.py \
  tests/test_trading_paper_improve.py \
  tests/test_trading_ai_text.py \
  tests/test_paper_improve_retry.py \
  ops/vm_actions/tests/test_paper_improve_diagnostics.py
```

2026-10-03 の統合検証では取引・改善関連 625 passed / 12 skipped / 2 subtests、診断関連 138 passed / 3 skipped / 47 subtests。skip はそれぞれ gVisor guest image 未提供、submodule の `start_all.sh` 不在による環境制約。独立コードレビューで追加 blocker はなく、本番実測や最新 PR HEAD の CI 成功とは区別する。

Runtime checklist:

- registry / manifest / queue: 新規 worker・queue・provider・runtime component は増やさず、既存 registry の診断対象を更新する。新しい control は trading state 内の固定 JSON とローカル lock。
- worker health / telemetry: 既存 heartbeat 契約を維持し、実験制御の 3 項目と固定理由コードを追加する。
- diagnostics / redaction: 固定ファイルの読み取りのみ。ID・戦略本文は出さず、状態 enum・有限数値・真偽値に限定した評価 projection を既存 gateway の sanitize とサイズ制限で検証する。
- deploy 影響: 反映経路は `PR → review/tests/CI → protected main → GitHub Actions → owner-only VM gateway`。反映後は対象 SHA、worker の実行コード、次の評価・停止・flat 交代を別々に確認する。本調査では VM の書き換え・再起動・バナー操作を実施していない。

この checkout から運用正本 `handoff.md` は読めず、内容の推測・複製はしていない。正本を必要とする `ops_brief.py build / check-source` は未実施。main・VM 反映と運用正本の照合は、権限と正本を持つ担当者の次工程として残す。
