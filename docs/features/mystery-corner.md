# AI探偵ミステリーコーナー（参加型マーダーミステリー、docich#684）

視聴者参加型の1話完結推理コーナー。2026-10-18 オーナー承認（案A マーダーミステリー MVP /
アンケート＋予想 / 30分 / 予想あり＋コミットメント表示）にもとづく。

## 1. 公平性の中核: 決定論ケースエンジン

事件の答え（犯人・手法・動機・手がかりの配置）は `src/docich/mystery_case.py` が所有する。

- コーナー開始時に 64bit の seed を1つ生成（`secrets.randbits`）し、以後の全事象を
  `random.Random(seed)` で決定論的に導出する。同じ seed は常に同じ事件になる。
- 視聴者へは開示まで **コミットメント**（`sha256("docich-mystery-v1:<version>:<seed>")`）
  だけを見せ、配信ボードに常時表示する。配信者側でも途中で答えを変えられない。
- 終了時に seed を開示する。誰でも公開コードで同じ seed から事件を再計算し、
  開示された答えと照合できる（`verify_reveal`）。
- LLM の出力はゲーム状態・答えに**一切ならない**（#380 が確立した信頼側エンジン哲学と同じ）。
  ナレーションは検証済みの事実の再話のみ。

## 2. 進行（1 slot = 1事件、既定30分）

| フェーズ | 内容 | 参加手段 |
|---|---|---|
| intro | 事件ボード表示・コミットメント表示・紹介読み上げ | — |
| investigate | 「次に調べる場所は？」を Twitch アンケートで投票（既定120秒） | アンケート |
| interrogate | 「次に尋問するのは？」をアンケートで投票 | アンケート |
| deduction | 推理タイム | — |
| prediction | 「AI探偵ミステリー：犯人は誰？」をチャンネルポイント予想で受付（既定300秒） | 予想 |
| reveal | 犯人・手法・動機の開示＋ seed / commit 開示。予想を精算または返金 | 予想の精算 |

- 予想の精算は半熟英雄予想と同じ 2026-10-15 オーナー方針（#933）を踏襲する:
  投票の入った選択肢が1つ以下なら **CANCELED（全額返金）**、成立していれば犯人の
  選択肢を winning outcome にして RESOLVED する。勝敗の読み上げは実際の票
  （channel_points の最多選択肢）で判定する。
- **予想が作られた後は必ず精算または返金する**（`_ensure_prediction_settled`）。
  精算に失敗した場合は state に `prediction-settle-failed` を残す（LOCKED のまま
  焦点を失わない。リトライ上限は5回）。
- Twitch API を使えない場合（`TWITCH_POLLS_ENABLED` / `TWITCH_PREDICTIONS_ENABLED` 未設定・
  通信失敗・投票0件）は **seeded なAI探偵が代わりに進行する展示モード**。視聴者0人でも
  1事件は完走し、開示まで必ず到達する。
- `ends_at`（catalog の `duration_minutes`、1-45分）が唯一のハード上限。各フェーズは
  残り時間から reveal/復帰の reserve（150秒）を引いた予算内で実行し、足りなければ
  短縮（アンケートは最短15秒）または skip する。

## 3. 構成

```text
src/docich/
  mystery_case.py       # 決定論事件エンジン（純粋・seed再現可能・I/Oなし）
  mystery_api.py        # Helixクライアント（アンケート POST/GET /polls、予想は
                        # hanjuku_prediction_api の共有 Helix を再利用）
  mystery_narration.py  # ナレーション（overlay + enqueue_audio_text、定型文フォールバック、
                        # [mystery_corner] narration_agents が非空のときだけLLM）
  mystery_view.py       # 配信HTML事件ボード（loopback 8807、payload読み取り専用）
  mystery_corner.py     # GameSwitch でのライフサイクル + フェーズ状態機械
  adapters/mystery_program.py  # view adapter（960x540 viewport、フルスクリーン例外の対象外）
```

- catalog 行: `{ id = "mystery", adapter = "mystery", game = "mystery-view",
  enabled = true, duration_minutes = 30, audio_enabled = true }`（#684 2026-10-18 承認）。
- ナレーション: `[mystery_corner].narration_agents`（空 = 定型文のみ・課金実行なし）。
  LLM は `RADIO:mystery-narration` ラベルで共有 dispatch され、出力は sanitize 後にのみ
  読み上げられる。reveal フェーズは常に定型文。
- view は `mystery_view.json`（0600・256KiB上限・atomic）だけを読む。書くのは corner のみ。
  runtime generation 束縛（tsuitate_show と同じ規則）で、別世代の配信には出ない。

## 4. 視聴者入力の扱い

- アンケート・予想とも Twitch の公式 Helix API 経由のみ。視聴者の自由テキスト（チャット）は
  参加手段に使わず、既存のコメントパイプラインの管轄のままとする（本コーナーは
  コメントを読まない）。
- 選択肢は事件の容疑者・部屋から生成され、途中で差し替えられない（seed で固定）。
- 票の多い選択肢（同票は case 順の最初）を採用する。0票ならAI探偵の seeded 選択。

## 5. テスト

```sh
PYTHONPATH=src python3 -m pytest -q tests/test_mystery_case.py \
  tests/test_mystery_api.py tests/test_mystery_narration.py \
  tests/test_mystery_view.py tests/test_mystery_corner.py
```

- エンジン: seed 再現・bounds（Helix 2-5選択肢/25字）・公開ボードから答えが漏れない・
  reveal 照合（fail-closed）。
- API: gate（`TWITCH_POLLS_ENABLED` / EXPLORE_MODE / 未設定）・envelope 厳密検査・
  vote 集計の決定性。
- corner: 単独完走→開示→復帰 / アンケート票で分岐 / 0票で solo / 予想の精算と返金 /
  manual stop でも開示 / オペレーター移動で interrupted（`operator-moved-during-mystery`）。

## 6. 運用メモと既知の限界

- 配信ネイティブ全画面例外（天気・予想王のみ）には入れない。本viewは通常のゲーム領域
  （960×540 viewport）に投影する。
- 予想の精算が連続失敗した場合、予想は LOCKED のまま残る。state の
  `prediction-settle-failed` と `prediction_id` で Twitch ダッシュボードから手動精算できる。
  定期再試行は follow-up。
- view ポート 8807 は loopback のみ。ホスト設定スイッチは存在しない。
