# IMPLEMENT-4982 — [半熟英雄] 築城可能なのにキャンセル: ch11 48-6

Issue: azumag/docich#4982
判定: `chikujou_cancelled_with_resources`（confidence: high）
ラン g1190-ch1v4 / generation 1163 / hanjuku-chart-v1349-card-panel-scroll-arrows
月 ch11 48-6（観測 170回 / 16.8秒）/ 結果 cancelled / 決定 `chikujou_leftover` / 状態 opened
所持金 9999 / 必要残し 30 / 未試行候補 8城（チリベット、タプロベイン、ジャンセニア、
ゴンダル、マランマ、クラボニア、アエンタール、タタール）
記録された理由: 「築城の城一覧が上限に達して選択できないため、Bで閉じる」

## 原因（欠陥と判断）

城一覧の表示・選択上限（4行＋`_chikujou_scroll` によるスクロール）は 8 候補を
扱える設計であり、候補絞り込み `_chikujou_candidates` も正しく 8 城を返していた。
欠陥は orchestrator の早期ディスパッチにある。

- `orchestrator._decide_impl` の `_is_chikujou_prompt(screen.text)` 分岐
  （`src/docich/hanjuku/orchestrator.py`、旧 1582 行）は v1142（4e78a547c、#4591）で
  「`month_sub` の有無に関わらず B を1回送る」早期経路として導入された。
  66-10 の実測では残った `month_sub` が B を出せず画面が保持され続けたことが理由。
- しかし `_is_chikujou_prompt` は通常の選択可能な城一覧
  （「…などのしろをぞうちくなさいますか?」は `どのしろをぞうちくなさいます`
  を部分文字列として含む）と、一覧が読めるまま残る拒否行
  （「これいじょうのぞうちくはできませんぞ!!」＋一覧）にも一致する。
- この分岐は追跡中の `month_sub_step` → `_chikujou_step`（1969 行付近）より先に
  `return` するため、`_month_chikujou` が一覧を開いた直後の最初のフレームで
  必ず B が押され、追跡フローは城を一切選べない。さらに `chikujou_refused` が
  記録され、以後 2 か月は `_month_chikujou` が再オープンを抑止する
  （`_month_chikujou` の refused/skips 判定）。
- 既存テストは `month_sub_step` を直接呼んでいたため、orchestrator 側の早期
  ディスパッチとの衝突は検出されていなかった。

#4591 が解決したかった停滞は「chikujou 以外の `month_sub` 残り」が原因であり、
chikujou の sub 自身は `_chikujou_wait`（CHIKUJOU_WAIT_LIMIT=6）、
`presses`（CHIKUJOU_SUB_LIMIT=64）、スクロール上限で有界のため保持し続けない。
したがって「追跡中の chikujou sub がある間は早期分岐を発火しない」ことで
#4591 の修正を維持しつつ今回の欠陥を解消できる。

## 変更ファイル

- `src/docich/hanjuku/orchestrator.py`
  `_is_chikujou_prompt` 早期分岐にガードを追加。`mem['month_sub']` が
  `{'kind': 'chikujou'}` の dict のときは発火せず、通常ディスパッチへ落として
  `month_sub_step` → `_chikujou_step` が一覧を所有する（選択・スクロール・
  拒否行の上の一覧からの再選択はすべて既存実装済み、全待機は有界）。
  sub 無し・他 kind の sub の場合は従来どおり B で閉じ `chikujou_refused` を記録。
- `src/docich/hanjuku/version.py`
  `BOT_VERSION = 'hanjuku-chart-v1353-chikujou-list-limit'`。
  マージ時に v1365 へ付け直してマージ: PR #4990、merge commit aad135ee。
- `ops/vm_actions/collect_diagnostics.py`
  許可リストに `"hanjuku-chart-v1353-chikujou-list-limit",` を追加
  （v1350 エントリ直後、末尾カンマ付き、既存エントリ保持）。
  マージ時は v1365 へ付け直し（PR #4990）。
- `tests/test_hanjuku_chikujou_orch_refused.py`
  回帰テスト2件追加:
  - `test_a_live_chikujou_sub_owns_the_prompt_and_picks_the_next_castle`
    追跡中 chikujou sub では早期 B が出ず、`_chikujou_step` が未試行城を
    `chosen` に選んで A を返すこと（`chikujou_leftover` 記録・
    `chikujou_refused` 設定が無いこと）を検証。
  - `test_a_non_chikujou_month_sub_still_closes_the_prompt_with_b`
    recruit 等の別 kind の sub 残りでは #4591 どおり B で閉じることを検証。

## テスト結果

- ローカル実行: **未実施**。本環境で `python3` / `pytest` / `python3 -m compileall`
  の実行がすべて拒否されたため、関連テスト・構文確認ともにローカルでは
  実行できなかった。差分・テストコードはレビューと既存テスト（
  `tests/test_hanjuku_multi_chikujou.py` の `castles()`/`memory()`/`open_build`
  フィクスチャ、`_chikujou_step` の分岐）を根拠に追跡済み。
- 実行予定だったコマンド（未実行）:
  `pytest tests/test_hanjuku_chikujou_orch_refused.py tests/test_hanjuku_multi_chikujou.py tests/test_hanjuku_chikujou_prompt_hold.py tests/test_hanjuku_month_audit.py -x -q`
- 手動トレース（コード読み）:
  `month_sub={'kind':'chikujou'}` が live のとき、ガードにより早期分岐を通過し、
  先行フロー（recall/jump/egg/sortie/battle/trade/bijin/army_count）は全て
  状態無しで `None` を返すため `month_sub_step` へ到達。`_chikujou_step` は
  `ぞうちくなさいます` の一覧で未試行城へ `menu_to` → `here` → `chosen` 設定 →
  `[pad('a')]` を返す。

## 残課題

- 関連テストのローカル実行（上記コマンド）を可能な環境で実施し、結果を
  確認すること。特に新規2テストが想定どおり緑になること。
- 本番観測: v1353（PR #4990 マージ時に v1365 へ付け直し）反映後、
  `chikujou_cancelled_with_resources` が解消し、
  `chikujou_confirm` / `chikujou_continue` / `chikujou_scroll` が記録されること。
- コミット・push・PR は本指示の制約により未実施（worktree に未コミットで残置）。
