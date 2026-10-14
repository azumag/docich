# #829 PR-0 baseline inventory（mockのみ・実API/VM操作なし）

目的: #829（コメント返し・ラジオ生成のdocich完全移管）と #882（JEV direct/Vercel
transportのdocich共通化）の実装開始前に、現行の互換経路（参照実行PoC）の
入出力・rc・state・prompt・parser・provider chain・queue/backoff・delivery・ackを
fixtureとして固定する。新実装はdocichへ直接作る（soviet_now内で整理してから移す
二段階実装を避ける）。本PR-0ではコード実装の移管・実API要求・VM操作・設定変更を
行わない。

## 基準（2026-09-22 JST実測）

- docich main: `da81989a91645cc52cbbf3207135aa8c396a6426`
  （共有checkoutを `55d8dae3` → `da81989a` へfast-forward、未コミット運用ルールは
  stash退避→popで保護、共有branch切替・reset/cleanなし）
- `games/soviet_now` gitlink: `9af382fbbaae9a79ed4d5921a0f6b9cbf3ef8e23`
  （作業worktreeではclean checkoutで実測。共有checkout側のdirtyな
  `codex/retro-challenge-selection` 作業は保持し、本PR-0の根拠に使わない）
- 進行中PR（docich）: #903 / #902 / #531 のみ。#829 / #882 関連の実装PRなし
- 方法: mockのみ。実API要求、VM操作、秘密情報の読取なし。
  JEV既定CIもkeyless mock（`comment-classifier-jev.yml:36`）

## 変更範囲

- `docs/plans/829-pr0-baseline-inventory.md`（本書）: 実測の棚卸し記録
- `tests/fixtures/jev_direct_golden.json`: #882 direct goldenの固定値
- `tests/test_jev_direct_golden.py`: 上記goldenとsoviet_now現物の一致を検証
  （submodule不在時はskip、network/secret不使用）
- `tests/fixtures/broadcast_baseline.json`: comment/radio互換境界の固定値
- `tests/test_broadcast_baseline.py`: docich wrapperの参照先・rc契約・gateの
  後退を検出（shell実行なし、静的読取のみ）
- `.github/workflows/ci.yml`: 新規2ファイルを `feature-regressions` の新規step
  `Broadcast baseline contract` へ登録

守る仕様:

- `docich chat` / `docich radio` / `docich ai` は引き続き参照実行wrapper。
  本PR-0でnative pipelineを作らない（§14順序: baseline → PR-1 LLM dispatch →
  PR-2 semantic core+#882 → PR-3 comment core）
- soviet_nowへ新しいprovider transport・semantic基盤を追加しない（#882 §移行順）
- `games/soviet_now` submoduleへの書込みなし（読取のみ）
- 二重送信・二重再生・本番state更新なし（shadowですらない、fixture固定のみ）

## 1. comment pipeline（soviet_now `broadcast/comment.sh` 4201行）

docich wrapper: `src/docich/chat.py`（308行）。実装を複製せず固定の参照実行。

- allowlist `chat.py:27-31`: source={twitch,youtube}、fn=generate_comment_response
- wrapper template `36-47`: `source ./eloop_lib.sh; "$@"`（本番と同順序）
- `build_comment_invocation:111-139`: rc契約 `0=成功/1=生成失敗/2=引数エラー`
- env `89-101`: `OUTBOUND_CHAT_QUEUE_DIR=<tmp>/outbound` へredirect（誤送信防止）、
  `SAY_CONTEXT_LABEL=docich`、`DOCICH_CC_ENABLED=0`
- 実実行は `DOCICH_ALLOW_REAL_COMMENT=1` 必須（`run_comment:221-230`）
- stdoutは返答本文に使わない（stderr=detail）

stage map（`games/soviet_now/` 以下、行番号はgitlink `9af382f` 実測）:

| stage | 位置 | 摘要 |
|---|---|---|
| intake | `comment.sh:3242-3285` / `twitch_chat.sh:319,466-488,555-586` | source別fetch、URL strip、pending上限10、NFKC、`message_id` strict |
| dedup/pending/identity | `comment.sh:46-81,469-603,3347-3358,3591,3666` / `config.sh:915-916` | hash/key、行TTL1800/500件、batch inflight/processed、`viewer_meta.jsonl` |
| notification保護 | `comment.sh:2059,3557-3568,4094-4134` / `comment_runtime_policy.sh:34,47,131,208-267` / `comment_lib.sh:254-323` | category gate＋card debounce（3s/6s/上限12s）＋address repair＋playback dedup |
| 分類 | `comment.sh:2332-2541` / `comment_classifier_jev.sh:13-28` / `lib/comment_classifier_jev.py` | heuristic既定、JEVは `COMMENT_CLASSIFIER_BACKEND=jev` opt-in、旧AI chainは `AI_ENABLED=1` 時のみ |
| context builders | `comment.sh:627,748,853,1023-1488,2576,2665` | 下表の game依存/非依存に従う。`3431-3439,3647-3654` でsanitize |
| prompt | `comment.sh:2665-2694,3710-3732,4176` + `prompts/comment_response_*.md` | category別template envsubst、raid facts追加、返答contract付与、retry addendum `3773-3788` |
| 生成 | `comment.sh:3739-3794` via `ai_generate_list "COMMENT"` | retry上限既定1（`config.sh:311`）、timeout既定90s、background subshell `3594-4164` |
| guard/検証 | `comment.sh:3042-3230,3807-3891` | reasoning/worknote strip、日英guard、候補validator、SING抽出/除去 `2697,2780,3827-3833` |
| 翻訳 | `comment.sh:2429-2522,3908-3922` | `is_english`対象のみ、最大2試行、timeout既定60s（`config.sh:149`）、`lib/comment_bilingual.py` merge |
| delivery | `comment.sh:3959-4015,4148` / `comment_lib.sh:139-254` | `COMMENT_QUEUE_DIR/comment_*.txt` 原子claim、overlay chat通知。再生は別process（bilingual/say/captions） |
| ack/memory | `comment.sh:228-253,652-681,3964,3983,4034-4036` | 再生成功時のみviewer-memory commit。loop回避のremember＋ack-batch＋processed記録 |

## 2. radio pipeline（`broadcast/radio_engine.sh` 1911行）

docich wrapper: `src/docich/cli.py:254-265,381-382` → `chat.cli_radio/build_radio_invocation`
（`chat.py:142-194`）。`radio_engine.sh` の `_radio_generate_and_play` を参照実行。
`DOCICH_ALLOW_REAL_RADIO=1` 必須、OUTBOUNDはprivate tmpへredirect。

| stage | 位置 | 摘要 |
|---|---|---|
| trigger | `radio_engine.sh:1405-1478` / `workers/radio_worker.sh:38,66,269-304` / `radio_corners.sh:5-1827` | done/inflight guard、peak-hour defer、10s poll/300s scheduler、corner別prompt生産者 |
| material | `radio_corners.sh:158-174` / `radio_news.sh:111,502-601` / `config.sh:293,315-326` | `tmp/news.txt` 未読filter＋prompt block＋random pick、grounding cache |
| research/prepass | `radio_engine.sh:344-435,1494-1568` / `lib/ai_prepass_budget.sh:15-256` | Web grounding sanitize、prepass prompt（1200字・箇条書）、合計wall予算既定60s（per-provider timeoutではない） |
| prompt/persona | `radio_persona.sh:6-434` / `radio_corners.sh:36-261` / `prompts/radio_*.md` | 時刻context、main/soren91 persona、output rules、過去topic回避 |
| 生成 | `radio_engine.sh:1575-1605` via `ai_generate_list "RADIO:{corner}"` | soren91/main chain分離、peak reorder、claude fallback無効（`1486`） |
| parser/guard | `radio_engine.sh:666-887,1091-1168,1268-1363,1633-1689` / `lib/radio_parser.py` | `ON_AIR_SCRIPT`必須parse、100字以上・終端約物・provider error混入拒否、dedup/intro/tone |
| fact-check/quality | `radio_quality.sh:10,188` / `radio_factcheck.sh:22-165` / `radio_engine.sh:1725-1844` | 言語/繰返し/garbled/verbatim検査、mixedのみrepair再生成、rollback/strategy/celebration/news/jiji/themeはfact-check skip |
| backup/state | `radio_state.sh:23-322` / `config.sh:224-296,924` | state/corner status、markers、生成meta、spoken history、`backups/radio_scripts/YYYYMMDD/` |
| delivery | `radio_engine.sh:1887-1893` / `radio_state.sh:373-823` | deferred queue（.txt/.voice/.cc_text/.meta）→ render → `--no-preempt` 再生。enqueueのみでworkerが再生 |

NEWS/SPAM二値判定（`radio_news.sh:1-65`）: `SPAM|NEWS` の1単語のみ有効。
`NEWS_SPAM_CHECK_TIMEOUT_SEC=20`、primary local＋fallback opencode。
失敗（rc!=0・queue give-up含む）はPASS（排除しない）。単一20sが全工程の保証ではない。

## 3. provider chain / queue / backoff（`lib/ai_generate.sh` + `ai_generate_policy.sh`）

- dispatch `_ai_dispatch:1449`: `codex:*`→codex、`local:*`→local LLM、
  `opencode:*|opencode-go:*|openrouter:*|vercel:*|amd:*`→opencode、その他codex
- 既定chain `config.sh:54-66,147`: `AI_COMMON_AGENTS`（opencode muse-spark系、
  vercel A+B、amd DeepSeek-V4-Flash、opencode-go）。`COMMENT_AGENTS` は継承
- timeout: COMMENT 90s / RADIO 240s（`ai_generate.sh:1503-1527`）、
  VERCEL fallback 45s（`1529`）、TRANSLATION 60s（`config.sh:149`）、分類旧chain 90/45s
- backoff: 明示429（rc79）→ COMMENT/RADIO 18000s・他600s（`1773-1785`）。
  一般失敗→ `AI_BACKOFF_FAILURE_SEC 300` 基準streak倍率 上限3600（`1987-1999`）。
  policy overlayでfamily breaker（vercel系429≥2で60-300s、`119-131`）
- queue `_ai_generation_queue_run:741`: lane優先 `comment>radio>improve`（`220-236`）、
  base `tmp/state/.ai_generation_locks`、COMMENT既定max-wait 0=無限（`158-189`）、
  RADIO 300s、poll 2s/STALE 900s（`473-476`）、give-up rc92 queue/rc91 gate（`44-47`）。
  improve-gateはRADIO/NEWS/JIJI/CELEBRATIONのみ（COMMENT素通し、`750-756`）

## 4. game依存/非依存の分類（実コード基準）

| stage | 区分 | 理由 |
|---|---|---|
| docich chat/radio/ai wrapper | 非依存 | lib source＋固定fn呼出のみ。game stateを読まない |
| intake/fetch/pending/dedup/identity/normalize | 非依存 | chat pending/hash/message_id/純text処理のみ |
| notification保護・debounce・address repair | 非依存 | chat内容パターンのみ |
| 分類（heuristic/JEV/旧AI） | 非依存 | text-only。game/user/historyをJEVへ送らない（§5） |
| viewer memory/spoken/followup/past topics | 非依存 | 過去返答・memoryのみ |
| game_context / celebration_history | **依存** | `game_state.json`・score/creation historyを読 む（`comment.sh:1027,1423`） |
| ops_context | **依存** | game_switch/lifecycle/game_count/improve stateを読む（`1225`） |
| soren91 note・mode別persona・host mode分岐 | **依存** | 表示中gameでpromptが変わる（`1023,3622-3631`） |
| prompt組立・生成・backoff・queue | 非依存 | provider chain/timeoutにgame入力なし |
| guard/検証/翻訳 | 非依存 | 言語/format＋`is_english`のみ |
| queue書込/再生/say/captions | 非依存 | file/TTS経路（mode sidecarのrouting判断のみ依存） |
| ack/memory commit | 非依存 | pending/file bookkeeping |
| advice routing（抽出/append） | 非依存 | text処理。`main/soren91`振分けはgame-awareだがsoren state不要 |
| radio scheduler/defer/peak-hour | 非依存 | 時刻/queue/費用制御 |
| news/jiji/weather等の一般corner＋spam filter | 非依存 | 外部RSS/一般知識 |
| strategy/rollback/soviet_quiz等のgame corner | **依存** | game_num/score/diff/loreを消費 |
| grounding/prepass memo・persona/output rules | 非依存 | Web要約・host tone/TTS（soren91切替はmode switch） |
| parser/guard/quality/fact-check/repair | 非依存 | text衛生・言語検査 |
| state/marker/deferred queue/history/backup | 非依存 | 共通delivery persistence |
| speech render/play（VOICEVOX/say/captions） | 非依存 | 共通音声（AGENTS.md責務分離） |

受入定義（#829 §2.4、PR-3以降で検証）: game context不要fixtureが
`games/soviet_now` 不在で生成完了すること、traceに `eloop_lib.sh`・
`broadcast/comment.sh`・`broadcast/radio_*.sh`・`lib/ai_generate.sh` の
exec/sourceがないこと、credentialがdocich runtime/configから解決されること。

## 5. #882 direct golden（移行元固定・docich移植時の比較基準）

`lib/comment_classifier_jev.py`（533行）実測。Vercel transportはsoviet_nowへ
実装しない（#882 §移行順）。値は `tests/fixtures/jev_direct_golden.json` に固定。

| 項目 | golden |
|---|---|
| endpoint | `https://api.typesafe.ai/v1/systemone`（固定・TypeSafeのみ） |
| requested model | `jev-1.13.0`（`MODEL_RE=jev-(latest\|preview\|X.Y.Z)`） |
| credential | `TYPESAFE_API_KEY`（child envのみ。argv/payload/stdout/metrics/errorへ出さない） |
| HTTP | stdlib urllib POST、`ProxyHandler({})`＋redirect禁止、killable child（pgkill+reap、wall deadlineはDNS+body含む） |
| 上限 | 8 comments/req、body 4096B・req 32768B・resp 131072B・source 1MiB、log 1MiB×2/日・3日保持 |
| timeout/retry | 1500ms（50..5000 int） / retry 0（単発 `transport`＋単発 `gated_request`） |
| confidence | 0.70未満は行単位heuristic fallback（baseline維持） |
| 送信field | `model`＋`state.comments[{index,text}]`＋固定rubric `questions` のみ。persona/game/user/history/heuristic予測は送らない |
| rubric | 14固定category（card_gacha/raid/subscription/stream_goal/bits/sing_request/game_question/game_status/general_question/strategy_advice/comment_advice/stream_bug_report/chitchat/other）、`RUBRIC_VERSION=comment-body-v1` |
| 検証 | duplicate key拒否・NaN/Infinity拒否、answers==questions、type=choice・allowlist・probabilities key/range/sum・probs[choice]最大、confidence有限0..1、model一致（latest/preview除く）、usage int 0..1e9。全or無（1行不良でbatch全体fallback） |
| HTTP分類 | 401/403 auth・429 rate・529 overload・5xx server・他http |
| fallback | missing key・cooldown/busy/state不可・invalid response・main非0（stdoutなし）→ heuristic。旧長時間AI chainへ進まない |
| 通知保護 | `NOTIFICATIONS={card_gacha,raid,subscription,stream_goal,bits}` は送信skip＋local_notification。modelの通知化は `unconfirmed_notification`（不採用）。`SYSTEM_USERS={wizebot,nightbot,streamelements,streamlabs}` |
| cooldown | auth 300・rate 30・overload 10・server 10・network 5・timeout 5・invalid 10・http 10（s）、`gate.json` flock、429/529はRetry-After 0..300s。共有AI backoffに触らない |
| metrics | schema_version/batch_id/timestamp/rubric_version(status/resolved_model/jev_ms/usage/estimated_usd/rows/...)。raw本文/投稿者/hash/key/生errorを保存しない。costは `jev-1.13.0` のinput $0.042/1M・output無料のみ、他はNone（0円記録しない） |
| env | `COMMENT_CLASSIFIER_BACKEND=jev` がgate（`comment_classifier_jev.sh:14`）。`COMMENT_CLASSIFIER_JEV_*` は `core/config.sh` になく、code既定（MODEL/TIMEOUT_MS/MIN_CONFIDENCE/STATE_DIR/METRICS_DIR/LOG_ENABLED）＋allowlist（`start_all.sh:647-650`、`chat_worker.sh:26-29`） |

既存test: `games/soviet_now/tests/test_comment_classifier_jev.py`（563行、mock-only）、
fixture `fixtures/comment_classifier_jev_eval.jsonl`（16件合成・実視聴者コメント非使用）。
docich `tests/` にJEV testなし。`src/docich/semantic_decision/` は不存在。
`src/docich/ai_generate.py`（267行）は `lib/ai_generate.sh` の参照wrapper（JEV非関連）。

## 6. 検証による完了条件（本PR-0）

- [ ] `tests/test_jev_direct_golden.py`・`tests/test_broadcast_baseline.py` が成功
- [ ] `git diff --check` clean、`compileall -q src tests` 成功
- [ ] CI新規step `Broadcast baseline contract` が成功（必須5jobの対象）
- [ ] 実API・VM・秘密情報に触れていない（fixtureは合成値・固定値のみ）
- [ ] soviet_now submoduleへの書込みなし（`git -C games/soviet_now status` clean）

未確認・残件: 実API canary（direct/Vercel）、native LLM dispatch（PR-1）、
semantic core移植（PR-2）、comment core（PR-3）以降は本PR-0の範囲外。
`common_parts_chat*.md` のowner表更新はdocs/cleanup段階（§14-14）で行い、
本PR-0では現行「soviet_now所有」記述を変えない。
