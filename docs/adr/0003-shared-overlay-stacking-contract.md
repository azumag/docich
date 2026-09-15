# ADR 0003: 共通オーバーレイ（共有レール）のレイヤリング契約

- Status: Proposed
- 関連 Issue: [azumag/soviet_now#303](https://github.com/azumag/soviet_now/issues/303)（Soren91 macOS renderer → OCI SRT → docich corner）
- 関連 PR: [azumag/soviet_now#345](https://github.com/azumag/soviet_now/pull/345)（本ADRの決定を実装）
- 関連実装（soviet_now）: `start_shared_overlay_service.sh` / `game_lifecycle_control.sh` /
  `deploy/soren-shared-overlay/` / `lib/shared_overlay.mjs`

## 0. 前提と調査方法

production VM の配信ディスプレイ `:99` について、**read-only の実測のみ**で以下を確認した。

- `Xvfb :99 -screen 0 1280x720x24 -nolisten tcp`（**depth 24**、alpha なし）。
- `xprop -root _NET_WM_CM_S0` → **not found**（コンポジタが動いていない）。
  `xfconf-query -c xfwm4 -p /general/use_compositing` は `true` だが、実際の compositor
  selection owner は存在しない。
- したがって **32bit visual が無く、ARGB ウィンドウのピクセル透過は利用できない**。
  （Xvfb の depth を変える選択肢は、配信エンコーダを巻き込む :99 の再起動が必要になるため採らない。）

再現・修正の確認は**隔離ディスプレイ**（`Xvfb :105` + `xfwm4`）で行い、production `:99` には
一切変更を加えていない。production 側の実測は PID・ピクセル・`/healthz` の読み取りに限った。

## 1. 現状（As-Is）

- 共通オーバーレイ（`soren-shared-overlay.service`）は **1280x720 の fullscreen Chromium**。
  `lib/shared_overlay.mjs` が body/ステージを不透明（`#050914`）、ゲーム矩形を `#000` で塗る。
- ゲーム切替（game-only モード）の開始時に `game_lifecycle_control.sh` が unit を start し、
  メインゲーム復帰時に stop していた（Issue #303 option A）。
- メインゲーム表示中は、ゲームのページ（1280x720）が**自前のレール**を描いている。
  presenter（`docich-present-*`）はゲーム矩形 `(0, 90, 960, 540)` ちょうどのサイズで出る。

## 2. 問題（2026-09-15 18:00 の定時 soren91 コーナー）

- 配信が **約4分間まっくら**（18:01:17 に overlay start → 18:05:25 に stop）。
- X11 では**後から map したウィンドウが上**に来る。overlay を start した時点でメインの
  sorengame はまだ表示中（`draining`）だったため、**動作中のゲームウィンドウを overlay が覆い**、
  レール＋空のゲーム領域（黒）だけが配信に出た。
- 「メインゲーム表示中は overlay を起動しない」という option A の前提は、
  **overlay の起動が停止処理の前提条件**（bridge が不可逆停止の前に `/healthz` を要求する）
  である以上、構造的に守れない（起動とゲーム表示が必ず重なる）。

## 3. 決定（To-Be）

1. 共通オーバーレイは **常時起動**とする（boot で `systemctl enable`）。ゲームのライフサイクルに
   連動した start/stop は行わない。
2. overlay のウィンドウは **X スタックの最下層**に固定する（`_NET_WM_STATE_BELOW` を再表明）。
   `docich-present-*` は `above` を維持する。
3. ゲーム／presenter のウィンドウは overlay より上に載る。**ゲーム矩形はゲームが所有**し、
   overlay はゲームが覆わない領域（共通レール）だけを埋める。
4. **ピクセル透過（透明化）は採用しない**。§0 の表示制約により不可であり、
   最下層固定で同じ見た目（レール常時表示＋ゲーム領域はゲーム表示）が得られる。
5. `/healthz` readiness gate は維持する。overlay が落ちていれば bridge は `unsupported` で
   fail-open し、不可逆な停止は行わない（旧ゲームが継続するだけで配信は壊れない）。

## 4. 影響

- メインゲーム表示中: ゲームページ（1280x720）が overlay を完全に覆うため**見た目は不変**。
- ゲーム専用モード: ゲーム矩形は presenter、周囲は overlay のレール。
- overlay が disable/failed のとき: レールは出ないが、readiness gate が落ちるため
  ゲーム切替の不可逆停止は行われない（fail-open）。

## 5. 検証

- 隔離ディスプレイ（`Xvfb :105` + `xfwm4`）で再現と修正を実測:
  ゲームウィンドウを先に表示 → overlay 起動 → ゲーム領域のピクセルが**黒**（再現）。
  `wmctrl -i -r <overlay> -b add,below` → ゲーム領域が**再表示**、`/healthz` は `ready:true` のまま、
  10秒後も `below` を維持。
- soviet_now: `node --test tests/test_shared_overlay.mjs`（28 pass）、
  `bash tests/test_game_lifecycle.sh`、CI `Shared overlay contracts`。
- 契約テストで「wrapper が overlay を below に固定する」「lifecycle 制御が unit を
  start/stop しない」「unit が boot で install 可能」を固定した。

## 6. 未解決・次段階

- メインのレールは**今もゲームページ側が描いている**。共有オーバーレイへの一本化
  （各ゲームをゲーム矩形サイズのウィンドウにし、レールを共有側へ寄せる）は次段階。
- production `:99` でのコーナー再実行による最終確認は**未実施**（隔離ディスプレイでの確認のみ）。
- 常時起動化には production での `systemctl enable --now soren-shared-overlay.service` が必要。

## 7. 再発防止

- overlay のウィンドウを最前面に上げない。再起動直後は
  `start_shared_overlay_service.sh` の stacking ループが `below` を再表明する。
- production checkout 上の tracked file の直接編集や ad-hoc な production 操作を行わない
  （`AGENTS.md` §9 の deployment contract）。
- 隔離ディスプレイの後片付けで `pkill -f <pattern>` を使わない。pattern が自分自身の
  リモートシェルや production `:99` の `xfwm4` にも一致し得る。対象は
  `/proc/<pid>/environ` の `DISPLAY` 等で限定する。
