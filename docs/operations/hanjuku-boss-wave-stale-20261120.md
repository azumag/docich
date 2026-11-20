# 半熟英雄: 入城しないボス波をいつまでも追わず、城へ戻して次の波を編成する（v1258、2026-11-20）

## 症状（本番 g1288 / v1254〜v1256 の実測）

第9話のボス段階で、7将軍のボス波が **1〜9時間前の出撃記録のまま** 追撃され続けていた。

- `boss_wave_generals` の `launched`: カシス 1475 / ピオーネ 3960 / コンポート 14703 /
  マスカット 15926 / ギー 20335 / バタール 23559 / コリアンダー 23839。
  計測時の tick は 32000 台で、最も新しい隊でも約8600 tick（約2.4時間）前。
- 追撃は約90〜150秒ごとに再開し、Yジャンプ→ボスセルへいどうを繰り返すが、
  ボス交戦は0（`hanjuku_month_audit.json` の `boss.battle_count = 0`）。
- 月次監査は `launches 0 / departures 0 / order_start 0` が続く。将軍は全員フィールドに
  残っているため、どの城にも余剰がなく、新しい波を編成できない。

つまり「古い波を追い続ける」「城に戻らない」「次の波が出ない」が同時に起きていた。

## 変更

- `flows/boss_pursuit.py`:
  - `PURSUIT_STALE_TICKS = 3600`（1時間）。`wave_stale(mem)` は **最も新しい**
    ボス波の `launched` が上限を超えたとき True。
  - 停滞中は追撃を開始せず、`boss_wave_stale` を1回だけ記録してフィールドを明け渡す。
    新しい波が記録されれば `boss_wave_stale_noted` は消え、追撃が再開する。
- `flows/recall.py`: 停滞中は `boss_redirect_goal` が None を返し、野営をボスへ
  再投入しない。野営は通常の帰還経路で城へ戻り、城から新しい波を出せる。
- `state/regions.py`: `boss_wave_stale_noted` を章スコープ（FLOW）で宣言。
- `version.py`: `hanjuku-chart-v1258-boss-wave-stale`。
- `ops/vm_actions/collect_diagnostics.py`: hot-load許可リストへ追加。

## 検証

- 追加: `tests/test_hanjuku_boss_wave_stale.py`（4件）
  - 上限超過の波は追撃せず、`boss_wave_stale` を1回だけ記録する
  - 新しい波なら従来どおり追撃を開始する
  - 波が無いときは停滞扱いにしない
  - 停滞中は野営のボス再投入（`boss_redirect_goal`）が None、通常時はボス城
- 既存: 追撃5ファイル・第9話野営帰還・キー台帳・状態領域 109 passed。
- `tests/test_hanjuku_boss_pursuit_gap.py` のメモリを「新しい波」に更新（停滞ゲートと分離）。

## 未確認・残件

- 実機で停滞判定後に野営が城へ戻り、城からの出撃が再開するかは未確認。
  `boss_wave_stale` の記録、`boss_wave_generals` の減少、`order_start` の再出現で確かめる。
- ボスへの入城そのもの（移動ボスへの追いつき）は別問題として残る。
