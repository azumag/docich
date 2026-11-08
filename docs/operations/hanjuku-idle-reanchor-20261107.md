# 半熟英雄: 指示待ちの城ジャンプ反復（v501、2026-11-07）

## 実測と原因

オーナー報告「しろ→アルマムーンへのジャンプを繰り返す」を、正規の
read-only diagnostics（[run 42358737260](https://github.com/azumag/docich/actions/runs/42358737260)）
と、同じ active runtime の固定状態・判断ログを補助的に読み取り確認した。
本番ファイルの編集やゲーム入力は行っていない。

g1147、第1章、1年4月、v500 の判断ログ（JST）:

- 18:10:27 `cursor_reanchor_open`。実行中の指示はなく、所有者は `map_navigation`。
- 18:10:36 `cursor_reanchor_confirm`。アルマムーンの座標 `[731,805]` に復旧。
  直後に `cursor_reanchor_wait` / `no_ready_order`。
- 18:10:43 同じ座標で `cursor_still_search`（still=6）と再度の `cursor_reanchor_open`。
  この X 入力を `cursor_reanchor_resumed` として記録。
- 18:12:23 2回目は戦闘割り込み中に観測上限となり `cursor_reanchor_failed`。
  後の状態も `active=null` / `nav_search=true`。

`_watch_cursor_still` は移動する指示がない待機中にも同じ座標を数えていた。
待機中の `update_world` で屋根アンカーが得られないと、復旧後6観測で再び
探索状態になる。さらに `map_step` の復旧開始が指示選択より前にあるため、
残存した `nav_search` だけでも指示のない城ジャンプを再開できた。

## 修正

- 実行中の指示がないときは停止判定のカウントを消して待機する。
- `map_step` は実行中の指示があるときだけ城ジャンプによる位置復旧を開始する。
  残存する探索状態は維持し、指示選択・所有調査を進める。次の指示が決まれば
  位置復旧を再開できる。
- 月次処理の明示的な `_start_reanchor_jump(owner='post_month_status')` は維持。
- v501 を診断の版 allowlist に追加。worker・queue・runtime registry は増やさない。

## 検証と残件

関連6ファイルのテストで 518 passed / 21 subtests passed。
待機中の繰り返し判定、古い探索状態で80観測待機、その後の新指示での復旧を
合成状態で確認した。月次処理・通常出撃・城ジャンプ・診断も同時に検証した。
自己レビュー実施、独立レビュー未実施。

本番への反映と、同じ自然進行でのジャンプ反復停止は未確認。
PR/CI/main と正規配備後、v501 の実判断、指示なし待機中のジャンプ非発生、
次の指示時の操作再開を別々に確認する。新しい指示が生成されない理由は
本修正で解決したとは扱わない。

共通 handoff は `DOCICH_HANDOFF_PATH` が未設定で未読・未更新。
本番の作業中バナーは正規スクリプトで開始した。
