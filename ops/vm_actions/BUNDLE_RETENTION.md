# VM Git bundle retention

`state/bundles/docich/*.bundle` は production / preview deploy の入力として使うため、参照状態を確認せず削除してはいけません。一方、無期限保持すると deploy 回数に比例して VM storage を消費するため、`prune_bundles.py` で fail-closed なローテーションを行います。

## Retention policy

削除候補になるのは、次の条件を **すべて**満たす bundle だけです。

- current production から参照されていない
- `previous_head` から参照されていない
- retained preview release から参照されていない
- `deployment_intent.from/to` から参照されていない
- `pending_repairs[].candidate_sha` から参照されていない
- mtime が 7 日以上前
- 未参照 bundle の新しい方から 8 世代に含まれない

つまり、最近 upload されたがまだ deploy されていない bundle は最低7日間保護されます。また長期間 deploy がない場合でも、未参照bundleを新しい方から8世代残します。

## Fail-closed rules

以下の場合は1件も削除しません。

- state / bundle / release root が期待した通常ディレクトリでない
- bundle/release scan に symlink、未知名、未知entryがある
- current state、deployment intent、pending repair の参照SHAを完全に読めない
- scan 件数上限を超える
- scan 後に削除候補の inode / size / mtime が変化する

削除前に候補をすべて再検証し、実際の削除は owner-only forced-command gateway の production `exec` 内で行います。gateway は `vm-operations.lock` を保持したまま `exec` を実行するため、同じ control plane の upload/deploy/preview と競合しません。

## Automation

`.github/workflows/vm-bundle-retention.yml` は次のタイミングで rotation を試みます。

- protected main への `VM operations` deploy が成功した後
- 1日1回の安全網
- owner の手動 `workflow_dispatch`

実行前後に production `status` を確認し、VM checkout が current main と完全一致して `configured` の場合だけ mutation を許可します。main と VM がずれている、drift/recovery中、またはscanが不完全な場合は fail-closed です。

このworkflowは新しいSSH operationやsudo経路を追加しません。既存の owner-only `exec docich production <sha>` を使い、その既存lock/security boundary内だけで動作します。
