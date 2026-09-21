# handoff / ops_brief の正本と配布

運用正本は **docichルートのローカル `handoff.md` だけ**。内部運用記録を含むため
公開Gitへ追加しない。SorenサブモジュールやVM側のhandoffを独立更新しない。
既存のVM側handoffは自動では削除しないが、この生成・検証・配布経路では読まない。

## 入力からruntimeまで

1. 親の非公開 `handoff.md` を更新する。先頭の `##` 見出し3件は配信で使うため、
   見出しには機密情報や未確認の「本番反映済み」を書かない。
2. 親の `ops/vm_actions/ops_brief.py build` が日付・`Issue #123` / `docich#123` /
   `PR #899` 等の内部番号を取り除き、
   最大70文字の公開用topicとソース全体のSHA-256、生成markdownのSHA-256を
   `ops/runtime_context/ops_brief.json` に保存する。時刻・絶対パス・本文は含めない。
3. `check-source` が正本から再生成してJSON全体を比較する。本文だけの変更も
   ソースSHAで検出する。正本がない場合は失敗し、VMやSoren側へfallbackしない。
4. JSONを**docichの作業ブランチだけ**でコミットしてレビューする。
   CIの `check-artifact` はschema・正規化・出力SHAを検証し、回帰テストは
   ソース変更・改変・欠落・rollbackを検証する。CIは非公開ソースを持たないので、
   **CI成功だけで最新handoffとの一致を主張しない**。ローカル照合結果が必要。
5. 将来の承認済みmain統合時に、既存のowner-only `VM operations` が親コミットを
   bundleで送る。gatewayはそのコミットのJSONを検証してmarkdownを生成し、
   `/home/ubuntu/soren/prompts/ops_brief.md` にdescriptor-bound projectionで配布する。
   gitlink変更がなくても実行する。state確定失敗時は親HEAD・生成物をrollbackし、
   未知の同時変更があれば上書きせずrecovery_requiredとする。
6. stateは親SHA・handoffソースSHA・生成物のSHA/modeを記録し、以後のdeployと
   statusが改変・欠落を拒否/検出する。旧VM handoffの内容は一切根拠にしない。

Sorenの `games/soviet_now/prompts/ops_brief.md` tracked copyと
`tools/build_ops_brief.sh` はlegacy。親投影が有効なとき、Sorenのgitlink差分に
同ファイルが含まれていてもVMへ投影しない。毎回のSorenコミットは不要。
Sorenコードの変更は従来どおりSoren側PRと親gitlinkで扱う。

## ローカル操作

docichルートで実行する（生成・検証だけ。ネットワーク・VM操作はない）。

```bash
python3 ops/vm_actions/ops_brief.py build
python3 ops/vm_actions/ops_brief.py check-source
python3 ops/vm_actions/ops_brief.py check-artifact
```

worktreeに正本がない場合、`build` と `check-source` に
`--handoff /absolute/path/to/docich/handoff.md` を追加する。正本のコピーは作らない。
markdownのローカル照合が必要なら `materialize --output run/ops_brief.md` を使う。
共有サブモジュール内のtracked fileへ出力しない。コマンドは成功/失敗だけ表示する。

正本は並行更新され得るため、過去の `check-source` 結果は将来の同期保証ではない。
最終pushの担当者が、その直前に現在の正本から `build` → `check-source` を実行し、
生成物をコミットする。コミット後・push直前にも `check-source` を再実行し、差分が
出たら再生成からやり直す。ソース照合成功をコード・文書に恒久的な事実として残さない。
PR #902のレビュー修正ではartifactの最終再生成とコミットは親担当が行う。

ローカルファイルの変更だけではGitHub Actionsは起動しない。公開用JSONの親PRを
統合した時点で既存push deployが起動する。非公開handoffの自動アップロード、
GitHub上の自動コミット、常駐watcherは導入しない。

## 初回移行と検証の境界

- `gateway.py` はVM上のroot-owned installed copyで動く。通常のコード配備だけでは
  更新されない。承認済みownerの導入手順で **gateway.py、ops_brief.py、projection_io.pyをセット**で
  installする必要がある。本変更の実装作業ではinstall/VM操作を行っていない。
- workflowは配布前にstatusの `capabilities` に `parent_ops_brief_v1` と
  `projection_no_clobber_v1` があることを
  必須確認する。古いgatewayが生成物を無視してdeploy成功を返すケースを防ぐ。
- 初回はVM生成物が旧gitlinkのtracked blobとバイト/mode一致する場合だけ移行する。
  古い別版・手動編集・未知の差分はfail-closed。原因を調べて既存のreviewed recovery
  契約で解消し、無条件copy/resetやbootstrap/rebaselineで成功に見せない。
- 親投影開始後にJSONを削除して旧submodule版へ戻すdeployも拒否する。
- status/diagnosticsの `ops_brief_projection.status` は
  `matched` / `drift` / `unmanaged` / `unknown` のみ。本文・パス・ソースhashを公開しない。
  `drift` と `unknown` はdiagnostics全体を少なくともwarnにする。
  mapping欠落時はcollectorを動かさず、`collection.status=unavailable` を併記する。
  `unmanaged` は親投影未移行であり、生成物同期成功ではない。
- 日付タイトル更新 `update_stream_title_day.sh`、ゲーム切替タイトル更新
  `update_stream_game.sh`、コメント返しは従来の `prompts/ops_brief.md` を読む。
  箇条書き形式を維持するためconsumer変更やworker再起動は不要。
  配布はタイトルAPIを即時発火しない。次の通常更新での実タイトル反映は別のE2E確認。

## 同時更新と保全

- planningでprojection rootと既存の親ディレクトリを `O_DIRECTORY|O_NOFOLLOW` で
  開き、apply/verify/rollback終了までfdを保持する。以後の作成・rename・公開は
  `dir_fd` 相対で行う。パスのsymlink差し替えを辿らず、名前と保持fdのidentityが
  変わったらfail-closed。新規ディレクトリも同じrootから一段ずつ開く。
- POSIXの `rename` は内容比較付きの置換ではない。従来の検証→`os.replace` を
  廃止し、旧inodeを同じ親のmode 0700の `.vmops-projection-*` 内の `before` へ
  退避してから検証する。競合内容・mode変更・削除を検出したら新内容を公開しない。
- 新内容と復元は、宛先が存在すれば失敗する `linkat` 相当の `os.link(dir_fd=...)`
  で公開する。競合して作られたファイルを置換しない。削除も旧inodeの退避で行い、
  live pathの無条件unlinkは行わない。失敗時は空いている名前にだけ退避inodeを戻す。
- 退避と公開の間には短時間の名前欠落がある。既存ファイルを切れ目なく置換する保証と、
  非協調writerの同時更新を無条件に上書きしない保証はPOSIX renameだけでは両立しない。
  本経路では後者を優先する。consumerの既存の欠落時fallbackは維持する。
- `before` / `after` / `record.json` は成功・失敗とも自動削除しない。旧fdを保持した
  writerの遅延書き込みも退避inodeに残る。commit前後の検証で観測した競合は失敗にし、
  安全にrollbackできなければ `recovery_required`。処理終了後のwriter活動を未来まで
  検知する保証はなく、退避を保持することで内容の消失を防ぐ。
- 同時変更で元の名前へ復元できなければ、liveの競合ファイルと退避の両方を保全する。
  backupの位置・旧新hashはprivateな `record.json` に記録し、Actionsへ出さない。
  保全ファイルは容量を使う。既存fdのwriter終了と復旧不要をownerが確認するまで
  自動GCしない。変更不要のファイルでは退避を作らない。

APIの根拠: [linkatのno-clobber契約](https://man7.org/linux/man-pages/man2/link.2.html)、
[openatとO_NOFOLLOW](https://man7.org/linux/man-pages/man2/open.2.html)。

## この実装の引き継ぎ

PR #902の独立レビュー修正は既存 `codex/handoff-single-source` の作業ツリーで実施。
本番VM、共有main、Sorenのgitlinkと既存ユーザー差分は変更しない。共有handoffを
更新する権限を広げず、この文書に設計と導入残件を残す。修正差分のコミットとpush、
artifactの最終再生成・コミットは親担当が行う。今回のレビュー修正ではバナー/音声、
main統合、gateway install、配布、実タイトル確認は未実施。
この設計変更を本番反映済みと記載するものではない。

ローカル検証（2026-09-22、macOS / Python 3.14）:

- focused: `python3 -m unittest ops.vm_actions.tests.test_projection_io
  ops.vm_actions.tests.test_ops_brief ops.vm_actions.tests.test_deploy_state_transaction
  ops.vm_actions.tests.test_stage_repair ops.vm_actions.tests.test_pending_repairs
  ops.vm_actions.tests.test_diagnostics -q`: 82件成功。
- `python3 -m unittest ops.vm_actions.tests.test_ops_brief.BriefGenerationTests -q`:
  日本語に隣接する `PR #899等` の除去を含め、最終修正後の7件成功。
- `python3 -m unittest discover -s ops/vm_actions/tests -q`: 617件中613成功、
  3失敗、1skip。失敗は変更前と同一のMoomoo preflight 2件（GNU `stat -c`）と
  radio restart 1件（Linux `/proc` 必須）。対象の既存コード・テストに差分なし。
- source照合は最終push担当の直前検証が必要。過去の結果を最新性の保証に使わない。
- `check-artifact`、Python構文確認、installerの `bash -n`、`git diff --check` は
  レビュー修正時のローカル実行で成功。artifactの対private source鮮度を保証しない。
- PR #902の独立レビュー指摘に対応したが、修正後の独立再レビュー・GitHub CIは
  親担当へ引き継ぐ。Linuxでの全体CIと、将来の承認済みgateway導入・VM projection・
  実タイトル確認は別の確認段階とする。
