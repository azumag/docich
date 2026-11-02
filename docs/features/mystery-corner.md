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

### 1.1 推理可能性（#2757、2026-10-26）

オーナー指摘「推理できる材料が少なすぎる／開示で全容と推理が明かされない」への
構造的な対応。エンジン `VERSION = 2`。

- 事件は「時刻つきの手がかり」と「アリバイ」の突き合わせだけで解ける形に生成する。
  犯人のアリバイ（別室で一人）を否定する手がかりは**必ず別の部屋**に置き、
  無実の容疑者の居場所は証言か時刻つきの手がかりで裏づける（§2 の成立条件）。
- 手がかりは部屋ごとに1件。役割は `contradiction`（犯人を事件時刻の別室に置く）/
  `corroboration`（無実グループの居場所を裏づける）/ `motive`（時刻を持たない動機の証拠）/
  `flavour`。役割と対象容疑者はエンジン内部の真実で、公開ボードには出さない。
- 開示（reveal）は `timeline` / `method_how` / `method_evidence` / `motive_reason` /
  `motive_evidence` / `motive_room` / `contradiction` / `exclusions` を返し、
  `verify_reveal` はこれら全項目を照合する。文面はすべて確定事実から決定論的に組み立てる。
  LLM には渡さない（`_speak(..., allow_llm=False)`）。
## 2. 進行（1 slot = 1事件、既定30分）

| フェーズ | 内容 | 参加手段 |
|---|---|---|
| intro | 事件ボード表示・コミットメント表示・紹介読み上げ（30秒） | — |
| investigate ×2 | 「次に調べる場所は？」を Twitch アンケートで投票（120秒＋表示20秒）。2巡目は調査済みの部屋を除いた選択肢 | アンケート |
| interrogate ×2 | 「次に尋問するのは？」をアンケートで投票。2巡目は尋問済みを除く | アンケート |
| deduction | 残りの部屋・容疑者の資料を一括公開し（`_complete_dossier`）、全手がかりと全アリバイを照合表で提示する推理タイム（90秒） | — |
| prediction | 「AI探偵ミステリー：犯人は誰？」をチャンネルポイント予想で受付（既定300秒） | 予想 |
| reveal | 4部構成の開示（判定／全容／動機／推理、計200秒）＋ seed / commit 開示。予想を精算または返金 | 予想の精算 |

- **予想開始時点で全資料が公開済み**（#2757 A）。アンケートで扱えるのは各2件までだが、
  deduction 冒頭の `_complete_dossier` が残りの全部屋の手がかりと全容疑者のアリバイを
  ボードに載せる。したがって参加0件の展示モードでも、予想を出す時点で材料は揃っている。
- **推理の成立条件（エンジンが構造的に保証）**:
  - 各部屋に手がかりが1件、各容疑者にアリバイが1件。時刻つきの手がかりは
    「その人物が事件時刻にいた部屋」を意味する。
  - 犯人は「別室で一人だった」と証言するが、**別の部屋**の手がかりが事件時刻の
    痕跡（手法の証拠＋犯人固有の痕跡）を残している。これが事件で唯一の矛盾。
  - 無実の容疑者は全員、事件時刻の居場所を「互いの証言」または「時刻つきの手がかり」で
    裏づけられており、申告した部屋と食い違わない。一人だけのアリバイの無実もいるため、
    「一人でいた者＝犯人」という近道は成立しない。
  - `mystery_case.contradicted_suspects` / `solve` が公開情報だけからこの導出を行い、
    seed sweep（`tests/test_mystery_case.py`）で一意性を検証する。
- 手法・動機も独立抽選の飾りではない。手法は現場に残る証拠（`method_evidence`）、
  動機は犯人の部屋から出る証拠（`motive_evidence`）と結びつき、どちらも key clue /
  motive clue として公開される。
- **表示時間の配分根拠**: 30分枠 = intro 30 + 調査 2×140 + 尋問 2×140 + deduction 90 +
  予想 300 + 開示 200 = 1,140秒（19分）。残り約11分は待ち合わせ・復帰の余裕で、
  セッションは `ends_at` から `SESSION_RESERVE_S`（300秒 = 開示200＋復帰）を引いた
  予算内でのみ動く。開示は4部（判定45／全容60／動機40／推理55）に分け、
  960×540の全幅パネル（①判定・②全容・③動機・④推理）を読み切れる速さにする。
  15分未満の短い枠では開示（200秒）と復帰を優先し、アンケートは solo、予想は skip に
  縮退する（表示自体は完走し、開示まで必ず到達する）。
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
  残り時間から reveal/復帰の reserve（300秒）を引いた予算内で実行し、足りなければ
  短縮（アンケートは最短15秒）または skip する。
- エンジン `VERSION` は 2（#2757 で事件の生成規則が変わった）。セッションは開始時の
  version を保持し、配備をまたいだ再開で version が変わっていた場合は、公開済み
  commitment と食い違う答えを作らないために開示せず case-error で終了する。

## 3. 構成

```text
src/docich/
  mystery_case.py       # 決定論事件エンジン（純粋・seed再現可能・I/Oなし）
  mystery_api.py        # Helixクライアント（アンケート POST/GET /polls、予想は
                        # hanjuku_prediction_api の共有 Helix を再利用）
  mystery_participation.py  # trpgコア(#2481)から統合した汎用ロジック:
                        #   冪等投票集計（1人1票・最後の票が有効・同数は最低index）、
                        #   実行台帳（500件/256KiBで古い順に間引き・atomic fsync書込）、
                        #   fail-closed検証。参加者は件数のみ記録（表示名は保存しない）
  mystery_narration.py  # ナレーション（overlay + enqueue_audio_text、定型文フォールバック、
                        # [mystery_corner] narration_agents が非空のときだけLLM）
  mystery_view.py       # 配信HTML事件ボード（loopback 8807、payload読み取り専用）
  mystery_corner.py     # GameSwitch でのライフサイクル + フェーズ状態機械
  adapters/mystery_program.py  # view adapter（全画面 1280×720、未知時は 960×540 viewport）
```

### 3.1 trpgオフラインコアとの統合（#2481 → #2492、2026-10-18 統合判断）

#684 には trpg レーンのオフライン中核（`trpg_corner.py`、PR #2481）が先行 merge されていたが、
2026-10-18 の統合判断により**汎用ロジック（投票・台帳・検証）を本コーナーへ取り込み、
`trpg_corner.py` は削除して一本化**した。引き継いだ契約:

- 投票集計: 1人1票・冪等（再投票は移動）・同数は最低index・0票は None（AI探偵 solo）。
  チャンネルポイント予想の `top_predictors`（選択肢ごとに上位のみ公開）から参加者を集計し、
  「票数による大勢」（`hit_by_users`）を「ポイントによる精算」（Twitchが払う `hit`）と別に記録する。
- 実行台帳: `mystery_history.json`（0600・schema 1・500件/256KiB で古い順に間引き）。
  1事件ごとに seed・commitment・outcome（completed/failed/timeout/aborted）・参加者数・
  投票総数・アンケート回数・予想結果を記録。**視聴者名は保存しない**（件数のみ）。
- 検証: 保存フィールドはすべて有界・fail-closed（trpgコアの検証契約を踏襲）。

引き継がないもの: シナリオファイル方式（事件は seed から決定論生成するため不要）と
チャット `!1`〜`!4` / `!accuse` パーサ（参加経路は Twitch ネイティブのアンケート/予想）。
これらは将来のチャット参加レイヤとして再設計できる
（`docs/features/participatory-trpg-murder-mystery-corner.md` に Superseded バナーで経緯を記録）。

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
  reveal 照合（fail-closed）。#2757 として:
  - `test_every_seed_is_uniquely_deducible_from_the_published_material`:
    seed sweep（400件）で「公開情報だけで犯人が一意」を検証。
  - `test_full_pre_reveal_dossier_still_hides_the_solution`: 全公開後も手法・動機・
    理由文がボードに漏れないことを検証。
  - `test_reveal_explains_the_whole_case` / `test_reveal_parts_explain_the_whole_case…`:
    時系列・方法の実行と証拠・動機の関係・矛盾・除外理由が reveal と読み上げの両方に
    含まれることを検証。
- API: gate（`TWITCH_POLLS_ENABLED` / EXPLORE_MODE / 未設定）・envelope 厳密検査・
  vote 集計の決定性。
- corner: 単独完走→開示→復帰 / アンケート票で分岐（2巡） / 0票で solo / 予想の精算と返金 /
  manual stop でも開示 / オペレーター移動で interrupted（`operator-moved-during-mystery`）/
  `test_prediction_opens_on_a_complete_and_uniquely_deducible_dossier`（予想開始時の
  ボードだけから犯人が導けることを実配信経路で検証）。

## 6. 運用メモと既知の限界

- **readiness 契約（2026-10-18 本番初回発火の修正）**: view server は switch 直後の
  正常状態（corner がまだ payload を書いていない）でも `/api/mystery` に
  `200 + ok:true + case:null（reason=case-unavailable）` を返す。payload 不在を 503 で
  返すと GameSwitch の readiness が必ずタイムアウトし、初回発火が必ず失敗する
  （本番実測: mystery state = interrupted / switch-terminal-before-corner-active /
  started_at=null。state は reconcile_failed_start が正しく受理し、背景の sorengame へ
  復帰済み）。ボードは case が無い間「準備中」表示になる。
- **全画面表示（2026-10-21 オーナー依頼）**: 天気予報・予想王と同じ配信ネイティブの
  情報画面として扱い、`display.width/height`（本番 1280×720）が既知なら
  `(0, 0, width, height)` の全画面に提示する。ビューHTMLは960×540を基準に縦横比維持で
  拡大するため、レイアウトは変えない。`display.width/height` が無い環境では従来の
  `viewport` へ fallback する。コーナー中はステータス枠を隠し（#2494）、終了は既存
  GameSwitch の復帰経路で元画面へ戻す。
- **配信タイトルとカテゴリ**: commit 時は `mystery-view` を明示カテゴリ経路
  （`AI探偵ミステリー` / Just Chatting `509658`）で発表し、復帰時に前のゲームの
  タイトル・カテゴリへ戻す。置き換えられたゲームのメタデータを残さない。
- **ラジオとの混線防止**: コーナー中は Soren の共有フラグ
  `tmp/.program_corner_active`（`ends_at` 付き・ボード更新ごとに延長）を書き、
  Soren のラジオは新規生成と deferred 再生の両方を止める。旧 PAPER フラグ
  `tmp/.paper_corner_active` にも同じ内容を mirror し、旧 Soren ゲートしか配備されて
  いない間も新規生成だけは止まるようにする（共有ゲート配備後に削除できる互換層）。
  Soren 側は共有フラグを第一に、旧 PAPER フラグを後方互換として受理する。
  終了・退避時は所有 token が一致する場合だけフラグを削除する（別コーナーのフラグを
  消さない）。クラッシュ時も `ends_at` で自動復帰する。
- 予想の精算が連続失敗した場合、予想は LOCKED のまま残る。state の
  `prediction-settle-failed` と `prediction_id` で Twitch ダッシュボードから手動精算できる。
  定期再試行は follow-up。
- view ポート 8807 は loopback のみ。ホスト設定スイッチは存在しない。
