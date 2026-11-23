# VERIFY-5213

## 結論
(b) 設計どおり・修正不要

## 判定
- シグネチャ: zero_sortie（戦闘が長引く / 出撃元の将軍がいない）
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1373-egg-receipt-settle`
- 月: ch8 10-11（観測 715回 / 132.5秒 / max gap 0.7秒）
- 出撃: 確定 0 / 発射 0 / order_start 0 / order_launched 0
- 決定内訳: battle_card_announce_retry 9件・battle_card_unclassified 6件・battle_result 8件・battle_retreat_banned 7件
- 翌5分: 決定 658件 / 出撃 6件 / 回復: yes / 初出撃まで 33.8秒
- 監査 JSON の signals: `[battle_grind, no_source]` / signature: `battle`

## 機構の切り分け
- `order_start` は `src/docich/hanjuku/flows/map.py:309` で記録される。`next_order(mem)` が nil 以外の順序を選んだときだけ記録される。
- `next_order`（`src/docich/hanjuku/strategy/orders.py:232`）が nil を返す条件:
  - `_ready(order, mem)` が偽（ボス目標未解放・壊れた英雄指図など）。
  - 出撃元の城が占有外、または城名簿が空。
  - `_source_spare_missing(order, mem, allow_probe=True)` が真: 予備将軍の補給口がある作戦で、城の在住将軍が en_route・未確認名簿外を除いて 2 未満。
  - 将軍が en_route・位置不明。
- `order_start 0` は「開始可能な注文が 1 件も無かった」状態。`sortie_departure_requested` 0 / `sortie_launch_unverified` 0 / `order_launched` 0 と整合する。
- 本件は「発射されない 72 回の開始（#5153 の probe ループ）」ではなく「開始できる注文が一度も無かった」状態。プローブループ自体が存在しない。
- 世界状態は在城 5 城×各 1 名のまま（`sender_min(2)`・`allow_sole=False` の下で 1 将軍城からの出撃は不可）。出撃元の城の将軍は全員存在するが、戦闘に占有されてチャートの各注文が開始条件を満たさなかった。
- 戦闘終了後、調整チャート（`A:002a816e:M7b9b8e` ミモザ→ひめじ）で順序が開始され、5 分後に出撃 6 件へ回復。つまり zero_sortie は「戦闘に占有されている間の瞬間的な零出撃」を時系列通りに拾ったもので、本質的に欠陥ではない。

## 既検証シグネチャとの突き合わせ
- #5190（ch2 3-5、(b) 設計どおり）: order_start 0 で「開始可能な注文が一度も無かった」状態。プローブループ自体が存在しないため #5153 の欠陥機構も PR #5182 の修正箇所も本件には無関係。本件は同種（order_start 0、プローブループなし、攻撃占有）。
- #5191（ch2 どうし、(b) 設計どおり）: boss_retry_exhausted（上限 3）で step failed。戦闘・敗北・差分の全経路が完走しており、発射不良・プローブ空転ではない。本件は別系統（戦闘に占有され順序開始不能）のため直接比較しない。
- #5153（ch8 67-10、(a) 実欠陥）: order_start 72 / order_launched 0。chart_adjust_applied 72 回で同じ要員を繰り返し起動し、発射なし。PR #5182（replan.py stuck-probe fallback 候補選択）が修正済み。本件は order_start 0 でプローブループ挙動が無いため #5153 とは別機構。
- #5159（ch10、(b) 設計どおり）: 敵出撃ゼロの月。zero_sortie の「零」系列だが別シグネチャ（attack_retry_exhausted / no_source)。本件は戦力状態に起因する。
- #5125 系: 同シグネチャ（戦闘が長引く / 出撃元の将軍がいない）。本来の結論は #5190 と同じ (b)。

## 関連
- #5153: probe ループ欠陥（修正 PR #5182 マージ済み）
- #5159: 敵出撃ゼロ（(b) 設計どおり）
- #5190: 法的出撃元ゼロ（(b) 設計どおり）
- #5191: 出撃失敗・boss_retry_exhausted（(b) 設計どおり）
- PR #5182: stuck-probe fallback 修正（#5153 向け。#5213 のシグネチャは対象外）

## 受入条件
- コード変更なし（read-only 検証）
- 本番 SSH 使用なし
- 秘密情報なし
- 検証記録: VERIFY-5213.md をブランチ `verify/5213-zero-sortie` へコミット済み。push・PR なし。
