# 半熟英雄: 章が未確定なら城のステータスを開いて章を確定する（v646、2026-11-10）

## 症状（本番 g1163 で実測）

第8話でゲームオーバー後に章の最初からやり直しとなり、ボットの `mem['chapter']` が None、
`mem['resume_chapter_pending']` が True のまま動いた。城メニュー（しゅつげき/ステータス）で
`actions=[]` が101観測続き、no_input_fallback の B → マップで A → 再び城メニュー、の無限ループになった。

## 原因

- `orchestrator.py` の城メニュー・マップ系ディスパッチはすべて `mem.get('chapter')` を要求する。
- 章を確定できる経路は、城ステータスの城名を `observe_chapter_castle` に渡すことだけ。しかし章不明のとき
  城ステータスを開く処理が無かった（`resume_sortie_step` は `resume_sortie_cancel` がある復元直後限定）。
- 第8話の本城はチャート上ひらがなの `あるまむーん` で、`CASTLE_NAMES` では第8話にしか無い一意な名前だが、
  `observe_chapter_castle` の除外集合に入っていて証拠にできなかった。

## 変更

1. `strategy/campaign.py`: 章不明時の除外集合から `あるまむーん` を外す。カタカナ `アルマムーン`
   （他の全章共通）は除外のまま。一意性（`len(matches) == 1`）の条件は不変。
2. `flows/deploy.py`: `chapterless_status_step(screen, mem)` を追加。章が無い城メニューで
   「ステータス」へカーソルを動かして A で開く（月ごとに最大 `CHAPTER_PROBE_OPEN_LIMIT = 3` 回）。
   パネルが出ても章が確定しなかった城名は `chapter_probe_ambiguous` として記録し B で閉じる。
   状態は `mem['chapter_probe'] = {month, opens, seen}`。章が確定したら破棄する。
3. `orchestrator.py`: `castle_menu` かつ章なしの分岐を追加（既存の先行分岐の順序は不変）。
4. `flows/prompts.py` `observe_events`: `resume_chapter_pending` が True でなくても、章が無ければ
   城名で `observe_chapter_castle` を呼ぶ（running 判定と一意性判定は関数側が持つ）。
5. `BOT_VERSION` を `hanjuku-chart-v646-chapterless-status-probe` に更新、診断許可リストにも追記。

既存テスト `tests/test_hanjuku_resume.py` のうち「`あるまむーん` は章を確定しない」という1ケースは、
上記1.の意図的な仕様変更により外した（新テストで逆を固定）。

## 確認

- `tests/test_hanjuku_chapterless_status_probe.py`（合成フレーム＋ g706 の実フレーム。章なしの
  城メニュー → 下 → A → スペランザのステータスで第2話が確定する流れ）。

## 未確認事項

- 本番で第8話本城のステータスが実際に `あるまむーん` と読めるかは、反映後に実測する。
- 本城がカタカナ `アルマムーン` の章（8以外）で章を失った場合は、依然として確定できない
  （別の城へカーソルを動かす処理は未実装。上限3回で `[]` に戻り no_input_fallback に任せる）。
- g1163 の実機で無限ループが解消するかは未検証（反映前）。
