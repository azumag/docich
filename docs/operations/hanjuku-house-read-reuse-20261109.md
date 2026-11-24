# 半熟英雄: 同じ月に家臣走査が読んだステータスを月次巡回で再利用する（v548、2026-11-09）

## 症状

g1163 第6話 月12-1、2026-11-09。
`house_scan_started`（09:55:57）が全将軍の個別ステータス（卵・HP・所在地）を読み、
その3分後に `post_month_status_armed`（09:58:08）が**同じ将軍たちのステータスを
もう一度**開いていました（`post_month_roster_marker_unverified` 等で1将軍あたり約10秒、
月あたり数分）。

## 原因

`post_month_status.arm()` の `stale`（再読取対象）判定が、**同じ月に家臣走査が
直前に読んだ**という事実を見ていませんでした。章替わり直後は全将軍の所在地が
変わったように見えるため、全員が再読取対象になっていました。

## 修正（v548 `hanjuku-chart-v548-house-read-reuse`）

- `_house_freshly_read(mem)`: `house_eggs` のうち
  - `month` が現在の月と一致し、
  - `location` が既知の区分で `location_observed` が False でなく、
  - `observed_tick` が現在から `HOUSE_READ_FRESH_TICKS`（300）以内
  の将軍を「読取済み」として返す。
- `arm()` の `stale` 計算で、読取済みの将軍は再読取対象から外す。
  古い読取・別月の読取・所在地未観測の読取は従来どおり再読取する。

## 確認

- `tests/test_hanjuku_post_month_status.py` に1件追加: 同月・新しい読取は再利用、
  別月は無効、`location_observed: False` は無効、tick 不正は無効。
- 月次ステータス・家臣・卵・キャンプ 451件 passed。
- 本番反映後: 次の月替わりで `post_month_roster_marker_*` の件数が減り、
  巡回時間が短くなることを実測する。
