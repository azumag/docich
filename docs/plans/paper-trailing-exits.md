# 株・FX PAPER: 固定出口とトレーリング出口の比較

## 範囲と既定動作

2026-09-29の依頼に対する第一段階。対象は `trading/markets/` の株・FX。
暗号資産の `trading/strategies.py` / 実験DSL、実口座、発注API、配信、VM、設定ファイルは変更しない。
既定は `exit_mode="fixed"`。旧6項目のpolicy JSONを読め、fixedのversion hashも従来と同じ。
既存ポジションをトレーリングへ変換せず、新規約定時に出口条件をポジションへ固定する。

## 出口契約

追加policy項目は `exit_mode` (`fixed` / `trailing`)、`trail_activation_bps` (整数5..600)、
`trail_distance_bps` (整数5..300、activation未満)。bool、非整数、未知項目、不正な組合せは拒否する。
既定のactivation=100、distance=50は実装上の初期値であり、最適化済みの推奨値ではない。

trailingでは初期損切りを残し、一定の含み益から追従を開始する。固定利確だけを置き換え、
日次リスク停止・最大保有時間・シグナル反転・セッション終了決済は残す。
ロングは売却側bidの最高値、ショートは買戻し側askの最安値を観測する。
ロングのstopは `max(旧stop, 最高bid * (1-distance/10000))`、ショートは
`min(旧stop, 最安ask * (1+distance/10000))`。有利な方向にだけ動かす。
activationは取得価格に対する決済側quoteの騰落率であり、純利益・建値撤退の保証ではない。

有効かつ同銘柄の前回より新しいquoteだけを使う。古い・未来・同時刻再送・順序逆転・
売買不能・過大スプレッド等のquoteでstopを更新したり約定したりしない。観測していない
バー内高値・安値や停止中の値動きは補間しない。この時刻ゲートは比較する両口座へ同じように適用する。

stopは発動条件であり約定価格ではない。発動後は観測bid/askに従来のslippage/feeモデルを
適用する。板数量不足では全量決済を捏造せず、決済理由と発動時刻・stopを保存して再試行する。
価格が戻っても決済要求を取消さない。`pending_liquidation` に未完了の決済要求も含める。
極値・stop・発動済み状態・約定・集計は既存SQLite transactionで一緒に保存し、再起動や
再送で巻き戻し・二重約定をしない。明示的trailingポジションの状態欠落はエラーにして固定出口へ落とさない。

## 比較と採用境界

既存の `lab.propose` が厳格JSONでtrailing候補を提案できる。trailingを含む比較では
kind/lookback/entry_bps/stop_bps/take_bps/max_hold_sの6項目をbaselineと同一に強制する。
エントリーと安全条件を同時に変更した候補は拒否する。

既存の独立したbaseline/policy PAPER口座へ、提案時点より後の同じ価格列・エントリー許可・
終了ゲートを渡す。提案前のキャッシュquoteは、まだ鮮度範囲内でも比較証拠として使わない。
実際のエントリー回数は、決済後の資金・保有差で一致しない場合がある。同一約定単位の反実仮想比較ではない。

最低24時間・各10決済という既存の収集条件に加え、両口座flat・比較時の新しい有効quoteを要求する。
結果は `experiments/<id>/verdict.json` と `experiment-status.json` に保存し、状態は
`paper_review_required`。`candidate_passes_screen` は既存の純益/DD条件の判定にすぎず、
統計的優位性・収益保証・採用承認ではない。trailingを含む比較は **active-policyを自動変更しない**。
この境界は検証済みpolicyから導出し、markerの `exit_comparison=false` で迂回できない。
固定出口同士の従来の改善・採用経路は維持する。`live_enabled=false` を維持する。

## 観測指標

既存の費用込みequity/realized/unrealized/max_drawdownに加え、close fillへ次を保存する。

- `reason`: trailing_stop / stop_loss / take_profit / max_hold / signal_reverse / risk_stop / session_end。
- `max_net_pnl_jpy`: 決済側quoteから同じ費用・slippage・累積保有費で計算した観測最大純損益。下限0。
- `peak_to_exit_giveback_jpy`: max(0, 観測最大純損益 - 決済純損益)。損失決済では最大含み益を超え得る。
- `mfe_tracking_from_entry`: 取得時から観測できたか。既存ポジションはfalseにし、過去の最高利益を捏造しない。
- 発動quote時刻・発動stop・出口方式。

`exit_stats` は新形式導入後の決済数・勝数・純損益・giveback・完全観測取引数をDecimalで増分集計する。
旧取引を遡及集計した値ではない。比較口座は新規作成なので開始から記録する。
状態・fill・集計は既存snapshot/health/report経路に載せる。新規workerやqueueは追加しない。

## 検証と残件

`PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_market_paper*.py' -v`
で既存Market paper contracts workflowが新規テストも収集する。新規30テストはネットワーク・
認証・AI呼出し・実ブローカー・ゲームプロセスを使わない。全ての価格と損益は合成fixtureである。

本番での比較開始・実績収集・採用はこの変更作業では実行しない。実データでの収益改善は未確認。
動的株ユニバースでは、候補口座だけに残る銘柄のquote継続供給を別途確認する。欠損時に
強制的な架空決済や不完全な比較判定をするフォールバックは追加しない。
暗号資産への適用、ATR等の可変幅、同一エントリー約定単位の出口比較、番組用の専用表示は別段階。
