# IMPLEMENT-5020 — 出撃先ピッカーの戦闘割り込み再開（改善案 1・2）

対象: docich#5020 / DSH 判断（2026-11-23）で改善案 1・2 を採用。条件 1〜5 にすべて対応。

## 実装内容

- `strategy/orders.py`：`SORTIE_PICKER_RESUME_LIMIT = 2`（同月・1 step あたり最大 2 回の再オープン）。`sortie_resume` 台帳（month + used + armed/owed）で同月限定・回数制限を管理。`launch_interrupted_by_battle` に再開の帰結（`resumed` / `established` / `exhausted`）を `resulting_event` として持たせ、#5138 の計測語彙と合わせた。
- `flows/map.py`：`observe_sortie_transition` で戦闘割り込み時に台帳を arm し、予算切れなら `exhausted` を記録。マーカー復帰は予算を消費せず `resumed`（driven=False）、再駆動の再オープンは予算を 1 消費して `resumed`（driven=True）。未確認出撃の検証は resume 対象を優先。
- `flows/deploy.py`：`_verify_sortie_source` でキャンセル時に resume が owed なら step を active のまま維持し、再駆動が直後に同じ将軍・切り札で再送する。発射が確定した場合は `established` を記録。
- `state/regions.py`：`sortie_resume` を FLOW / chapter 寿命として宣言。
- `version.py`：`v1396-sortie-picker-resume`（v1393〜v1395 は他 PR が使用中のため次空番）。allowlist・固定値テストも同期。

## DSH 条件への対応

1. 再開は行き先ピッカーの再オープンのみ。将軍・切り札は据え置き（再駆動は同じ order を再送）。ピッカー画面は実読みし、`planned_target` が一覧に無ければ不成立として数える（既存の検証経路）。
2. 同一月内のみ・最大 2 回。2 回失敗したら `launched_unconfirmed` 終端へ（`exhausted` を記録）。`sortie_target_resumed` 経路は維持（予算非消費）。
3. 回帰テスト：割り込み→再開→成立／2 回で打ち切り／月を跨がない／マーカー復帰は予算非消費（`tests/test_hanjuku_sortie_interruption.py` に 4 件追加）。
4. `launch_interrupted_by_battle` に `resumed` / `established` / `exhausted` を持たせた。
5. バージョンは v1396（着手時の次空番）。

## 検品ゲートでの追加修正（Muse）

新規テスト `test_two_failed_picker_reopens_cut_off_to_launched_unconfirmed` が当初失敗した。原因は既存のリトライガード（`SORTIE_CANCELLED_OBSERVED_LIMIT = 3`）で、resume で処理済みのキャンセルも数えて 3 回目で order を `failed` にしていた。`_sortie_resume_resumed` で再開成功時に当該 step のキャンセル回数をリセットし、再開で進捗した分はガードの対象外とした（再開予算 2 回/月で無限ループは起きない）。修正後、関連テスト 646 件が全て green（環境起因の 2 件を除く）。

## 検証

- `tests/test_hanjuku_sortie_interruption.py` 41 件、関連セット 279 件、map 系 183 件、month_audit・recall 系 184 件が green。残る失敗は環境起因の 2 件のみ（root での chmod-0 読み取り）。
- secret scan clean、mojibake 0。
