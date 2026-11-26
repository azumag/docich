# docich#5704 実装記録: 築城一覧走査の早期打ち切り修正

## 背景と結論

VERIFY-5704.md の検証どおり、ch12 `27-2` では gold 9815 / reserve 30 で、staffed・affordable な未試行候補アファニア・ファンゴルが残っていた。しかし築城一覧の4行窓は既試行またはレベル5の行だけを表示し続け、同一 signature の停滞が4観測続いた時点で `castle_scroll_no_progress` により走査を終了した。その後 `chikujou_held` から B で戻り、verified な支払い・増築がないまま月が skipped になった。

今回の実装は、この挙動を実欠陥として修正する。目的は、buildable な候補が残っている限り、一覧入力の停滞だけで月を諦めず、候補行の選択・レベル5実測・増築拒否実測のいずれかへ進むことである。

## 主欠陥の修正

`src/docich/hanjuku/flows/month.py` の `_chikujou_scroll` を schema 2 の双方向走査へ更新した。

- 走査対象は従来どおり `_chikujou_candidates` の未試行候補であり、選択・確認ロジックは変えていない。
- 章ごとの城カタログ順と現在の4行窓から、残候補が上側・下側のどちらに近いかを判定し、初期方向と継続方向を決める。
- 画面に現れた城キーは `seen_keys` / `seen` へ蓄積し、途中で観測が途切れても「既に確認した行」と「未発見の候補」を decision log に残す。
- 同一 signature が続いた場合は即座に打ち切らず、逆方向 probe を行う。
  - probe が動けば、その方向へ走査を継続する。
  - probe も動かなければ `castle_scroll_input_stalled` として記録し、入力を押さずに有限回待って再試行する。
- 逆方向が動いたことだけをもって原先端とみなすことはしない。原先端として確定するのは、カタログ上の最初または最後の城行にカーソルがあるという、一覧外が存在し得ないことが分かる場合だけにした。これにより、一方向だけの一時的な入力欠損を一覧終端と誤認しない。
- 打ち切りは次のいずれかに限定する。
  - カタログ上の上端・下端の両方へ到達した場合。
  - 新しい城行が `len(labels)+4` 回の変化観測にわたって現れず、所有城一覧を一周しても候補行が見つからない場合。
- 打ち切り理由は旧 `castle_scroll_no_progress` ではなく `castle_scan_exhausted` とし、最終可視行・既視行・未到達候補・scroll回数・停滞数を `chikujou_held` に残す。
- 入力停滞は月を即座に impossible と判断する根拠にしない。全体の有限性は既存の `CHIKUJOU_SUB_LIMIT` と `month_sub` abort 経路が担う。

## 診断欠陥1の修正

`chikujou_held` と `_finish_month_sub` による skipped 確定が別観測になると、`mem['_records']` は観測開始時に初期化されるため、従来は同月の hold を逆引きできなかった。

今回は `_record_chikujou_hold` / `_store_chikujou_hold` を追加し、直近の同月 hold を `shop['chikujou_last_hold']` へ bounded な形で保持する。`_note_chikujou_skip_unexplained` はまずこの永続状態を見て、同月 hold があれば `chikujou_skip_after_hold` を記録する。`_records` の逆順参照は互換 fallback として残した。

走査入力が回復した場合は、同月の `castle_scroll*` hold を `_clear_chikujou_scroll_hold` で消し、古い停滞を後の skipped 判定へ残さない。

## 診断欠陥2の修正

以下の decision record に `month` を付けた。

- `chikujou_scroll`
- `chikujou_scroll_stalled`
- `chikujou_held`
- `chikujou_max_level_seen`
- `chikujou_castle_unavailable`

これにより、監査 `_track_chikujou` の same-month evidence filter から同月のレベル5・増築不可 evidence が落ちにくくなる。

## 監査証跡の強化

過去の dangling commit `0d88128af` / `5d01f8fa1` / `f061db8d5` の方向を参照し、現行コードへ築城 incident 向けに適用し直した。

- `month_audit.py` に `_chikujou_terminal_diagnostic` を追加し、同月最後の `chikujou_held` から bounded な終端証跡を抽出する。
- `_build_chikujou_incident` の `chikujou_event` に `terminal_diagnostic` を添付する。
- issue rendering の築城節へ、最終保留理由、最終可視行、scroll/stalled 数、最終画面種別・文言を出す。

## バージョン

- `src/docich/hanjuku/version.py`: `hanjuku-chart-v1569-chikujou-scan`
- `ops/vm_actions/collect_diagnostics.py`: allowlist に同値を登録
- `ops/vm_actions/tests/test_collect_diagnostics.py`: projection 固定値 assertion と固定 version list に同値を登録

採番根拠: 作業割り当て時点の main は `hanjuku-chart-v1567-sortie-panel-occluded` であったため、次の未使用番号として v1568 を使った。検品ゲート時の rebase で main が v1568（PR #5709）へ進んでいることが判明したため、v1569 へ再採番した。

## 対象ファイル

- `src/docich/hanjuku/flows/month.py`
  - 双方向築城一覧走査
  - 入力停滞の probe / wait / retry
  - catalog end 判定
  - scan 終端証跡の記録
  - `shop['chikujou_last_hold']` の永続化
  - 同月 `month` の付与
- `src/docich/hanjuku/month_audit.py`
  - terminal diagnostic の抽出・incident添付・rendering
- `src/docich/hanjuku/version.py`
  - v1568 へ更新
- `ops/vm_actions/collect_diagnostics.py`
  - v1568 allowlist 登録
- `ops/vm_actions/tests/test_collect_diagnostics.py`
  - v1568 固定値テスト登録
- `tests/test_hanjuku_chikujou_scroll.py`
  - 双方向走査・逆方向 probe・入力停滞回復待ち・`castle_scan_exhausted` の回帰テスト
- `tests/test_hanjuku_chikujou_skip_note.py`
  - 観測バッファ reset をまたいだ `shop['chikujou_last_hold']` の回帰テスト
- `tests/test_hanjuku_month_audit.py`
  - terminal diagnostic rendering の回帰テスト
  - same-month `chikujou_max_level_seen` が監査 evidence filter を通る回帰テスト

生成 mirror である `src/hanjuku_speedrun` / `tests/hanjuku_speedrun` は手編集していない。

## 検証状況

- 依頼どおりローカルの pytest は実行していない。CI で検証する。
- `python3 -m compileall` は実行可能と明示されていたが、この環境では shell 実行承認が拒否されたため未実行である。構文は差分レビューで確認したが、最終的な構文・テスト検証は CI に委ねる。
- push、PR作成、issueコメント、Slack投稿、git fetch/rebase/push、VM操作は行っていない。
