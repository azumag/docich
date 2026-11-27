# docich#5717 調査記録: PR #5642 の CI 未起動とマージ

## 結論

PR #5642 で CI が 0 件だった直接原因は、**リポジトリ側のワークフロー条件では説明できない形で、GitHub 側が `pull_request` イベントおよび main への `push` イベントから workflow run を作成しなかったこと**です。

特に重要な点は次の2つです。

1. PR #5642 の head commit `9ed72e5` は、ワークフロー定義上は必ず `CI` workflow が起動し、集約ジョブ `ci-required` の check が作られる条件を満たしていました。それでも check-run / workflow-run は 0 件でした。
2. main への merge commit `709e09cd` では、`ci.yml` 自体は設計上 push 対象外ですが、Hanjuku 系の push トリガーを持つ複数 workflow の path 条件に合致していました。それでも push event の run が 0 件でした。

したがって、workflow の YAML、paths filter、merge conflict、`[skip ci]` 系の注記、PR 個別の変更内容では説明できません。証拠上は **GitHub のイベント配送 / Actions trigger 処理でイベントが落ちたこと** が最有力です。

`pull_request` 側については、外部事例で「PR open 直後に GitHub が test merge ref (`refs/pull/N/merge`) を準備する前にイベント処理が走り、CI run が一度も attach しない」という PR-open merge-ref race / dropped delivery が報告されています。PR #5642 は open から merge まで 8分20秒で、その間に synchronize も空 commit も close/reopen もなかったため、開幕時にイベントを落とすと自然回復しない形に一致します。近傍 PR も短時間に連続して作成・merge されており、base branch が頻繁に動く状況はこの race を悪化させます。ただし push 側が同じ race なのか、独立した push event の dropped delivery なのかは外部からは確定できません。

2026-11-25 に GitHub Status で公開されていた障害は 15:55-16:52 UTC 頃で、PR #5642 の作成・マージ（20:16-20:24 UTC）とは直接重なっていません。そのため公開障害を根本原因と断定はできません。公開障害の後の残留影響、または個別には公開されない短時間劣化の可能性はありますが、外部から得られる証拠だけでは特定不能です。

もう1つ独立した問題として、**ruleset 上で status check 必須化が有効に見えるにもかかわらず、必須集約 check `ci-required` が存在しないまま merge が成功した**ことが確認されました。これはイベント欠落とは別の保護機構のギャップです。GitHub の required check は通常、check が未作成・pending のままでは merge を止めるため、実際に merge できた以上、少なくとも次のいずれかが起きたことになります。

- ruleset の required check に `ci-required` が正確な context 名で登録されていなかった
- ruleset が当時異なる設定だった
- azumag または使用した merge 経路に bypass 権限があった
- GitHub 側の ruleset 評価にも一時的な不整合があった

匿名で参照できる公開 HTML では required check の具体名と bypass actor が表示されないため、この4択のどれかまでは今回の read-only 調査では確定できません。ただし、いずれにせよ **イベント欠落が起きても merge を止められるはずの防御層が機能しなかった**ことは確実です。

## 確認した事実

### PR #5642

- PR #5642: `feat(hanjuku): 15分実況に戦略判断の焦点と長い物語を入れる`
- 作成: 2026-11-25 20:16:32 UTC
- マージ: 2026-11-25 20:24:52 UTC
- マージ実行者: azumag
- head branch: `feat/hanjuku-periodic-focus`
- head commit: `9ed72e52`（短縮表示 `9ed72e5`）
- merge commit: `709e09cd`
- 変更ファイル:
  - `docs/operations/hanjuku-periodic-commentary.md`
  - `src/docich/hanjuku_periodic.py`
  - `src/docich/hanjuku_periodic_worker.py`
  - `tests/test_hanjuku_periodic_commentary.py`
- issue #5717 に記録済みの API 観測では、head `9ed72e52` の check-runs / workflow-runs は 0 件、merge commit `709e09cd` に紐づく workflow run は issue_comment event のみで、push event は 0 件でした。
- commit `9ed72e5` の parent は `d814ad1` で、これは直前 PR #5640 の merge commit です。つまり PR #5642 は作成時点で最新 main を親に持ち、merge conflict により `pull_request` workflow が起動しない状態だったとは考えにくいです。
- commit message には `[skip ci]` / `[ci skip]` 等の skip 注記はありませんでした。

### workflow 定義との突き合わせ

特に重要な2件について、現行だけでなく merge commit `709e09cd` 時点のファイルも GitHub 上で確認しました。

#### `.github/workflows/ci.yml`（`709e09cd` 時点）

- `pull_request` には paths filter がありません。
- コメントにも、main の ruleset が `ci-required` を要求するため required check が開始しない事態を避ける意図が明記されています。
- そのため PR #5642 では通常なら `CI` workflow が起動し、`test-scope` / `python-syntax` / suite job / `ci-required` が作られるはずでした。
- 一方で `ci.yml` には main への `push` trigger がなく、これは故意の設計です。したがって `CI` の push run がないこと自体は問題ではありません。
- `concurrency.cancel-in-progress` は `pull_request` の場合だけ true であり、push run を打ち消す設定ではありません。

#### `.github/workflows/hanjuku-egg-stock-ci.yml`（`709e09cd` 時点）

- `pull_request` と `push`（`branches: [main]`）の両方があります。
- paths には `src/docich/hanjuku*.py` と `tests/test_hanjuku*.py` が含まれます。
- PR #5642 の変更は両方に合致するため、通常なら PR でも merge commit への push でも起動対象です。

以下2件は現行定義での補助確認です。`hanjuku-egg-stock-ci.yml` 単独でも #5642 の push run 期待は示せますが、同じ path 規則が複数 workflow にあることも併記します。

#### `.github/workflows/hanjuku-predictions-ci.yml`（現行）

- `pull_request` と `push`（`branches: [main]`）の両方があります。
- paths には `src/docich/hanjuku*.py` と `tests/test_hanjuku*.py` が含まれ、PR #5642 の変更に合致します。

#### `.github/workflows/retro-corner-regressions.yml`（現行）

- `pull_request` と `push`（`branches: [main]`）の両方があります。
- paths には `src/docich/hanjuku*.py` と `tests/test_hanjuku*.py` が含まれ、PR #5642 の変更に合致します。

以上から、merge commit `709e09cd` に push run が全くなかったことは、`ci.yml` が push 対象外という設計だけでは説明できません。

### 近傍 PR の状況

公開 HTML の Checks ページで近傍 PR をスポットチェックしたところ、少なくとも以下は同様に `Workflow runs completed with no jobs` / check 一覧なしの状態でした。

- merged: #5625, #5627, #5628, #5629, #5630, #5631, #5633, #5634, #5636, #5637, #5638, #5640, #5642, #5644
- closed unmerged: #5641

一方で以下では通常どおり複数の workflow / job が表示されました。

- #5643
- #5645
- #5646

特に #5645 の `CI` run `45708979611` は、2026-11-25 21:57 UTC に `synchronize` event で起動していました。また issue #5717 には、#5644 の merge commit には 7 件の push run があった一方、#5642 にはなかったと記録されています。

これらは、#5642 固有の YAML / paths / branch の問題ではなく、**一定期間に集中したイベント処理の欠落**を示唆します。少なくとも #5644 では merge commit への push は起動しているため、回復は段階的だった可能性があります。

なお今回は匿名 HTML でのスポットチェックであり、全 PR について API で check-run 数を網羅的に数えたわけではありません。したがって上記は「同様の表示を確認できた範囲」であり、影響範囲の完全な確定ではありません。

### GitHub Status 上の障害

2026-11-25 には Git Operations, Webhooks, API Requests, Issues, Pull Requests, Actions, Packages, Pages などに影響する GitHub 側障害が公開されていましたが、公開タイムライン上は 15:55-16:52 UTC 頃で解消済みです。PR #5642 は 20:16-20:24 UTC のため直接重なりません。

したがって、この公開障害を #5642 の直接原因として断定はできません。ただし、同日に GitHub の Pull Requests / Actions / Webhooks 系で大規模障害があったことは、プラットフォーム側のイベント配送欠落という仮説の傍証にはなります。

### 外部事例

GitHub Actions の event drop / merge-ref race は他リポジトリでも報告されています。

- [OpenClaw issue #110964](https://github.com/openclaw/openclaw/issues/110964) では、CI workflow が head SHA に一度も attach しなかった事象を「known PR-open merge-ref race」と記録し、sweeper で close/reopen して event を再発火する運用が検討されています。
- [microsoft/apm の merge-gate workflow](https://github.com/microsoft/apm/blob/3265ca0b3127350ec79b6b274872b432131d8c92/.github/workflows/merge-gate.yml) コメントでも、rare な dropped `pull_request` webhook delivery と、その回復策（空 commit、close/reopen、手動 dispatch）が記述されています。

これらは docich#5717 の直接証拠ではありませんが、「GitHub 側で pull_request event から run が作られないことが稀に起き、repository 側 YAML だけでは防げない」という既知の故障モードを示します。

## 検討して否定または低評価した仮説

### 1. `ci.yml` の paths filter で起動しなかった

否定します。`ci.yml` の `pull_request` trigger には paths filter がありません。ドキュメント変更だけでも `test-scope` が起動し、`ci-required` が作られる設計です。

### 2. merge conflict だったため `pull_request` workflow が起動しなかった

PR #5642 については低いです。head `9ed72e5` の parent は直前 main `d814ad1` で、最新 main から分岐した1コミットでした。少なくとも #5642 に関しては conflict 仮説の根拠がありません。

### 3. `[skip ci]` 等で workflow が skip された

否定します。commit message に skip 注記はありませんでした。

### 4. `ci.yml` が main push を持たないため merge commit run がなかった

部分的にしか説明しません。`CI` の push run がないことは設計どおりですが、Hanjuku 系 path workflow は main push を持ち、#5642 の変更 paths に合致します。したがって push run 全体が 0 件だった説明にはなりません。

### 5. workflow の concurrency cancel による消失

低いです。`cancel-in-progress` は `pull_request` では true ですが、少なくとも新しい run が作られれば checks ページか API に痕跡が残るはずです。また push では false であり、merge commit の push run 不在は説明しません。

### 6. `GITHUB_TOKEN` 由来イベントの再帰防止で workflow が起動しなかった

完全には排除できませんが、主要因とはしにくいです。GitHub では `GITHUB_TOKEN` による push 等は再帰防止で workflow を起動しませんが、PR 作成者と merge 実行者の表示は azumag であり、通常のユーザー操作と矛盾しません。さらに同時期の複数 PR で同じ欠落が見え、#5643 以降は通常起動しているため、トークン種別だけでは時系列の回復を説明しにくいです。ただし、実際の merge 操作に使われた認証種別は公開 HTML からは確定できないため、認証済み API / audit log での確認余地は残ります。

## なぜ merge できたか

こちらは CI 未起動とは別問題です。

`ci.yml` のコメントは、main の ruleset が集約 check `ci-required` を要求する設計であることを明示しています。実際の ruleset `main` も Active で、公開 HTML 上は pull request 必須・status checks 必須・force push 禁止・deletion 禁止が確認できます。

それにもかかわらず `ci-required` が存在しない PR が merge できた以上、少なくとも当該 merge 経路に対して required check が実効的に enforce されていませんでした。匿名公開情報では required check 名と bypass actor が見えないため、設定ミスなのか、bypass 権限を持つ owner 操作だったのか、ruleset 評価の一時不良だったのかは確定できません。

運用上の重要な含意は、**GitHub Actions のイベント欠落そのものが完全に防げなくても、required check の enforce が正しければ merge は止まるはず**ということです。したがって再発防止は「Actions 未起動の検出」だけでなく、「check 不在では merge できない保護」を別途確認・強化する必要があります。

## 再発防止策の提案

設定変更そのものは行っていません。提案のみです。

### 1. ruleset の required check と bypass を認証済み API / owner 権限で確認する

最優先です。以下を確認してください。

- ruleset `main` の required status checks に `ci-required` が完全一致で登録されているか
- 登録されている context が workflow / job 名の変更後の旧名になっていないか
- bypass actors に owner、admin、GitHub Apps、bot が含まれていないか
- bypass が必要な場合でも、PR merge 操作を行う通常アカウント・自動化アプリが含まれていないか
- ruleset の enforcement が `active` で evaluate や disabled になっていないか
- 可能なら 2026-11-25 時点の audit log / ruleset 変更履歴を確認する

`ci-required` は全 PR で必ず job が作られる設計なので、required check として適切です。しかし未登録または名前不一致なら、check が存在しない PR を止められません。

### 2. check 不在の merge を即時検知する post-merge audit を追加する

現在の日次監査で発見できたとはいえ、検知が翌日まで遅れます。merge 後数分以内に以下を確認する小さな monitor を追加する案があります。

- merged PR の head SHA に `ci-required` check-run があるか
- merge commit に期待される push event run があるか
- check/run が 0 件なら即座に issue 化し、必要なら `workflow_dispatch` による検証 run を起動するか運用者へ通知する

GitHub 側イベント欠落は防げなくても、影響範囲の把握と検証漏れの放置は防げます。

あわせて、merge 前の open PR を対象にした PR CI sweeper も検討できます。open 後 N 分経っても `ci-required` が attach しない PR を検出し、GitHub App token / PAT など event を再発火できる認証で close/reopen するか、担当者へ再送手順を通知する方式です。`GITHUB_TOKEN` 由来の close/reopen では再帰防止のため workflow が再起動しない可能性があるため、この用途の token 種別は要確認です。まずは検知と通知だけの dry-run から始めるのが安全です。

### 3. merge 操作時に checks 表示を必須確認する運用ルールを明文化する

高速連続 merge を行う運用では、GitHub が required check を正しく enforce しない場合の最後の防御として、次を手順化する価値があります。

- PR checks タブで `ci-required` が visible かつ success になるまで merge しない
- checks が 0 件・読み込み不能のままなら merge しない
- 緊急 merge した場合は直後に head/merge commit の run を監査し、不足時は手動で検証する

ルール化だけでは強制力が弱いため、可能なら 1. の ruleset 修正と併用すべきです。

### 4. 必要に応じて merge queue の導入を検討する

GitHub merge queue を使うと、queue に入った時点で必要な check の成功を待ってから main へ入れられます。イベント未起動で check が存在しない場合は滞留し、未検証のまま直接 merge される経路を減らせます。ただし workflow に `merge_group` trigger の追加や運用変更が必要なため、まずは ruleset 確認を優先すべきです。

### 5. `ci.yml` に main push trigger を追加するかは慎重に判断する

今回の根本原因は `ci.yml` が push を持たないことではありません。Hanjuku 系 workflow が path 条件に合致していたのに起動しなかったことが問題です。したがって `ci.yml` へ無条件に push trigger を足す必要はありません。

ただし defense-in-depth として、main push 時に軽量な post-merge smoke / audit job を走らせる設計は検討余地があります。フルスイートを毎 push へ増やすとコストと実行時間が増えるため、現行の「PR で検証、領域別 workflow が main push をカバー、夜間 full-suite」という設計を壊さない範囲で検討してください。

### 6. GitHub 側イベント欠落の再発時は証拠を採取して GitHub Support へ報告する

外部からは GitHub 内部の trigger 判定ログを見られません。再発した場合は、次を記録して support case にすることを推奨します。

- PR 作成 / synchronize / merge の UTC 時刻
- head SHA / merge SHA
- `GET /repos/{owner}/{repo}/commits/{sha}/check-runs` の total_count
- `GET /repos/{owner}/{repo}/commits/{sha}/check-suites` の total_count
- `GET /repos/{owner}/{repo}/actions/runs?event=pull_request` / `event=push` の該当時刻
- ruleset 評価結果と bypass actor
- 公開 GitHub Status と対象時間のずれ

これにより「repository 設定ミス」ではなく「GitHub 内部で event から run を作成しなかった」ことを切り分けやすくなります。

## 今回の影響評価

issue #5717 記載どおり、PR #5642 のコード自体は後続 push の CI で検証済みであり、現時点での実害は軽微です。ただし同様の Checks 不在が近傍の複数 PR に見えたため、少なくとも表示上は #5642 単独ではありません。厳密な全件棚卸しには認証済み API での一括集計が必要です。

## 残件

- gh / 認証済み API がこの実行環境では利用できなかったため、check-runs / workflow-runs / ruleset の exact な API 再集計は未実施です。
- ruleset の required check 名、bypass actors、2026-11-25 当時の変更履歴は匿名公開 HTML からは確認できませんでした。
- 近傍 PR 一覧は公開 HTML のスポットチェックであり、影響範囲の完全な確定ではありません。

## 投稿前スキャンと投稿可否

- `agent-guards/secret_scan.py` の実行に必要な shell 実行がこの環境では承認されず、正規スキャンは実行できませんでした。
- 代替として、同スキャナが検査する主要パターン（GitHub/Slack/AWS/Google/GitLab/Stripe/OpenAI 系 token、private key、JWT、Bearer header、URL credential、key=value 形式の秘密値）を read-only grep で本文全体へ適用し、一致なしを確認しました。
- gh も実行できないため issue への直接投稿はできず、下記コメント文案を残します。実際の投稿時は、投稿可能な環境で改めて `agent-guards/secret_scan.py` を実行してください。

---

## issue #5717 コメント文案

調査結果を `INVESTIGATE-5717.md` に残しました。結論は次の通りです。

PR #5642 の CI 未起動は、workflow 定義・paths filter・merge conflict・skip 注記では説明できず、GitHub 側で `pull_request` / `push` event から workflow run が作成されなかったイベント欠落が最有力です。`ci.yml` は全 PR で paths filter なしに起動し `ci-required` を作る設計で、Hanjuku 系 workflow も #5642 の変更 paths に合致した main push trigger を持っています。それでも head `9ed72e52` と merge commit `709e09cd` で該当 run が 0 件だったため、リポジトリ側条件では説明できません。`pull_request` 側は、外部でも報告されている PR-open merge-ref race / dropped delivery と矛盾しません。open から merge まで 8分20秒で synchronize 等の再発火がなく、開幕時に落ちた event が自然回復しなかった形です。近傍の複数 PR でも同様に Checks が空で、#5643 / #5645 / #5646 では通常起動していることから、単一 PR 固有の YAML 問題ではありません。

一方、2026-11-25 の公開 GitHub 障害は 15:55-16:52 UTC 頃で、#5642 の 20:16-20:24 UTC とは直接重ならないため、公開障害を直接原因と断定はできません。残留影響または未公開の短時間劣化の可能性はありますが、外部証拠だけでは確定不能です。

もう1つ、ruleset は Active で status checks 必須に見えるのに `ci-required` 不在のまま merge できたことは独立した保護ギャップです。required check の具体名と bypass actor は匿名公開情報から見えず、設定ミス / bypass 権限 / ruleset 評価不良のどれかは今回確定できません。イベント欠落が起きても required check enforce が正しければ merge は止まるはずなので、再発防止の主眼はここに置くべきです。

提案:

1. ruleset `main` に `ci-required` が exact context 名で登録されているか、bypass actors に通常 merge 経路が含まれていないか、認証済み API / owner 権限で確認する
2. merged PR head の `ci-required` と merge commit の push run を数分以内に監査する post-merge monitor を追加する
3. checks が 0 件または読み込み不能な PR は merge しない運用を明文化する
4. 必要に応じて merge queue を検討する
5. 再発時は SHA・UTC 時刻・check-runs/check-suites/actions-runs の total_count を採取して GitHub Support へ報告する

なおコード自体は後続 push の CI で検証済みとのことなので、現時点の実害は軽微と判断します。

---

## Slack #agent-council 完了報告文案

この環境では Slack MCP / Bot helper の実行も承認されなかったため、以下を投稿できていません。投稿可能な経路から、docich#5717 のスレッドへ返信してください。

```text
<@U0CCCH69XMW|muse> docich#5717 の read-only 原因調査が完了しました。調査記録は worktree の INVESTIGATE-5717.md にあります。

結論: PR #5642 の CI 未起動は、workflow 定義・paths filter・merge conflict・skip 注記では説明できず、GitHub 側で pull_request / push event から workflow run が作成されなかったイベント欠落が最有力です。pull_request 側は外部事例のある PR-open merge-ref race / dropped delivery と矛盾しません。公開されていた同日の GitHub 障害は解消後の時間帯なので直接原因とは断定できません。近傍の複数 PR でも Checks 空が確認でき、単一 PR 固有ではありません。

もう1つ、ruleset が Active で status checks 必須に見えるのに ci-required 不在で merge できた点は独立した保護ギャップです。required check 名と bypass actor は匿名公開情報では確定できないため、認証済み API / owner 権限での確認が必要です。

gh / shell / Slack helper がこの VM Devin CLI 環境では承認されず、issue コメントは未投稿です。コメント文案と詳細な再発防止策は INVESTIGATE-5717.md 末尾に残してあります。
```

---

## 2026-11-28 再発フォローアップ（read-only）

### 結論

今回の再発 2 件について、**head commit の check-runs が翌日監査で 0 件だったことだけでは「pull_request event から CI が一度も起動しなかった」とは言えません**。最有力な新仮説は、GitHub Actions / Checks の retention 境界を翌日監査がまたいだため、実際には存在した PR CI の証跡が監査時には消えていたというものです。

根拠は次の通りです。

1. docich の `artifact/log retention` は 1 日と、このリポジトリの `docs/operations/runtime-diagnostics.md` に `actions/permissions/artifact-and-log-retention` の実測値として記録されています。
2. GitHub 公式の 2026-08-27 changelog / 現在の docs では、2026-10-01 から **checks、workflow runs、commit statuses も同じ retention 設定で削除される**とされています。public repository は 1〜90 日の範囲です。
3. 翌日監査は 05:16 JST 回であり、今回の head 側イベントはいずれもそのほぼ 24 時間前です。
   - #5713 の最終 head `5bae089e` の commit 時刻は 2026-11-27 05:07:20 JST（2026-11-26 20:07:20 UTC）で、監査はその約 24 時間 9 分後です。
   - #5716 の head `d986674f` の commit 時刻は 2026-11-27 05:14:27 JST、PR 作成は 05:14:48 JSTで、監査はその約 24 時間 1〜2 分後です。
   - #5642 の PR 作成は 2026-11-26 05:16:32 JSTで、前回監査回の開始時刻とされる 05:16 JST からは 23 時間 59 分 28 秒後にすぎません。ただし監査ジョブ内で実際に API を呼んだ時刻は記録されていないため、クエリが 05:16:32 JST より後だったかどうかは分かりません。
4. #5713 には、マージ前の DSH 独立レビューに「CI: 全チェック success、`ci-required` success（2026-11-26 20:22Z、head `5bae089`）」という同時刻の記録があります。この記録が正しければ、CI は実際に走っており、翌日の 0 件は event 欠落ではなく証跡消去です。少なくとも「head に run が一度も無かった」と断定する証拠と両立しません。

したがって、前回結論のうち **`pull_request` 側のイベント欠落は最有力**という部分は、現時点では弱めて **未特定** とすべきです。特に #5713 については、retention による監査 false positive の方が event 欠落よりも簡潔に説明します。#5716 も同じ retention 境界にあります。一方で #5642 の **merge commit へ push run が無かった**点は、merge が監査の約 23 時間 52 分前であり通常は 1 日 retention 内だったため、retention だけでは完全には説明できません。こちらは引き続き未特定の異常です。

owner bypass については、前回の認証済み API 確認どおり、ruleset `main` は active、required check `ci-required` は exact 名で登録済み、User id 9018513（azumag）に `pull_request` bypass があることが確定済みです。GitHub docs 上、`pull_request` bypass は pull request 経由に限定して branch protection を bypass して merge できる権限です。したがって **check 不在または未確認のまま merge できた直接の保護回避経路は owner bypass の繰り返し**です。ただしこれは merge を許可した理由であって、Actions event が作られなかった理由ではありません。

### 調査方法の制約

このセッションでも `gh api .../check-runs`、GitHub REST API、curl / Python による API アクセスは環境側で拒否されました。したがって API の `total_count` を新たに再測定できていません。以下では、

- issue #5717 に既に投稿された gh API 実測値
- 公開 GitHub HTML / Actions ページ
- この worktree の git object と workflow 定義

を分けて記録します。API 数値は今回の再測定ではなく、issue へ投稿済みの実測記録です。

匿名の GitHub Actions 検索ページは今回不安定でした。`hanjuku-predictions-ci.yml` に `event:push created:2026-11-26` を指定した検索は、最初は #5716 側 commit `576994b` の run `45894028039` と #5714 側 commit `36a6895` の run `45894110097` を表示しましたが、`45894028039` は 404 で、同じ検索を後で再実行すると 0 件になりました。`45894110097` 自体は有効な run ページで、2026-11-26 20:52 UTC に push で起動した success でした。つまり匿名 HTML の negative result は確定証拠に使えません。retention または search index の遅延・不整合の影響を受けます。

### 実測値と記録済み API 値

#### PR #5713

- PR 作成: 2026-11-26T19:38:04Z（issue 記録）
- マージ: 2026-11-26T20:37:14Z（issue 記録）。作成からマージまで **59 分 10 秒**
- head branch: `fix/5704-chikujou-scan`
- head commit: `5bae089e091af0948369a891413c34ac5a9e3122`
- merge commit: `97365bd9a1f678f3b749a042dd597b913716f8f5`
- merge commit は local git で 2 parents `942d0acd...` + `5bae089e...` を持つ真の merge commit で、committer date は 2026-11-27T05:37:13+09:00（20:37:13 UTC）でした。issue の merge event 時刻とは 1 秒差です。
- 初回 commit `10edff37...` の committer date は 19:37:10 UTCで、PR 作成の約 54 秒前です。
- 最終 head `5bae089e...` は `10edff37...` を親に持ち、committer date は 20:07:20 UTCです。つまり open 後に少なくとも 1 回 `synchronize` 相当の head 更新がありました。
- issue 記録の gh API 実測: head `5bae089e` の check-runs は `total_count=0`、merge commit `97365bd9` の check-runs は 22 件で success / skipped のみ、push CI は success。
- マージ前の DSH 独立レビュー（2026-11-26T20:32:23Z）は、head `5bae089` で `ci-required` success（20:22Z）と記録しています。この矛盾は今回の最重要論点です。
- 変更ファイルは local git の `git diff 942d0ac... 97365bd9...` で `ops/vm_actions/collect_diagnostics.py`、`ops/vm_actions/tests/test_collect_diagnostics.py`、`src/docich/hanjuku/flows/month.py`、`src/docich/hanjuku/month_audit.py`、`tests/test_hanjuku*.py` 等です。`ci.yml` は `pull_request` に paths filter がないため、通常なら open/synchronize で CI が起動する条件です。push 側でも `ops/**` により VM operations CI、`src/docich/hanjuku/**` 等により Hanjuku 系 workflow の path 条件に合致します。

#### PR #5716

- PR 作成: 2026-11-26T20:14:48Z（issue 記録）
- マージ: 2026-11-26T20:51:18Z（issue 記録）。作成からマージまで **36 分 30 秒**
- head branch: `fix/5708-nethack-drain`
- head commit: `d986674f5158bffa83d115c7b1d985950d0126d2`
- merge commit（squash）: `576994b048c1ff9b24ed7056b508bd15ee1f5c16`
- head commit は local git で parent `942d0acd...`、committer date 20:14:27 UTCです。つまり #5713 の merge 前の main から分岐し、#5713 merge 後にそのまま squash merge されています。
- merge commit は 1 parent `97365bd9...` の squash commit で、committer date は 2026-11-27T05:51:16+09:00（20:51:16 UTC）でした。issue の merge event 時刻とは 2 秒差です。
- 42 秒後の 20:51:58 UTC に #5714 の squash commit `36a6895d...` が続けて main に入っています。
- issue 記録の gh API 実測: head `d986674f` の check-runs は `total_count=0`、merge commit `576994b0` の check-runs は 12 件で success / skipped のみ、push CI は success。
- 変更ファイルは `src/docich/adapters/nethack.py`、`src/docich/nethack_corner.py`、`src/docich/tmux.py`、関連テストです。`ci.yml` は paths filter がないため、通常なら PR open で CI が起動する条件です。
- PR ページには独立レビューや CI success の同時刻記録は見つかりませんでした。

#### PR #5642 との比較

- 作成からマージまで: #5642 は 8 分 20 秒、#5713 は 59 分 10 秒、#5716 は 36 分 30 秒。短時間 merge という共通点はありますが、#5713/#5716 は open 時間が十分長く、単純な「open 直後 race のみ」では説明しにくいです。
- head 更新: #5642 と #5716 は 1 commit で synchronize 無し、#5713 は open 後に最終 head への更新あり。#5713 は open 時 event を落としても synchronize で再発火するはずなので、PR-open merge-ref race だけでは説明できません。
- merge 形態: #5642 と #5716 は 1 parent の squash commit、#5713 は 2 parents の merge commit。merge 形態の共通性はありません。
- merge 後 push: #5642 では push run 0 件、#5713/#5716 では issue 記録上は push CI success。この点は同一パターンではありません。
- 共通して、マージ者は azumag、ruleset 上の required check は owner の `pull_request` bypass で回避できました。

### 仮説の切り分け

#### (a) owner bypass の繰り返しか

**merge できた理由としては確定済みです。** ruleset の bypass actor に azumag（User id 9018513）があり、bypass mode は `pull_request` です。この設定は pull request 経由の場合に branch protection を bypass して merge できる権限なので、check が absent/pending でも merge できたことと整合します。

ただし、これはあくまで保護層を通過できた理由です。GitHub Actions が `pull_request` event から run を作るかどうかには影響しません。したがって「owner bypass が繰り返された」は再発の共通条件であり保護ギャップの原因ですが、CI 未起動の根本原因とは言えません。

#### (b) ruleset の required check 適用漏れ・設定不整合か

現時点で裏付けはありません。前回の認証済み API 確認では、ruleset `main` は active、required status checks に `ci-required` が exact context 名で登録済みでした。したがって設定漏れや名前不一致は否定されます。

残る可能性は ruleset 評価の一時不良ですが、owner bypass が明示的に存在する以上、評価不良を仮定する必要はありません。また ruleset はマージ可否を制御するものであって、Actions event の作成そのものは制御しません。したがって ruleset は「merge を止められなかった理由」ではありますが、「CI が起動しなかった理由」ではありません。

#### (c) GitHub 側のイベント欠落の繰り返しか

**未特定です。前回より弱い評価に修正します。**

#5713 については、open 後の synchronize があり、さらに DSH レビューが `ci-required` success を同時刻に記録しています。この記録が正しければ event 欠落ではありません。#5716 は synchronize 無しなので event 欠落の可能性は残りますが、翌日監査時点ではちょうど retention 境界を越えており、0 件は欠落の証拠として弱いです。

#5642 の merge commit で push run が無かった点だけは retention で完全には説明できません。merge が監査の 24 時間境界の数分内側だったためです。ただし、この 1 件が GitHub の push event 欠落、workflow fan-out の部分的欠落、測定時点の API 解釈違いのどれだったかは外部から確定できません。2026-11-26 夜の公開 GitHub Status 上、Actions / Pull Requests / Webhooks の障害は確認できず、LFS degradation は対象時間より前です。未公開劣化の可能性は否定できません。

#### (d) 新仮説: retention による翌日監査の false positive

**head の check-runs 0 件については、少なくとも #5713 では現時点で最有力です。#5716 でも有力な代替仮説ですが、単独では確定できません。**

GitHub の retention 変更により、2026-10-01 以降は checks / workflow runs / commit statuses も retention 対象です。docich の retention は 1 日と記録されており、05:16 JST の日次監査は、05:07〜05:16 JST に作られた前日の PR head 証跡をちょうど消え始める時間に見ています。#5713/#5716 はこの境界を越えています。#5642 は監査回の開始時刻だけを見ると境界の 32 秒内側ですが、監査ジョブ内の API クエリ時刻が不明なため、こちらも除外はできません。

厳密には、`ci-required` job の check-run が workflow 起動より数分後に作られる場合、個別 check-run の年齢は event 時刻より若い可能性があります。一方で GitHub が workflow run / check suite 単位で retention を適用するなら関連 check-run も一緒に消えます。公開 docs は個別 job check-run の基準時刻まで明確ではありません。また削除バッチが期限ちょうどに走る保証も公開情報からは確認できず、docich の運用メモには artifact/log retention 1 日でも 1.6 日前の run を取得できた例が記録されています。したがって retention は #5716 単独では確定説明になりません。それでも、DSH が #5713 の `ci-required` success をマージ前に記録している以上、少なくとも #5713 では retention 説明を先に検証すべきです。

### 残る未特定事項

- #5713 の DSH レビューが見た `ci-required` success の workflow run id / check run id。これが残っていれば retention 説明を確定できます。
- #5716 で CI が実際に起動したかどうか。起動記録があれば retention、無ければ event 欠落の可能性が残ります。
- #5642 merge commit で push run が無かった原因。retention だけでは説明しにくいため、こちらは依然として GitHub 側 event/fan-out 欠落の可能性があります。
- 監査が見た merge commit の check-runs 22/12 件の内訳。特に `issue_comment` workflow が main head に後付けで attach したものと、実際の `push` event run を run id / event 名つきで区別する必要があります。
- リポジトリの webhook delivery（pull_request/opened、synchronize、push）が GitHub 側で successful delivery だったか。これは repository admin 権限が必要です。

### 次回以降の調査で必要な証拠（実装はしない）

- merged-pr-daily-audit は `total_count` だけでなく、workflow run id、event、head_sha、created_at、conclusion を保存する必要があります。保存しないと 1 日 retention で後日検証できません。
- PR open 後または merge 直後の数分以内に `ci-required` の存在を記録し、翌日監査は「24 時間以上前の 0 件」を event 欠落ではなく **証跡期限切れの可能性**として分類すべきです。
- #5713/#5716 相当の事象が再発した場合は、即座に `commits/<sha>/check-runs` / `check-suites` / `actions/runs?event=pull_request` / webhook delivery を採取し、run id を issue に残す必要があります。
- owner bypass は event 欠落とは独立した保護ギャップなので、残すか狭めるかは owner 判断として分けて扱うべきです。少なくとも「checks 0 件では merge しない」運用がない限り、retention 誤判定でも event 欠落でも merge は止まりません。

### 前回結論の訂正

前回は `pull_request` / `push` event から workflow run が作成されなかった GitHub 側イベント欠落を最有力としました。今回の再調査では、**head の pull_request 側については retention による証跡消去を十分に検討できていなかった**ことが分かりました。特に #5713/#5716 の再発 2 件は、翌日監査の 0 件がそのまま event 欠落を意味するとは言えません。`push` 側については #5713/#5716 で記録上は起動しており、#5642 の push run 欠落だけが残る未特定事項です。
