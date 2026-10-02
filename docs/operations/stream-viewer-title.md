# ゲーム切替時の配信タイトル

ゲーム切替の確定後、既存のstream-category hookはTwitchカテゴリとタイトルを
同じ `update_stream_game.sh` 呼出しで更新する。Soren91の既存の直接同期経路も
同じ `viewer_title_args` を使う。

タイトルは `[dayN] ソ連ゲーム AIプレイ配信` のような視聴者向けの固定文面。
半熟英雄、NetHackなどは現在ゲームの公開名を使い、paper-viewは
`[dayN] ペーパートレード AIの検証配信` とする。投資成果や攻略成功を約束しない。
日数・日付の計算と140文字上限は既存Soren updaterが所有する。

`--activity` と `--strategy` は両方とも非空で明示し、運用見出し、PR番号、
チャート版、環境に残った戦略名を自動タイトルへ流さない。未知の追加ゲームは
検証済みgame IDだけを使う。カタログ追加時は公開表示名の回帰も更新する。

カテゴリ未設定ゲームのskip、ゲーム名検証、private log、独立transient unit、
既存lockによる非同時実行、API失敗でゲーム切替を巻き戻さない方針は維持する。
資格情報、配信エンコーダ、ゲーム入力、タイマー設定は変更しない。

配布済みファイルと常駐プロセスへのロード、Twitch公開表示の反映は別々に確認する。
反映のためにゲームを強制切替しない。次の自然な切替でタイトルとカテゴリを照合する。
手動タイトルや日次タイトル更新を恒久的に禁止する変更ではなく、次のゲーム切替で
上記の固定タイトルに更新される。

## Record-aware title context

The switch hook can include bounded numeric evidence: Soren's durable best score,
or Hanjuku's recorded cleared-chapter count. A cleared chapter and the next
chapter goal are explicitly different. Current score, chapter-in-progress,
unadopted strategy proposals, private handoff text and generated prose are not
read as title claims. Missing, malformed, oversized or symlink evidence retains
the neutral fallback. An identity-matched ready generation selects one of three
reviewed wordings, stable for retries within that generation. Other game state
cannot select that wording. This is record-based variety, not proof of strategy
adoption or improved game performance. Strategy-feature metadata is still a
separate extension.
