# PR merge audio report

main へ pull request がマージされたとき、その pull request のタイトルを 1 文にして VM の
audio-worker へ読み上げさせる owner-only workflow です。

## 対象とゲート

- `.github/workflows/pr-merge-audio-report.yml` は protected `main` への `push` だけで起動します。
- owner（`azumag` / actor ID `9018513`）、`github.triggering_actor == azumag`、
  `refs/heads/main`、`github.ref_protected == true` のすべてを要求します。それ以外は
  job ごと skip します。
- checkout は `main` の trusted copy だけを使い、pull request のコードは workflow 内で
  実行しません。`environment: vm-operations` の secrets は VM gateway への固定 script
  実行だけに使います。
- `ops/vm_actions/authorize_pr_merge_audio.py` が workflow 文脈（repository / owner /
  protected main / workflow path / event name）と push payload（`ref`、
  `before`/`after`、`forced`/`deleted`、`head_commit`）を完全一致で検証します。

## 読み上げ内容

- マージ commit（`github.sha`）に紐づく pull request を GitHub API
  `/repos/{repo}/commits/{sha}/pulls` で引き、`merged_at` があり base が `main` のものだけを
  対象にします。
- 文面は `mainにプルリクエスト<番号>「<タイトル>」がマージされました。` です。タイトルは
  1 行化し、markdown・URL・制御/不可視文字を除去し、末尾の `(#123)` を落とし、
  240 字に丸めます（上限は既存の owner speak と work-indicator 音声と同じ）。
- 生成と base64 化は `ops/vm_actions/pr_merge_audio_report.py` が行い、本文は
  stdout / stderr / Actions log に出しません。出力するのは PR 番号と文字数だけです。
- VM 側は `ops/vm_actions/report_pr_merge_audio.sh` が `lib/outbound_queue.sh` の
  `enqueue_audio_text`（source=`pr_merge_report.<16桁hex nonce>`、重複抑制TTL=0）で
  audio_worker に積み、**enqueue の公開成功で exit 0** を返します。再生完了は待ちません
  （音声キューの滞留で成功した enqueue が失敗扱いにならないため）。
  稼働中の audio_worker が無い場合は何も積まずに失敗します。

## VM 前提と失敗時の扱い

- `status docich production <current-main>` が `configured` で、production baseline が
  current main の reviewed ancestor であることを `check_operator_parity.py` で確認します。
  drift / recovery_required / 50 commit を超える遅れは fail-closed です。
- 同じ push は deploy workflow も起動します。deploy が gateway の排他ロックを保持している間、
  `status` は `gateway_busy` で即時拒否されるため、この固定理由だけを最大 18 回
  （前半 5 秒・後半 15 秒の上限付きバックオフ）再試行します。drift・認可エラー・
  gateway 不在など他の拒否は再試行しません。
- 連続 merge の待機中 run が捨てられないよう、workflow に共有 concurrency group を置かず、
  各 push が独立して実行されます。enqueue の実行は VM gateway の排他ロックで直列化されます。
- merged pull request が無い push（direct push など）は VM に接続せず success で skip します。
- 読み上げの失敗は merge 自体や deploy を止めません。Actions の run を確認し、一時障害なら
  同じ run を Re-run してください。**enqueue に成功した run を再実行すると二重に読まれ得ます**
  （重複抑制TTLは0）。
- merge ごとに 1 回読み上げます。短時間に複数 merge すると audio queue に順に積まれます。
- 一時的に止めたい場合は `gh workflow disable pr-merge-audio-report.yml` を使い、
  再開時に `gh workflow enable pr-merge-audio-report.yml` を実行します。

## 検証

- `python3 -m pytest -q ops/vm_actions/tests/test_pr_merge_audio_report.py ops/vm_actions/tests/test_pr_merge_audio_report_script.py`
- `python3 -m unittest ops.vm_actions.tests.test_pr_merge_audio_report ops.vm_actions.tests.test_pr_merge_audio_report_script -v`
- workflow の policy は同テストの `WorkflowPolicyTests` が検証します。
- 実際の読み上げは VM の audio_worker が稼働している必要があります。workflow run の成功は
  queue へ公開できたことまでで、再生完了・配信ミックスでの可聴は別途確認が必要です。
