# VERIFY-5252 — [半熟英雄] キャンプ帰還失敗: ch9 将軍不明

- 対象: azumag/docich issue #5252 / fingerprint `e082e68efa45a6b3` / occurrence `0f89d4154fc031a37475`
- ラン: `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1419-hero-move-backoff`（v1419 = `b3a54a4ad`、main の祖先）
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 現行 main: `b98c5486c`（v1424）。v1419→HEAD 間で `flows/recall.py` / `flows/camp_recheck.py` の差分なし（`git log b3a54a4ad..HEAD`）

## 結論: (a) 実欠陥 — #5154 と同一機構。現行 main で再発しうる

`fort_status`（および `fort_menu` / `fort_list`）は、操作できない画面に対して
無記録の空リストを返し続ける唯一の経路であり、画面ごとの副上限を持たない。
abort は fail-closed として安全だが、90 観測の全予算を滞留画面へ空転させ、
証拠（将軍・画面 kind・滞留テキスト・fort_status_reads）を一切残さない。
この「有界タイムアウト自体は設計どおりだが、副有界と証拠が欠ける」という
評価は #5154 の (a) 判定と同じであり、#5203 の (b) 判定よりそちらに寄る。

## 根拠となるコード箇所（ファイル・行、main @ b98c5486c）

### 91 steps → camp_recall_limit の経路（検証観点1）

- `src/docich/hanjuku/flows/recall.py:20` — `RECALL_LIMIT = 90`。1 回の帰還試行に許される観測数。
- `src/docich/hanjuku/flows/recall.py:1498-1513` — `_camp_recall_step` は呼ばれるたび `state['steps'] += 1`。`steps > 90` で `camp_recall_aborted` を記録するが、`observed_metric` は `{'stage', 'steps'}` のみ —— issue の「stage: fort_status / steps: 91」と一致し、滞留画面の証拠は残らない。
- `src/docich/hanjuku/flows/recall.py:2133` — `enter_fort` は `camp_kind='dome'` を設定。
- `src/docich/hanjuku/flows/recall.py:1460-1465` — #5108 の text-frame hold は `state.get('camp_kind') != 'dome'` が条件。dome（砦）ルートはこの有界 hold（`RECALL_HOLD_TICK_LIMIT=600`、:43）の適用外。「menu stage の DOME_SCREEN_WAIT が非メニュー画面を持つ」という免除根拠は `menu` ステージ限定（:1717-1730）で、fort_* ステージには届かない。
- `src/docich/hanjuku/flows/recall.py:1479-1484` — #5134 の cursor-less map hold は `stage == 'to_camp'` 限定。同じく `battle_return` reset（:1486-1495）も `to_camp`/`menu` 限定。砦ルートへはどちらも届かない。
- `src/docich/hanjuku/flows/recall.py:2170-2218` — `_fort_step` の `fort_status`: `map_target` → `dest` へ遷移（:2207-2209、成功経路）、`map` → `_leave_fort`（:2210-2211）、`house.general_status(screen)` が `state['general']` と一致 → `fort_status_reads` を進め 4 読みごとに A（:2212-2217）。**それ以外の全画面は `return []`**（:2218）。記録・副上限なし。
- `src/docich/hanjuku/flows/recall.py:2172-2187` — `fort_menu`（`not is_fort_menu` → `[]`）と `fort_list`（`not fort_list_open` → `[]`）も同じ黙って空転する形。
- 一致したパネルでも `map_target` が開かなければ同じ 91 steps を燃やす（`fort_status_reads` は入力ペース用のカウンタで、上限でも abort metric にも出ない）。
- つまり「選択将軍以外のパネル」「fort メニュー/一覧の再出現」「無関係 text」「unknown フレーム」「パネル一致だがピッカー不開」のいずれでも、画面分類可能な限り recall へフレームが来るかぎり全 90 観測を空転する。#5154 の指摘どおり。

### テストが示す設計意図

- `tests/test_hanjuku_fort_camp.py:138-143` — 別将軍の status panel では `[]`（確定入力を送らない fail-closed は設計どおり）。
- `tests/test_hanjuku_fort_camp.py:309-315` — fort ルート内の unknown フレームへは全 stage で `[]`。「分類不能画面に入力しない」は正しい設計。欠けているのは入力ではなく**有界な待機と証拠記録**。

### 既知の判定との関係（検証観点2）

- #5154（v1373 / ch8 / 同シグネチャ）: (a) 実欠陥判定済み。指摘は「fort_status/fort_list が副有界を欠き証拠を残さない唯一の経路」であり、「v1399 でも再発し得る」と予告されていた。本件は v1419 で実際に再発した = その予告の確認。
- #5203（v1373 / ch6 / 同シグネチャ）: (b) 設計どおりで close。「abort が RECALL_LIMIT 到達の有界タイムアウトとして働いた」までは正しいが、副有界・証拠欠落という欠陥部分には踏み込んでおらず、クローズコメント自身も診断強化を #5154 / PR #5175 へ委譲している。両判定は矛盾しない: abort の安全性は設計どおり、**滞留の扱い方（全予算空転＋無証拠）が欠陥**。
- 本件は #5154 と同じ側に寄る。追加の新事実: v1419（#5108/#5134/v1417 再設計 #5237 をすべて含む）でも再発したため、既存のどの修正経路でも砦ルートはカバーされていない。

### 関連 PR のカバー範囲と現行 main での再発可能性（検証観点3）

- PR #5175（#5154 用、Open/Ready、v1405 marker）と PR #5254（#5252 用、Draft、v1422 marker）は同一内容の診断専用差分: stage 別観測カウンタ・期待将軍 vs 実読将軍・screen kind・正規化120文字の画面文言を abort 記録と監査本文へ投影する。入力・遷移・RECALL_LIMIT・backoff は変更しない。
- 両者とも未マージであり、仮にマージされても**診断の追加のみで滞留は防げない**。現行 main では何のカバーもなく、同シグネチャは確実に再発しうる（本件がその実例）。
- 滞留の副経路（別将軍パネル / メニュー逆戻り / 無関係 text / パネル一致・ピッカー不開）は issue の記録から一意に決められない。これ自体が欠陥の一部であり、切り分けには PR #5254（または #5175）の証拠投影が必要。

## 修正案

#5154 の方針を本件でも支持。診断と動作修正は分けて進めるのが安全:

1. **証拠投影（先行）**: PR #5254 / #5175 のどちらか一方をマージする（内容は同一で BOT_VERSION が競合するため片方を採用。新しい #5254 を rebase して採用が自然）。次回発生で滞留画面を特定できる。
2. **fort_status の副有界**: 非一致画面の連続観測を別カウンタで有界化（例: 8–12 観測）。fort メニュー/一覧の再出現なら fort_menu/fort_list へ有界回数の再遷移、それ以外は証拠を記録して早期に fort ルートを抜ける（全 90 予算を使い切らない）。
3. **abort metric の拡充**: `general`・`fort_status_reads`・最終 `screen.kind`・滞留テキストを `camp_recall_aborted` に含める（2 の実装と同時か、1 の診断 PR で既に担保）。
4. **fail-closed は維持**: 別将軍パネルや分類不能画面への入力は増やさない。修正するのは「待機の有界化と証拠」であって入力条件ではない。

## 手順上の注記

- read-only 検証。VERIFY-5252.md の作成以外のファイル編集・本番 VM 操作なし。
- `agent-guards/secret_scan.py` は本 box 未配置のため、投稿物（PR 本文・issue コメント）は同等のローカル秘密パターンスキャンで clean を確認してから投稿する（AGENTS.md §3.1 フォールバック）。
