# コメント返し eval 基盤（offline）

issue #1308「コメント返し・Jev・画面文脈の評価セットと自動改善ループを共通基盤化する」
の PR-1 / PR-2 スライス。**副作用なしの offline runner** を先に作り、信用できる eval を
用意してから hillclimb を載せる、という順序を守る。

- 実装: `src/docich/eval/`
- public fixture: `evals/comment/v1/`（`manifest.json` / `public_cases.jsonl` /
  `critical_cases.jsonl` / `rubric.json`）
- テスト: `tests/test_eval_*.py`

## できること

| コマンド | 内容 |
| --- | --- |
| `python -m docich.eval build --suite evals/comment/v1` | corpus を検証し、split の counts/hash を出力（sealed の case id は既定で非表示） |
| `python -m docich.eval run --suite ... --candidate heuristic --split validation` | 1 candidate を 1 split で実行し report を出力 |
| `python -m docich.eval compare --suite ... --base-jsonl A --candidate-jsonl B` | 2 つの JSONL 出力を paired 比較 |
| `python -m docich.eval report --campaign runs/campaign.jsonl` | campaign の keep/revert 集計 |
| `python -m docich.eval hillclimb --suite ... --target comment-prompt --rounds 8 --dry-run` | allowlist target のみを触る loop（既定 dry-run） |

candidate は「provider を呼ばない」ものを注入する。CLI では keyless な local
heuristic baseline（`candidates.heuristic_candidate`）か、外部で生成した出力を読む
`jsonl:PATH`、テスト用の `echo:` のみ。runner 自身はネットワーク client を持たない。

## 3層 split の契約（issue §1）

- split は case 単位ではなく **`group_id` 単位**で決める（同一 thread / batch / ほぼ同文を
  split をまたがせない）。seed と split manifest hash を固定して再現する。
- `train` 60% / `validation` 20% / `sealed_test` 20%。件数が少ない初期は比率より
  group 境界を優先する（`evals/comment/v1` は 17/6/5）。
- `critical_cases.jsonl` は比率に混ぜない **常時 hard-gate fixture** の独立 bucket。
- `sealed_test` は `runner.run_cases(..., allow_sealed=True)` を明示しない限り拒否される
  （`ContractError: sealed_test_locked`）。campaign 終了時の `sealed_evaluate` だけが見る。

## corpus の保存方針（issue §2）

- `evals/` には合成 fixture・公開可能な短文だけ。`contracts.assert_public_safe` が
  token / bearer / private path / private IP を検出した fixture を load 時に拒否する。
- production 由来の case は `$DOCICH_STATE_DIR/eval/corpora/<suite>/cases.jsonl` に置き、
  `corpus.load_private_cases` が salt 付きで username を不可逆 token 化し、URL / path /
  secret を `[url]` / `[private_path]` などへ projection してから runner に渡す。
  画像は保存しない（`image_attached` フラグのみ）。
- 通常ログから Git へ corpus を自動搬出する経路は無い。

## grader 版管理（issue §4）

`contracts.GRADER_VERSIONS` が正本。campaign の experiment record には
`rubric_version` / `grader_versions` / `split_manifest_hash` を必ず記録する。

- `graders.deterministic`（`deterministic-v1`）: empty / generation failure / provider error
  漏れ / screen 未添付なのに「見た」断定 / required content 欠落 / forbidden content /
  unsupported label / parse failure / timeout / side effect / capture の取り違え。
  **hard fail は semantic 平均点で相殺しない**（report でも別枠）。
- `graders.classifier`（`classifier-v1`）: macro F1・per-label precision/recall・
  game-intent recall（#1243）・screen_need の required precision/recall と
  critical false negative/positive（#1233）・abstain 率。
- `graders.reply_semantic`（`reply-semantic-v1`）: 6 軸（intent_relevance /
  game_grounding / answer_usefulness / evidence_discipline / conversation_quality /
  factual_consistency）。judge は注入式（blind、candidate 名・比較順を見せない）。
  `reliability_ok` が安定性 gate で、通らない campaign は開始しない。

## campaign / keep-revert（issue §5–§7）

- mutation は allowlist enum のみ（`comment-prompt` / `comment-reply-contract` /
  `jev-rubric-text` / `model-profile` / `screen-threshold`）。任意 path は拒否。
- **1 round = 1 hypothesis / 1 mutation**。generator が受け取るのは train case だけ。
- leakage checker（`leakage.check`）が train case 本文の丸写し・case/group id の混入・
  n-gram 過一致を検知したら、その round は評価前に revert する。
- validation は aggregate のみを改善側へ返す（case 本文・failure transcript は返さない）。
- 採否は hard gate → primary quality（paired bootstrap）→ cost/latency の順。
  margin は結果を見る前に固定する（`campaign.decide(margin=...)`）。
- experiment record は JSONL（`campaign.append_experiment`）。case 本文は複製しない。

## 安全境界（この時点でやらない）

- main への自動 push / merge、本番 worker の prompt/model 変更、本番コメントへの二重返信。
- hillclimber による任意 file/path 編集、deterministic guard / auth / delivery / ack /
  rate-limit 契約の mutation。
- holdout / sealed 本文を candidate generator へ渡すこと。
- production screenshot の無条件 dataset 保存、生 username / secret / token / private path
  の corpus 保存。

## 未実装（後続スライス）

- PR-3: 本番 comment prompt/generator の offline 実行と native semantic eval fixture。
- PR-4: 実 hillclimber（allowlist prompt の自動生成）と leakage の n-gram 警告出力。
- PR-5: campaign final の sealed-test 運用と CI/manual workflow（public fixture のみ CI）。
- #1263 の Jev provider/model 比較を同じ corpus/runner/metrics に載せる。
