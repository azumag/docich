# gateway_state 保持設計（azumag/docich#365 の後続）

状態: 設計ドラフト。PR #3104 で分割を実測済み（releases 約 1.6GiB, bundles 約 103MiB）。案 A の保護テストと定数 `PREVIEW_RELEASE_KEEP`（現在 2）を用意した段階。**定数を 1 に変えるのは owner 同意後のみ。**

## 1. 実測（確認できた範囲）

- 2026-10-29 read-only diagnostics（run 40946074212, `storage_breakdown` v2）:
  - `bundle_storage`: bundle 6 件 / 108,970,386 B。参照 4 件、未参照 2 件。
  - `gateway_state`: 19,257 entries（scan_complete）。バイト数は CI ログ上で一部マスクされており、ここでは引用しない。
- DSH の判定（#2780 の 10-30 コメント）では `gateway_state` は約 2.26 GiB。

読み取り: bundle は既に数百 MB 以下で、`gateway_state` の大半は bundle 以外、つまり `releases/` の preview checkout と推定される。**未確認**: releases の実バイト数。現状の diagnostics は bundles と releases を分けていない。

## 2. 仕組み（コード上の事実）

- `deploy_preview` は `git clone --no-checkout <bundle>` で `releases/<repo>/<sha>/` を作る。bundle からの clone は object を全件コピーするため、1 release は repo 全体のサイズに近い。
- `_prune_preview_releases(keep=2)` は preview deploy のたびに呼ばれ、protected sha と新しい順の 2 件を残す。
- 削除は「`HEAD == name` かつ `git_clean`」の release だけ。dirty / HEAD 不一致は fail-closed で残す（件数上限が効かない原因になりうる）。
- bundle 側は `prune_bundles.py`（#387）で、参照中を保護し未参照は新しい方 2 件だけ残す。

## 3. 保護条件（削除候補から外すもの、必須）

- current production SHA
- `previous_head`
- retained preview release（対応 SHA）
- `deployment_intent.from` / `to`
- `pending_repairs[].candidate_sha`
- 削除前に候補の inode / size / mtime を再検証（既存の prune と同じ）
- dirty / drift / recovery 中は全削除を拒否
- 削除は gateway の `vm-operations.lock` 内、owner-only `exec` 経路だけ

## 4. 選択肢

| 案 | 内容 | 利点 | 懸念 |
|---|---|---|---|
| A | releases の keep を 2 → 1（protected sha のみ） | 1 checkout 分の回収 | preview の即時ロールバック先が消える。owner 判断 |
| B | dirty / HEAD 不一致の release を、理由付きで隔離してから削除 | 件数上限が効く | 誤削除の検証が必要。設計レビュー必須 |
| C | release を bundle の alternates 参照にする | 容量を大きく下げられる可能性 | 参照先 object の GC 競合。本番 `.git` に触れるため慎重に |

推奨: まず分割の実測（本 PR）で releases の寄与を確定し、寄与が大きければ A を先に検討する。B / C は別 PR。

## 4.1 案 A の影響（owner 同意前に確認すること）

- `preview exec` と `preview status` は `releases/<repo>/<sha>` の存在を要求する。keep=1 では直前の preview SHA が消えるため、そのロールバック先は再 upload まで使えない。
- 削除は preview 配備時のみ。bundle prune は現存 release の SHA を preview 参照として保護するため、release が減ると未参照 bundle の保持枠が 2 件のルールで回る（想定どおり）。
- release の削除は `deployment_intent` / `pending_repairs` を参照していない。pending repair の candidate が preview release を必要とするケースがないか、flip 前に確認すること（未確認）。

## 5. 受け入れ条件

- 分割の実測（gateway_bundles / gateway_releases）を production で 1 回取得し、本ドキュメントに数値を追記する。
- 寄与が確定してから削除ロジックの PR を作る（保護条件 §3 のテストを先に書く）。
- 反映後に storage 使用率が悪化しないことを diagnostics で確認する。

## 6. この PR の範囲

- `collect_diagnostics.py`: `gateway_bundles` / `gateway_releases` を `gateway_state` の重なるサブツリーとして追加（read-only、上限付き）。
- `summarize_storage.py`: 任意キーとして出力。欠落や未完了で shared incomplete を変えない。
- テスト: 分割の計測、欠落時に incomplete を変えないこと。
- 削除・retention の挙動変更はなし。
