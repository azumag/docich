# コメント分類の active-game hint 契約（#1243 PR-1）

## 現在地：PR-1 は分類時の固定語彙 hint まで

短いゲーム発言（「右じゃない?」）を一般雑談に落とさないため、Jev と
heuristic に「現在プレイ中のゲーム」の最小限の文脈を渡す。
渡すのは docich-owned allowlist の固定語彙だけ。**生の game state、
スクリーンショット/パス、スコア、個人情報、prompt/history、
game-owned の任意文字列は渡さない。**

全体の段階計画・回帰ケース・評価方針は
[#1243](https://github.com/azumag/docich/issues/1243) を正本とする。
返答生成への grounding（PR-2）や #1233 画面連携（PR-3）はまだない。
`screen_need=required` と同様、hint は分類の根拠であり観測の証拠ではない。

## opt-in と互換性

| 設定 | 既定 | 意味 |
|---|---|---|
| `COMMENT_ACTIVE_GAME_ENABLED` | `0` | `1` の時だけ hint を付与。`0`/`1` 以外は設定不正 |
| `COMMENT_ACTIVE_GAME_KIND` | — | `sorengame` / `robots` / `nethack` / `hanjuku-hero` のみ。未知は body-only へ縮退 |
| `COMMENT_ACTIVE_GAME_INTERACTION` | `unknown` | `board` / `action` / `menu` / `unknown`。未知値は `unknown` へ縮退 |

無効時・未知時は本文単体の従来分類と同一（heuristic parity 維持）。
hint は非ゲーム分類（`general_question` / `chitchat`）にだけ適用し、
明示的なゲーム分類・通知・バグ報告・advice を上書きしない。
天気・政治（右派/左派）・歌・配信不具合はゲームへ寄せない。

## 実装

- `src/docich/comment_classifier/active_game.py`：allowlist、env 読取、
  hint 検証（`check_hint` は固定キー以外を拒否）、Jev 用固定文、
  heuristic 用 `suggest_category`
- `heuristic.classify(..., game_hint=None)`：既定 `None` で旧出力と同一
- `jev.build_request(..., game_hint=None)`：同一リクエスト内の category 質問へ
  固定文を追加。HTTP 往復の追加は 0、32KiB 上限を維持
- `event['game_hint']` と `game_hint_applied` で body-only / assisted を区別
  （本文は記録しない）

ロールバックは `COMMENT_ACTIVE_GAME_ENABLED=0` に戻す。
