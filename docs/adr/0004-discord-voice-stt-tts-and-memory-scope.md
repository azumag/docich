# ADR 0004: Discord 音声会話 Bot の STT/TTS 選定と記憶スコープ

- Status: Accepted（STT/TTS と記憶スコープの方針は確定。実 Voice Channel の credentialed 受入は未実施）
- 関連 Issue: [azumag/docich#1628](https://github.com/azumag/docich/issues/1628)
- 関連実装: `runtimes/discord-voice/`（offline coordinator + Windows live host）、
  `workers/discord-chat/`（canonical persona / Workers AI / SQLite memory / `/voice/reply`・`/voice/commit`）
- 先行スライス: #1731（offline coordinator）、#1744（join/playback）、#1753（1人受信 + STT）、
  #1757（会話 core 接続）、#1763（VOICEVOX 再生 + 成功後 memory 確定）、#1796（受入ランナー）

## 0. 前提と調査方法

- 比較は**公開情報（各社の料金表・モデルカード・公式ドキュメント）と、第三者の日本語ベンチマーク**に基づく。
  2026-10-08 時点の公開値であり、**実 API の呼び出し・請求額の照合は行っていない**。料金は改定されうる。
- 実 VC の音質・体感レイテンシ・reconnect 耐久は本 ADR の対象外（§6 の未受入）。
- 既存の実装（先行スライスで main にマージ済み）が既に採用している候補を、後追いで比較・確定する。

## 1. 決定（要約）

1. **STT は Cloudflare Workers AI `@cf/openai/whisper-large-v3-turbo`** を使う（スライス2で実装済み）。
2. **TTS は VOICEVOX ENGINE** を使う（スライス4で実装済み）。Cloudflare 側の TTS は日本語の要件を満たさないため採らない。
3. **記憶スコープは `(guild, channel, user)` の完全一致**とする。既定は **Voice Channel 単位で分離**（テキスト版とは別スコープ）。
   同一Guild内でテキスト版と記憶を共有したい配備だけが、`DOCICH_DISCORD_VOICE_MEMORY_CHANNEL_ID` に
   ペアのテキスト Channel ID を明示して **opt-in** する。**別 Guild 間の完全分離は常に維持する。**
4. 会話ターンは**Discord への再生成功 ACK の後だけ**記憶へ確定する（barge-in / TTS失敗 / 再生失敗 / 停止では保存しない）。
   これは先行スライスで実装済みの挙動で、本 ADR でもそのまま維持する。

## 2. STT 候補の比較

比較項目は Issue #1628 の指定（日本語精度・レイテンシ・streaming・料金・partial transcript）に合わせた。
日本語精度は**ベンチマークごとにコーパスが違う**ため、単一の数値ではなく出典付きで併記する。

| 候補 | 日本語精度（出典付き） | レイテンシ / streaming | partial | 料金（公開値） | 判定 |
| --- | --- | --- | --- | --- | --- |
| **Cloudflare Workers AI `@cf/openai/whisper-large-v3-turbo`**（採用） | FLEURS CER 5.8%（mlx M3）[S1]、5.30%（別実装）[S4]、自然会話 WER 0.218 / CER 0.184（RTX5090・10分）[S3] | バッチ REST。第三者計測 RTF 0.013（約77倍速、RTX5090）[S3]、Apple M 系で約2.6倍速 [S4] | なし（1発話完了後の一括応答） | **$0.000513 / 音声分**（$0.0005/分・46.63 neurons/分）[S1][S5] | **採用** |
| Cloudflare Workers AI `@cf/deepgram/nova-3` | Deepgram Nova-3 Multilingual の日本語対応。但し**日本語の数値整形(numerals)非対応**という既知制約（2026-05）[S7] | Deepgram 側は streaming < 300ms。Workers AI 経由はバッチ [S6][S7] | 直接 API では可 | Deepgram 直接: multilingual streaming $0.0058〜0.0092/分 [S6][S7] | 次点 |
| OpenAI `gpt-transcribe` / `gpt-4o-transcribe` / `gpt-4o-mini-transcribe` / `whisper-1` | 汎用（日本語個別の公開 CER は本調査で未取得） | バッチ中心。`gpt-live-transcribe` は streaming [S8] | streaming モデルのみ | $0.0045 / $0.006 / $0.003 / $0.006（音声分）[S8] | 次点 |
| Deepgram Nova-3 直接 | Nova-3 は 50+ 言語・日本語含む。数値整形の制約は同上 [S7] | streaming < 300ms [S6] | 可 | streaming list $0.0077/分（promo $0.0043〜0.0048）[S6] | 次点 |
| ローカル推論（faster-whisper large-v3-turbo / ReazonSpeech-ESPnet-v2 / Qwen3-ASR-1.7B） | Qwen3-ASR 1.7B が本ベンチで最良（WER 0.185 / CER 0.140）、whisper が次点 [S3]。ReazonSpeech は日本語ドメイン適応に強い [S3] | ホスト依存（parakeet-tdt-0.6b-v3 は RTF 0.002 だが日本語 WER 0.344）[S3] | 実装次第 | **API 課金 0**、ただしホスト CPU/GPU・モデル配布・運用手間を負担 [S2][S3] | 保留 |

**採用理由**

- 会話 core（canonical persona / Workers AI / SQLite Durable Object memory）が既に Cloudflare 上にあり、
  STT を同じアカウント・同じシークレット境界に置くことで、**外部 STT 事業者への音声送信先を新設しない**（§Privacy）。
- 料金が他候補より**1桁安い**（$0.000513/分 対 $0.003〜0.0092/分）。1発話10秒上限の本設計では差は小さいが、
  長時間接続の受入（30分）で効いてくる。
- partial transcript は無いが、初期仕様が「発話終了を VAD で検出して 1 utterance として処理」であり、
  streaming 部分認識を要件にしていない。partial が必要になった時点で Nova-3 / `gpt-live-transcribe` を再評価する。
- 既知の弱点は公式ドキュメント・第三者ベンチマークとも**幻覚（無音区間に文字を生成する）**。
  実装では provider 側 VAD（`vad_filter: true`）と、10秒上限・無音 400ms での発話確定で影響を抑えている。
  実 VC 受入で誤認識パターンを確認する（§6）。

**実装上の残課題（本 ADR の決定事項ではないが記録）**

- 現実装は WAV を `Array.from(wav)` の **JSON 数値配列**として送る（1発話10秒で約 96万要素 ≈ 3〜4MB の本文）。
  アップロード帯域とレイテンシの面で不利なので、実 VC 受入の前後で **base64 かバイナリ送信**へ改善する余地がある。
  現行の 16KiB 本文上限（`VOICE_MAX_BODY_BYTES`）は `/voice/reply`・`/voice/commit` 用であり STT には適用されない。

## 3. TTS 候補の比較

比較項目は Issue #1628 の指定（日本語品質・レイテンシ・streaming 再生・キャラクター性・barge-in 時の停止しやすさ）に合わせた。

| 候補 | 日本語品質 / キャラクター性 | レイテンシ / streaming | barge-in 停止 | 料金・ライセンス | 判定 |
| --- | --- | --- | --- | --- | --- |
| **VOICEVOX ENGINE**（採用） | 日本語ネイティブ。キャラクター音声ライブラリが多数 [S9][S10] | ローカル HTTP。`/streaming_synthesis` も提供。実測値は受入で確認（§6） | HTTP 中断で待機は打ち切れるが `/synthesis` は**切断後も計算継続**。`/cancellable_synthesis` は `--enable_cancellable_synthesis` が必要な実験機能 [S10] | **商用・非商用とも無料**。ENGINE は LGPLv3（別ライセンスも可）、音声は各ライブラリ規約 + クレジット表記 [S9][S10] | **採用** |
| Cloudflare Workers AI `@cf/deepgram/aura-1` | 話者は英語音声（angus/asteria/…）で日本語なし [S11] | バッチ / リアルタイム [S11] | — | $0.015 / 1k chars [S11] | 不採用 |
| Cloudflare Workers AI `@cf/deepgram/aura-2-en` | 英語のみ [S11] | 同上 | — | 同上 | 不採用 |
| Cloudflare Workers AI `@cf/myshell-ai/melotts` | 多言語（MyShell MeloTTS）。**日本語品質は本調査で未検証** [S11] | バッチ | — | 公開値は要確認 | 保留 |
| Deepgram Aura-2（直接） | 7言語（日本語含む）、英語は 40+ 話者 [S6] | ~90ms TTFB 級という比較記事あり [S6] | — | $0.030 / 1k chars [S6] | 次点 |
| Google Cloud TTS | `ja-JP` の Neural2 / WaveNet あり [S12] | streaming 提供 | — | Neural2 $16 / 1M chars、WaveNet $4 / 1M chars、Chirp3 HD $30 / 1M chars（無料枠あり）[S13] | 次点 |
| OpenAI `gpt-4o-mini-tts` | 多言語だが「音声は英語最適化」と明記 [S14] | streaming あり、TTFA 中央値 596ms（第三者計測）[S15] | — | $0.60 / 1M 入力 tokens + $12 / 1M 音声出力 tokens [S14] | 次点 |
| ElevenLabs | 日本語対応。TTFA 中央値 183ms（第三者計測）[S15] | streaming あり | — | 約 $5 / 音声時間（第三者比較、v3）[S16] と高価 | 不採用（費用） |

**採用理由**

- 追加課金が無く、**日本語品質とキャラクター性**が要件に直結する。
- docich 本体（`src/docich/speech.py`）と Soren の既存 TTS 経路が既に同じ VOICEVOX の
  `audio_query` → `synthesis` 契約を使っており、**運用・辞書・スタイル選択の知見を再利用できる**。
- Cloudflare 側 TTS は日本語を提供しない（aura-1/aura-2-en は英語話者のみ）。将来 melotts の日本語品質が
  確認できれば、Cloudflare 内完結という利点から再評価する価値がある（§6 の残件に含める）。
- barge-in は「待機を打ち切って再生を止める」ことで満たしており、エンジン側の計算打ち切りまでは要求していない。
  長時間発話で計算資源を無駄にしたくない場合は `/cancellable_synthesis` の有効化を検討する（§6）。

## 4. 記憶共有方針の決定

Issue が挙げた 3 候補を、実装済みの `workers/discord-chat/src/memory.js` の仕組みに照らして評価した。
memory は `(guild_id, channel_id, author_id)` の完全一致スコープで直近履歴（`RECENT_LIMIT=6`）と
語句一致の長期想起（`RECALL_LIMIT=4`）を行い、`state='sent'` の行だけを対象にする。

| 候補 | 内容 | 評価 |
| --- | --- | --- |
| 1. 同一 Guild 内で text / voice 共通 | 1 つのスコープを両方で使う | **配備ごとの opt-in として採用**（既定は無効） |
| 2. Voice Channel 単位で分離 | Voice Channel を独立スコープにする | **既定として採用**（現行実装の挙動） |
| 3. user 単位の長期記憶 + channel 単位の短期会話 | 長期は guild+user、短期は channel | 今回は**不採用**（長期/短期の分離が必要になったら再検討） |

**決定と理由**

- **既定は候補2**。Voice Channel はテキスト Channel と ID が異なるため、既定では voice の記憶は
  text の記憶と混ざらない。「Bot が参加していない Voice Channel の音声を処理しない」という
  セキュリティ要件とも整合し、**共有は事故ではなく明示操作でだけ起きる**。
- **候補1 は opt-in**。`DOCICH_DISCORD_VOICE_MEMORY_CHANNEL_ID` に同一 Guild のペアとなるテキスト Channel ID を
  設定した配備だけが、text/voice で同じスコープを読み書きする。未設定なら Voice Channel ID にフォールバックする。
- **候補3 は保留**。「user 単位の長期記憶」を channel 横断で引くには recall クエリを guild+author へ広げる必要があり、
  他人のチャンネル横断履歴を混ぜるリスクと削除セマンティクスの再設計を伴う。必要性が実受入で確認できてから扱う。
- **別 Guild 間は常に完全分離**。スコープに `guild_id` が必須で、`/voice/reply`・`/voice/commit` も
  リクエストの `guildId` をそのままスコープに使う。Guild をまたぐ共有経路は用意しない。
- **確定タイミング**。生成されただけの返答は保存せず、Discord 再生成功の ACK（`/voice/commit`）でのみ
  `state='sent'` の行を作る。barge-in / TTS 失敗 / 再生失敗 / 停止では保存しない（記憶に残らない返答を作らない）。
- **スコープは認可ではない**。`scope` はローカル検証用の文脈で、越権を防ぐのは
  `/voice/reply`・`/voice/commit` の Bearer シークレットと、Bot が購読した 1 ユーザー・1 Guild/Channel の制限による。

## 5. 本 ADR で確定した実装差分

- `runtimes/discord-voice/live-support.mjs`: `DOCICH_DISCORD_VOICE_MEMORY_CHANNEL_ID` を読み、
  `memoryChannelId`（既定 = 参加中 Voice Channel ID）として公開。未設定・空文字はフォールバック、不正な Snowflake は `invalid_config`。
- `runtimes/discord-voice/live.mjs`: 会話生成と memory commit には `memoryScope`（`channelId = config.memoryChannelId`）を渡し、
  TTS には参加中 Voice Channel の `scope` を渡す。つまり**記憶の共有先だけを差し替え、TTS/再生のスコープは変えない**。
- テスト: `test/live-support.test.mjs`（既定・上書き・空文字・不正値）、`live-runtime.test.mjs`（generate/commit が
  memory scope、TTS が Voice scope を受け取ることを確認）。
- `workers/discord-chat` 側の変更は無し（既存の `/voice/reply`・`/voice/commit` が受け取ったスコープを使うため）。

## 6. 未受入・残件

- **credentialed な実 Voice Channel 受入は未実施**。必要: Discord Bot Token、対象 Guild/Voice Channel、対象 User、
  Workers AI の Account ID/Token、Cloudflare Worker の `DISCORD_VOICE_INTERNAL_TOKEN`、同一ホストの VOICEVOX Engine。
  手順は `runtimes/discord-voice/README.md` の `acceptance-windows.ps1` を正本とする。
  Issue #1628 の Acceptance Criteria のうち、日本語1往復・barge-in 実測・reconnect 後の会話・30分連続受入がこれに当たる。
- VAD は RMS 閾値（本番 VAD 未検証）、wake word（「DoCiAI」「同志」等）は未選定、同意 UX は未実装。
- 多人数同時発話の分離、ホスト/サービスの常駐化、VOICEVOX の retry/failover は未実装。
- STT リクエスト本文（JSON 数値配列 → base64/バイナリ）と、VOICEVOX `/cancellable_synthesis` の要否は上記の通り再検討対象。
- `@cf/myshell-ai/melotts` の日本語品質確認（Cloudflare 内で TTS まで完結できるかの判断材料）。

## 7. 出典

- [S1] Cloudflare AI docs: `whisper-large-v3-turbo`（$0.000513 / audio minute）
  <https://developers.cloudflare.com/workers-ai/models/whisper-large-v3-turbo/>
- [S2] Cloudflare Workers AI チュートリアル（Whisper + chunking）
  <https://developers.cloudflare.com/workers-ai/guides/tutorials/build-a-workers-ai-whisper-with-chunking/>
- [S3] 日本語 ASR 9 モデル比較（2026-02、RTX5090、自然会話 10 分）
  <https://neosophie.com/en/blog/20260226-japanese-asr-benchmark>
- [S4] Whisper large-v3 / turbo / small の 12 言語 FLEURS 比較（Apple M3, mlx-whisper）
  <https://vocova.app/blog/ai-transcription-accuracy-benchmark-2026>
- [S5] Cloudflare Workers AI Pricing（audio モデル料金・neurons）
  <https://developers.cloudflare.com/workers-ai/platform/pricing/>
- [S6] Deepgram（Nova-3 / Aura-2）料金・レイテンシの第三者レビュー（2026）
  <https://knowara.com/ai-tools/voice/deepgram-aura-review>
- [S7] Deepgram Nova-3 の料金・多言語/日本語の数値整形制約の整理（2026）
  <https://telenow.ai/blog/deepgram-speech-to-text-models-benchmarks-pricing-and-when-to-use-it/>
- [S8] OpenAI API Pricing（transcription モデル）
  <https://developers.openai.com/api/docs/pricing>
- [S9] VOICEVOX 公式（無料・商用可、キャラクター規約）
  <https://voicevox.hiroshiba.jp/>
- [S10] VOICEVOX ENGINE（HTTP API、`/streaming_synthesis`・`/cancellable_synthesis`、ライセンス）
  <https://github.com/VOICEVOX/voicevox_engine>
- [S11] Cloudflare Workers AI Models（aura-1 / aura-2-en / melotts と単価）
  <https://developers.cloudflare.com/workers-ai/models/aura-1/>
- [S12] Google Cloud Text-to-Speech の対応音声（`ja-JP` Neural2 / WaveNet を含む）
  <https://docs.cloud.google.com/text-to-speech/docs/list-voices-and-types>
- [S13] Google Cloud Text-to-Speech Pricing（Chirp3 HD / Neural2 / WaveNet）
  <https://cloud.google.com/text-to-speech/pricing>
- [S14] OpenAI gpt-4o-mini-tts（料金と「音声は英語最適化」の注記）
  <https://platform.openai.com/docs/models/gpt-4o-mini-tts>
- [S15] ElevenLabs / OpenAI の TTS TTFA・WER 独立計測（Coval）
  <https://openbenchmarks.com/text-to-speech-benchmark-by-coval/elevenlabs-vs-openai>
- [S16] Soniox の日本語 TTS 料金比較（ElevenLabs v3 約 $5/時間）
  <https://soniox.com/text-to-speech/japanese>
