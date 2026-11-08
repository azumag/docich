# 半熟英雄: フルページ名簿の末尾確認が非wrap＋画素不一致で未確認終了する（v506、2026-11-07）

## 症状

g1147、第1章、1年8月の月次ステータス確認が `roster_end_unconfirmed` で
`post_month_status_unverified` になり、`post_month_status_last` は
`{complete: false, failed: roster_end_unconfirmed, scope: [1, 1-8]}` で残った。
8人全員の個別状態は読めていた（全員 `しろのなかにいます`）が、名簿走査の
完了にならず、`general_statuses` は 1-6 のまま残った。

## 実測（本番 g1147、JST、run-soren-live の判断ログ）

- step 3011 `post_month_status_armed`（cached 7、joins マカデミア、
  reopen どうし/ゼウス/ビシソワーズ/ヴィーナス、scope [1, 1-8]）。
- step 3023〜3048 に5人の個別状態を読み、残る3人（ココット/
  アマンディーヌ/リースリング）は変更なしのキャッシュとして名簿通過。
- step 3048 マカデミアの状態読み（Bで名簿へ戻る）の後、step 3049〜3061 に
  13連続の `down` が kind `text`（将軍一覧）のまま決定なしで続いた。
- step 3055〜3060 の frame_sha256 は同一（`99bcce...`）。カーソルは最終行で
  動かず、折り返しもスクロールも起きていない。
- step 3062 `post_month_status_unverified`（`roster_end_unconfirmed`）、
  step 3159 `post_month_sequence_finished`（`failed` 付き、v505 の解放経路）。
- 実フレームは 299x224（`screenshots/latest.png` で確認）。

## 原因

末尾確定の3経路がいずれも成立しなかった。

1. 短ページ経路（`len(names) < 8`）: 8人でページが埋まるため不成立。
2. 画素経路（`_roster_scroll_end`）: `frame.width == 256` を要求するが、
   本番フレームは 299x224 のため常に False。
3. 折り返し経路（`wrapped`）: この一覧は最終行の `down` が無変化
   （no-op）のため `selected == first` に戻らず不成立。

結果として `phase_reads > 12` で `roster_end_unconfirmed` に落ちた。
`post_month_status_unverified` のログに証拠が無く、切り分けに判断ログの
突き合わせが必要だったことも付随する問題。

## 変更（v506）

- `flows/post_month_status.py`
  - `END_STALL_READS = 3` を追加。既読名の観測が可視最終行で連続した回数を
    `end_stall` / `end_stall_selected` で数え、フルページ（既訪8人以上）の
    最終行で3連続かつ `_complete_scan` が通れば完了（B）する。
    `_complete_scan` が通らない部分走査では発火せず、既存の12回ガードが
    そのまま未確認終了する（誤った除籍をしない）。
  - 選択が最終行以外へ動いたら stall を破棄する。新規名の追加（キャッシュ
    通過・個別状態読み）でも破棄する。
  - `reenter_after_reanchor` と `resume_after_battle` の状態破棄集合へ
    `end_stall` 系を追加した。
  - `roster_end_unconfirmed` の `post_month_status_unverified` に
    `observed_metric`（names件数・selected・top_confirmed・dirty・
    phase_reads・end_stall）を付けた。理由文は不変。
- `version.py` を `hanjuku-chart-v506-roster-end-stall` へ更新し、
  `ops/vm_actions/collect_diagnostics.py` の許可集合へ追加する。
- 回帰テスト3件（`tests/test_hanjuku_post_month_status.py`）。

## 変更しないこと

- 未確認の将軍状態を `general_statuses` へ書かない（従来どおり）。
- 部分走査での除籍をしない。`_complete_scan` の membership proof
  （先頭確認・dirtyなし・unknownなし・人数不一致なし）を緩めない。
- 299x224 用の画素再校正はしない。stall 経路で画素に依存せず完了する。
  `house.py` の月次名簿走査にも同型の3経路があるが、今回の失敗経路では
  ないため対象外とする。
- 帰還（recall）・卵修理（house）・計画（next_order）の予算と所有者は不変。

## 検証

- 新規3件は修正前に1件が失敗（再現: 13 downs で未確認終了）し、修正後に成功。
- `tests/test_hanjuku_post_month_status.py` 55件成功。
- 関連 `test_hanjuku_roster_scroll_end` / `test_hanjuku_house` /
  `test_hanjuku_castle_jump` / `test_hanjuku_spent_sortie_expiry` /
  `test_hanjuku_arrival_status_probe` 177件成功。
- `test_hanjuku_chart_bot` / `test_hanjuku_recruit_roster` /
  `test_hanjuku_survey_cache` 504件成功。

## 未確認・残件

- 本番の次回月次確認（フルページ名簿）で `post_month_status_complete` が出て
  `post_month_status_unverified`（`roster_end_unconfirmed`）が出ないことを実測する。
- `_roster_scroll_end` の 256x224 固定は残る。stall 経路が実質の完了手段に
  なるが、画素証拠が無い completions が増える。299x224 用の再校正は別変更。
- `house.py` の月次名簿（`_month_count` 系）も同じ3経路のため、フルページで
  同じ停止があり得る。実測したら別途対応する。
