# handoff / ops_brief の正本と配布

運用正本は **docichルートのローカル `handoff.md` だけ**。内部運用記録を含むため
公開Gitへ追加しない。SorenサブモジュールやVM側のhandoffを独立更新しない。
既存のVM側handoffは自動では削除しないが、この生成・検証・配布経路では読まない。

## 入力からruntimeまで

1. 親の非公開 `handoff.md` を更新する。先頭の `##` 見出し3件は配信で使うため、
   見出しには機密情報や未確認の「本番反映済み」を書かない。
2. 親の `ops/vm_actions/ops_brief.py build` が日付・課題番号を取り除き、
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
   `/home/ubuntu/soren/prompts/ops_brief.md` に既存のatomic projectionで配布する。
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

ローカルファイルの変更だけではGitHub Actionsは起動しない。公開用JSONの親PRを
統合した時点で既存push deployが起動する。非公開handoffの自動アップロード、
GitHub上の自動コミット、常駐watcherは導入しない。

## 初回移行と検証の境界

- `gateway.py` はVM上のroot-owned installed copyで動く。通常のコード配備だけでは
  更新されない。承認済みownerの導入手順で **gateway.pyとops_brief.pyをセット**で
  installする必要がある。本変更の実装作業ではinstall/VM操作を行っていない。
- workflowは配布前にstatusの `capabilities` に `parent_ops_brief_v1` があることを
  必須確認する。古いgatewayが生成物を無視してdeploy成功を返すケースを防ぐ。
- 初回はVM生成物が旧gitlinkのtracked blobとバイト/mode一致する場合だけ移行する。
  古い別版・手動編集・未知の差分はfail-closed。原因を調べて既存のreviewed recovery
  契約で解消し、無条件copy/resetやbootstrap/rebaselineで成功に見せない。
- 親投影開始後にJSONを削除して旧submodule版へ戻すdeployも拒否する。
- status/diagnosticsの `ops_brief_projection.status` は
  `matched` / `drift` / `unmanaged` / `unknown` のみ。本文・パス・ソースhashを公開しない。
  `unmanaged` は親投影未移行であり、生成物同期成功ではない。
- 日付タイトル更新 `update_stream_title_day.sh`、ゲーム切替タイトル更新
  `update_stream_game.sh`、コメント返しは従来の `prompts/ops_brief.md` を読む。
  箇条書き形式を維持するためconsumer変更やworker再起動は不要。
  配布はタイトルAPIを即時発火しない。次の通常更新での実タイトル反映は別のE2E確認。

## この実装の引き継ぎ

作業は分離worktreeのローカルコミットまで。本番VM、共有main、Sorenのgitlinkと
既存ユーザー差分は変更しない。共有handoffを更新する権限を広げず、この文書に
設計と導入残件を残す。バナー/音声、外部PR公開、main統合、gateway install、配布、
実タイトル確認は未実施。生成JSONの見出しは作業時点の既存handoffから抽出した内容で、
この設計変更を本番反映済みと記載するものではない。

ローカル検証（2026-09-22、macOS / Python 3.14）:

- `python3 -m unittest ops.vm_actions.tests.test_ops_brief -q`: 最終20件成功。
- `python3 -m unittest discover -s ops/vm_actions/tests -q`: 592件中588成功、
  3失敗、1skip（追加テスト拡充前の全体実行）。失敗は変更前と同一のMoomoo
  preflight 2件（GNU `stat -c`）とradio restart 1件（Linux `/proc` 必須）。
  radioはsandbox外でも再現した。対象の既存コード・テストに差分なし。
- `check-source`（唯一の親handoffを明示）、`check-artifact`、Python構文確認、
  installerの `bash -n`、`git diff --check` は成功。
- GitHub上のCIと独立エージェントレビューは未実施。Linuxでの全体CIと、将来の
  承認済みgateway導入・VM projection・実タイトル確認は別の確認段階とする。
