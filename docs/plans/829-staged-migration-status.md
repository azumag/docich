# #829 段階移行の到達点と互換経路の廃止期限

この文書は epic [#829](https://github.com/azumag/docich/issues/829)
「コメント返し・ラジオ生成を docich 共通基盤へ移管し、soviet_now 依存を最終廃止する」
の**現在地**と、移行中に残す**互換経路の廃止条件・期限**を1か所に固定するもの。
実装順序そのものは #829 §14 を正典とし、本稿はそれを置き換えない。

観測基準: docich `main` = `82b384c`（2026-10-08 JST 取得）。
「済」と書いた項目は、docich の当該ファイルとマージ済み PR を実際に確認したもの。
確認していない本番実効状態（VM 上の実効設定・実API成功率・実測費用）は**未確認**と明記する。

## 1. 移管単位の到達点

| 単位 | 内容 | 状態 | 根拠（docich 側） |
|---|---|---|---|
| C-S1 | LLM dispatch / provider adapter / fallback / queue | **docich native 済** | `src/docich/llm/*` + `src/docich/ai_generate.py`（#941）。`docich ai` は native dispatch。soviet_now の `lib/ai_generate.sh` は runtime 依存ではなくなった |
| C-S2 | output guard | **docich native 済** | `src/docich/model_output_guard.py` + `src/docich/comment/guard.py`（#1042） |
| C-S3 | コメント分類（heuristic / JEV / 正規化） | **docich native 済** | `src/docich/comment_classifier/*`（#942 / #988）。soviet_now main から `lib/comment_classifier_jev.py` は消えている（2026-10-08 に raw 取得が 404） |
| C-S4 | コメント翻訳 | **未着手** | docich に翻訳 stage が無い（legacy は `lib/comment_bilingual.py`） |
| C-S5 | ラジオ prompt/material/research/news/generation/parser/quality/fact-check/state/backup | **部分** | `src/docich/radio/{parser,script,research,consumer}.py`（#1775 / #1819 / #1828 / #1855 / #1860 など）。prompt/persona/material manifest/news 選別/fact-check/quality/state/backup は未移植 |
| C-S6 | comment/radio から見た speech/caption delivery interface | **部分** | `src/docich/captions.py` は docich 正典。comment/radio core から見た generic `SpeechSink` / `CaptionSink` 契約は未定義（`comment/screen_cli.py` は "Delivery remains legacy" と明記） |
| C-S7 | outbound chat queue / sender | **未着手** | soviet_now `lib/outbound_queue.sh` が依然として正典 |
| — | viewer memory / semantics なしの state / prompt / context builder | **docich native 済** | `comment/viewer_memory.py`（#1050）、`comment/state.py`（#1047）、`comment/prompt.py`（#1038）、`comment/contexts.py` + `sorengame_context.py`（#1057） |
| JEV A | コメント文脈の選択（`plan_comment_context`） | **未着手** | `plan_comment_context` / `COMMENT_CONTEXT_ROUTING_MODE` は docich に存在しない |
| JEV B | ラジオ事前調査の要否（`plan_radio_research`） | **未着手** | `radio/research.py` は調査 plan/collect まで。要否の意味判断（`legacy/skip_local/reuse_material`）は未実装 |
| JEV C | 助言の対象・用途判定 | **決定的部分を docich native 化（本 PR）** | 新規 `src/docich/comment/advice_routing.py` + `tests/test_comment_advice_routing.py`。JEV 補助質問は未実装 |
| JEV D | 生成 model profile（`select_comment_profile`） | **未着手** | — |
| JEV E | ニュース素材選別（`news_spam_v1` / 意味的同一出来事） | **未着手** | — |
| JEV F | 意味的品質検査（`reply_quality_v1`） | **未着手** | — |
| — | owner-only control plane（`configure_semantic_routing`） | **未着手** | #829 §12.1。`docs/plans/829-882-semantic-transport-pr3-audit-and-next-gates.md` が「最大の残り」と記録している |

状態の見方:

- **済** = docich が正典で、呼び出し側が docich を参照している。
- **部分** = docich 側の実装は進んでいるが、移行元 shell / compatibility 経路がまだ本番の実行経路にある。
- **未着手** = docich に正典が無く、soviet_now 側の実装が動いている。

注意: `comment/` 配下の native 実装（prompt / guard / state / contexts）は**まだ production の owner ではない**。
`docich chat` / `docich radio`（`src/docich/chat.py` → `cli.py`）は今も `eloop_lib.sh` を source して
`soviet_now` の `generate_comment_response` / `_radio_generate_and_play` を呼ぶ互換経路であり、
#829 §2.4 の受入定義（`games/soviet_now` 無しで fixture を生成できる／runtime trace に
`eloop_lib.sh`・`broadcast/comment.sh`・`broadcast/radio_*.sh`・`lib/ai_generate.sh` の exec/source が無い）
はまだ満たしていない。

### 1.1 §7.0 の決定的 bugfix について（soviet_now 側は既に修正済み）

2026-10-08 に soviet_now main の `broadcast/comment.sh` を取得して確認した限り:

- `_append_strategy_advice_item_at_target` は target を**prefix 除去前に**受け取り、
  表示整形として `_strip_strategy_advice_mode_prefix` を適用する（append 側の再推定は無い）。
- `_detect_strategy_advice_target_mode` の soren91 語彙から `next` / `hold` / `順位` / `相手` は消えている。
- `_append_structured_strategy_advice_at_intake` は intake mode へ投影する。

つまり #829 §7.0 の「先行する小PR」相当は soviet_now 側で成立している。
本 PR はその**意味を docich の正典側へ持ち込む**もので、挙動を soviet_now に追加しない。

## 2. 次の実装順（#829 §14 の残り）

| 順 | スライス | 状態 |
|---|---|---|
| 6 | comment shadow（同一 captured input を legacy/native へ、native は送信しない） | 未着手（3e が前提） |
| 7 | comment active cutover + bounded rollback | 未着手 |
| 8 | radio native core 完備（template/persona/material/news/factcheck/quality/state/backup） | 部分（C-S5 の残り） |
| 9–10 | radio shadow → active cutover | 未着手 |
| 11 | `comment-routes-v1` + A/C/D policy | C の決定的部分のみ本 PR。A/D と contract activation は未着手 |
| 12 | B/E/F | 未着手 |
| 13 | delivery commonization（speech/outbound の compatibility sink 除去） | 未着手（C-S6/C-S7） |
| 14 | docs/cleanup（`common_parts_chat*.md` owner 表、`chat.py` の参照実行説明削除） | 部分（c4 のみ更新済み） |

次に着手する1本は §14 の順序どおり **3e（native comment orchestration + `docich comment` CLI）**:
intake DTO → 分類 → contexts → prompt → `docich.llm` dispatch(+retry) → guard → 翻訳 → delivery 契約 →
ack を1バッチ通し、`games/soviet_now` の無い環境で fixture が完走することを示す。C-S4（翻訳）は
このスライスに含まれる。

## 3. 互換経路の廃止期限

#829 §2.3 は「native pipeline を active にした後、legacy 側を fallback として無期限に残さない。
rollback window を固定し、安定後に旧 broadcast 依存を削除する」と定めている。
下表がその**期限の明記**にあたる。

| # | 互換経路 | 何に依存しているか | 削除 gate | 期限（案） |
|---|---|---|---|---|
| 1 | `src/docich/chat.py` の参照実行 wrapper（`docich chat` / `docich radio`） | `games/soviet_now` checkout、`eloop_lib.sh`、`broadcast/comment.sh`、`broadcast/radio_*.sh` | comment / radio の active cutover 完了 + rollback window 14日経過 | gate + 14日、絶対上限 **2027-03-31** |
| 2 | `bin/docich-comment-classify` を soviet_now が呼ぶ互換 adapter（soviet_now#492 の `DOCICH_SEMANTIC_BACKEND=jev`） | soviet_now 側の呼び出し行 | native classifier の運用安定確認後の adapter 削除 PR | 絶対上限 **2027-03-31** |
| 3 | `tests/test_comment_prompt.py` / `tests/test_comment_viewer_memory*.py` の drift guard（docich コピー == soviet_now コピー） | soviet_now に複製が存在すること | cutover で soviet_now 側の複製を削除する**同じ PR** | cutover と同日（単独期限を置かない） |
| 4 | `src/docich/comment/screen_cli.py`（旧CLI接続。delivery は legacy のまま） | legacy comment generation 経路 | native comment pipeline（3e）完了 + cutover | 絶対上限 **2027-06-30** |
| 5 | comment/radio の delivery・speech・outbound を soviet_now shell（`lib/outbound_queue.sh` 等）へ流す compatibility sink | soviet_now shell | delivery commonization（§14 step 13）完了 | 絶対上限 **2027-06-30** |
| 6 | `docich ai` の `game_name` 互換引数（互換のためだけに残る引数） | 既存呼出側の表記 | 呼出側の表記統一（実害なし・低優先） | 絶対上限 **2027-06-30** |

期限の読み方:

- 各行の**主たる期限は gate 相対**（gate 達成から固定 window 以内）。絶対上限は「gate が遅れても
  これを超えて残さない」ための backstop で、無期限の先送りを許さないためのもの。
- 上限を超えて互換経路が残る場合は、削除の完了ではなく**明示的な owner レビュー**（期限延長の理由と
  新しい日付をこの表に記録する）を必要とする。期限切れを黙って放置しない。
- 日付はすべて**案**であり、オーナー確認をもって確定する。確定までは「未確定」として扱い、
  廃止済みと書かない。
- 各削除は独立した PR とし、`#829` を参照する。表の行は削除 PR のマージ時に「削除済み」へ更新する。

## 4. 本 PR の位置づけ（JEV C の決定的 baseline）

`src/docich/comment/advice_routing.py` は #829 §7 の**決定的な部分**を docich 正典へ置く:

- `[main] / [soren] / [soren91]` prefix を**除去前に**読む（`[soren]` は従来どおり main の別名）。
- `next` / `nextnext` / `hold` / `順位` / `相手`、および内容語（`おじゃま` / `盤面タイプ` / `試合`）だけでは
  target を決めない。明示 target が無ければ受付時の intake mode へ投影し、それも無ければ `unresolved`。
- 否定・引用・比較（`本編ではなくSoren91` のような「否定される側と採用される側の対」は解決する）は
  単語一致で target を確定せず、`unresolved` に倒す。
- JEV の `explicit_target` は prefix と矛盾した場合に**採用しない**（prefix 優先、理由を固定 enum で記録）。
- `both` は #829 §4.5 の `proposal_id` 単位で target ごとの冪等キーへ投影する。

JEV 補助質問（`comment-routes-v1`）、用途の意味判定、保存ファイル I/O、metrics、policy mode は
本 PR に含まない。呼び出し側への結線は §7 の PR-5 スライスで行う。
