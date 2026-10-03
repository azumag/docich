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

以下の閾値自体は既存値を維持する。決済数は評価対象の売り約定数 `closed_sells` であり、部分売却も含む。

| 条件 | 理由 | 動作 |
| --- | --- | --- |
| 8 決済以上、実現損益が負、PF が非 null かつ 0.75 未満 | `early-stop` | 候補の有無によらず新規停止。旧 exit を継続し、flat で候補がなければ `waiting-candidate` |
| 20 決済以上 | `sample-complete` | 候補があれば新規停止して交代準備 |
| 開始後 48 時間以上 | `max-age` | 候補があれば新規停止して交代準備 |

20 決済と 48 時間は **いずれか** でよい。早期停止に該当せず候補もなければ、通常の実験は観測を継続する。一度交代準備へ入った同じ実験は、停止理由を control に保存するため、一時的な評価失敗や残存建玉の損益改善では新規エントリーを再開しない。

既知の実験の active ファイル欠落・破損、control 破損、評価不能、原価不足は新規停止に倒す。旧ルールが読めれば exit は継続し、ルール自体が不明なら legacy exit に置き換えない。実験を一度も開始していない legacy 状態は区別する。現行と同じ ID でルールだけ変える候補は拒否し、同一候補の再提出でも評価開始時刻をリセットしない。AI 処理中に現行実験が変わった場合は `baseline-changed` で古い基準からの書き込みを止める。

## 戦略判断と成績の補正

- **売却コストを含む exit 判断:** [`paper.py`](../../src/docich/trading/paper.py) の共通約定価格計算を使い、売却時の手数料・スリッページ控除後価格を購入原価と比較する。既定は片道 12 bps の手数料と 5 bps のスリッページ。購入原価には買い側コストが既に含まれるため、重複控除しない。新しい理由表示の単位は `net_pnl_bps`。
- **OR 条件の採点:** [`strategy_lab.py`](../../src/docich/trading/strategy_lab.py) は成立した条件の方向付き余裕をスコアへ使う。不成立条件の大きな乖離を絶対値で加点しない。
- **観測不足の修正:** 最大 lookback は 24 期間のまま、worker は 25 本の終値を取得する。24 期間のリターン・RSI 等に必要な先頭の終値を確保する変更で、lookback を 25 へ拡張するものではない。

[`strategy_metrics.py`](../../src/docich/trading/strategy_metrics.py) の評価は schema 2。SQLite の単一スナップショットから全履歴を平均原価方式で再構成し、他戦略の売買も在庫・原価の更新へ反映する。評価対象は現行実験の開始後に、その実験の exit 判断で売却した約定（`accounting_scope=experiment_exit_decisions`）。損益を今回の実験が買った分 `self_entry_realized_pnl_jpy` と、前期間・他戦略由来の分 `carry_in_realized_pnl_jpy` に比例配分し、両者の合計を `realized_pnl_jpy` とする。

混合原価の売却は self/carry 両方の内訳件数に含まれるので、内訳件数の単純合計は総決済数とは限らない。原価が復元できないときは `partial / ledger_incomplete_basis` とし、ゼロ損益の正常評価にしない。PF とドローダウンもこの実現損益の範囲であり、含み損益や戦略全体のリターンを表さない。carry の利益を今回の entry の成果とみなさない。今回確認したのは会計・制御の整合性であり、将来の収益改善は継続観測が必要。

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
