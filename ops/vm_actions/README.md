# Owner-only VM operations via GitHub Actions

`docich` を VM / 配信運用の唯一の control plane とする GitHub Actions 構成です。
main の本番反映、branch/commit の preview 反映、状態確認、owner command 実行、初回 baseline 登録を扱います。

## Repository boundary

- VM credential、Environment `vm-operations`、SSH gateway、production/preview workflow は **docich だけ**が所有します。
- `soviet_now` 側には VM 用 Environment / Secrets / workflow を置きません。`soviet_now` はゲーム・AI・ロジックの source repository として独立させます。
- 本番で使う `soviet_now` の版は、docich の `games/soviet_now` gitlink が固定します。`soviet_now` main の更新だけでは VM は変わりません。
- docich production deploy は親 commit を更新した後、allowlist 済み submodule を親 gitlink の commit へ同期します。現在の allowlist は `games/soviet_now` と `games/hanjuku-sfc-speedrun` です。
- 配信中の `/home/ubuntu/soren` への反映も **docich gatewayだけ**が担当します。`games/soviet_now` の旧gitlink→新gitlinkで変更されたtracked fileだけを投影し、変更対象のlive fileが旧commitと一致しなければ上書きせず停止します。
- submodule URL、HEAD、tracked working tree、投影対象live file が期待値と違う場合は fail-closed します。

## Security boundary

- `.github/workflows/vm-operations.yml` は owner (`azumag`, actor ID `9018513`) と再実行者、`refs/heads/main`、protected ref を検証します。
- VM credential は GitHub Environment `vm-operations` にだけ置き、deployment branch/tag policy は main branch だけにします。
- main は branch protection/ruleset で保護し、VM workflow/control-plane の変更を owner review 対象にします。
- SSH host key は `VM_SSH_KNOWN_HOSTS` に固定し、workflow 内で `ssh-keyscan` はしません。
- Actions 専用秘密鍵は VM の forced command に固定し、通常 shell、PTY、port/agent/X11 forwarding を許しません。
- candidate branch のコードを trusted control job で import/source/test しません。candidate は Actions 上で実行せず、full-history Git bundle としてVMへ渡します。
- preview command は `bubblewrap` で network と production filesystem から分離した一時コピー上で実行します。
- production command の stdout/stderr は Actions log に返さず、VM private logへ mode 0600 で保存します。command input に token/password を直接書かないでください。

## Production deployment

`/home/ubuntu/docich` は既存 Git worktree を維持します。production payload は tar 上書きではなく Git bundle です。

初回 `bootstrap` では現在 HEAD と tracked clean state（owned submoduleを含む）を baseline にします。その後の deploy は、現在 HEAD / tracked files / owned submodule が前回記録と一致するときだけ verified SHA へ進めます。

親 commit の `games/soviet_now` gitlinkが変わった場合、docich gateway がその固定commitへ submodule を更新し、旧commit→新commitで差分になったregular tracked fileだけを `/home/ubuntu/soren` へ原子的に投影します。無関係なruntime fileや、source側で変更されていない `strategy.py` 等は触りません。source側で変更されたpathがlive側で旧commitまたは新commitのどちらにも一致しない場合はdriftとして拒否します。live側が新commitのbytes+modeと完全一致しているpathはowner-only bounded repair等で先行反映済みのreviewed postimageとして再書込みせず採用し、それ以外の第三状態はfail-closedを維持します。ただし、reviewed commit が squash merge で main 履歴から落ち、旧gitlink→新gitlink の reachable history に現れない場合に限り、`ops/vm_actions/reviewed_lineage_attestations.json` へその old/new gitlink pair と同path blob の sha256+mode を明示登録できます。push-triggered reconcile は登録済みblobだけを渡し、liveのbytesとmodeが完全一致したときに限り旧baselineへ収束させます。未登録のbytesは一般許可しません。複数fileの途中失敗時は、それまでの投影とdocich親HEADを旧状態へ戻します。

これにより `soviet_now` 自身はVM資格情報や配信運用を知る必要がなく、`soviet_now main` の更新だけでは本番は変わりません。docichのgitlink bumpをレビューしてmainへ入れた時だけlive Sorenへの投影対象になります。

### Production checkout の Git remote / ad-hoc reset 禁止

`/home/ubuntu/docich` は canonical control plane だけが更新する対象で、**fetchable な Git remote（`origin` 等）を持ちません**。deploy は uploaded bundle を `git fetch <bundle>`、pre-synced reconcile は production root から reviewed URL を明示 fetch するだけで、`git fetch origin` / `git reset --hard origin/main` のような ad-hoc な remote 同期は使いません。

2026-09-15、production checkout が手動 `git reset --hard origin/main` で out-of-band に進み、recorded baseline と乖離して canonical deploy が `tracked_vm_drift` / baseline 不一致で失敗する事象がありました（Issue #412）。復旧は owner-only `rebaseline`（tracked-clean かつ HEAD が requested commit の ancestor のときだけ現在 HEAD を baseline に採用）→ canonical `deploy` のみを使います。

- production checkout へ remote を追加して `git pull` / `git reset --hard origin/main` をしない。更新は docich main への merge → `VM operations` deploy のみ。
- out-of-band な HEAD/baseline 移動は hourly `vm-storage-monitor` の `[VM deploy] production baseline alert` が検知する。`git_clean(root)==false` は `[VM deploy] tracked drift alert` が検知する。
- 監査は `/home/ubuntu/docich` の `git reflog` を read-only で確認する。

### turn-based 戦略を人手の PR で上書き反映する手順

turn-based の `strategy.py` / `strategy_helpers/` / `best_score.txt` などは改善ループ（`eloop_improve.sh` → `strategy/persist.sh`）が**実機で書き**、管理 clone `/home/ubuntu/soren-persist` 経由で **専用 candidate branch `runtime/eloop-improve` を更新し、`soviet_now main` への PR を作成**します（**main へは直接 push しません**）。採用戦略は PR + CI + review を通ったものだけが main に入ります。

> 2026-09-15、runtime が `origin/main` へ直接 push した採用戦略（`636d12e`）が reviewed main の `merge_opportunity_alternatives` まで削除し `Merge opportunity regression` を failure にしたため、main 直 push を廃止して上記の PR gate に変更しました（soviet_now #343）。`soviet_now` 側 main の ruleset / branch protection を active に戻すのは別の防御層として必要です。

`/home/ubuntu/soren` は投影先で、投影は live が **旧 gitlink と一致**するときだけ新内容で上書きし、**第三状態では fail-closed（`projection drift`）**で拒否します。runtime candidate PR を merge した場合はその内容が live と一致するため投影は採用します。一方、**live と異なる戦略を人手で上書き**したい場合は次の順で行います。

前提（これが崩れると fail-closed になる）: 上書きを反映する時点で **live == docich の現 gitlink（= 投影の old）** であること。

1. **改善を止めて live を固定する**
   - webui「予想・改善ワーカーの停止 / 開始」で `improve_daemon` を停止（`/home/ubuntu/soren/tmp/state/improve_daemon.paused` マーカー）。`soren_loop.sh` はこのマーカー中は改善を起動しません。
2. **gitlink を live に同期させる（必要なときだけ）**
   - docich の gitlink が `soviet_now main` より遅れている場合は、まず **現 main へ bump する PR** を作って deploy（live == new の採用で `status=configured`）。これで docich の gitlink == live になります。
   - 同期確認: `/home/ubuntu/soren-persist` で `git fetch origin main && diff <(git show origin/main:strategy.py) /home/ubuntu/soren/strategy.py` が空。
3. **上書き PR を作る**
   - `soviet_now` の **現在の main から** branch を切り、対象ファイルを編集して PR。runtime の candidate branch `runtime/eloop-improve` と食い違う場合は GitHub が `CONFLICTING` を出すので、rebase して必ず明示的に解消する（無視してマージしない）。
4. **反映する**
   - 上書き PR を `soviet_now main` へ merge → docich の `games/soviet_now` gitlink bump PR → merge → `VM operations` deploy。投影は live==old を確認して新内容で live を上書きします。
5. **改善を再開する**
   - マーカーを削除（webui start）。以降の改善は上書き後の戦略を起点にします。

注意:
- 改善を止めずに bump すると live が既に先へ進んでおり、`projection drift` で deploy が**拒否**されます（安全側の失敗で live は壊れません）。その場合は無理に進めず、1〜2 に戻って同期してから行います。
- runtime の永続化は candidate branch `runtime/eloop-improve` を `--force-with-lease` で更新するだけで、`main` への push は行いません（main に入るのは review/merge されたものだけ）。
- `ops/vm_actions/stage_repair.py`（bounded repair）の `ALLOWED_FILES` は overlay/audio 限定で **`strategy.py` は対象外**です。strategy を repair で扱うには reviewed control plane の変更（`ALLOWED_FILES` 追加＋`repair_policies` 登録＋root での再install）が必要です。
- 手動 `git reset` / `git fetch origin` で live を書き換えない（out-of-band 操作は禁止。#412 / 上記「ad-hoc reset 禁止」）。

## One-time setup

1. docich の main protection/ruleset を設定します。
2. docich に Environment `vm-operations` を作り、deployment branch を main のみに限定します。
3. VM に `git` と `bubblewrap`（`/usr/bin/bwrap`）があることを確認します。Ubuntu 24.04 では
   `apparmor_parser` も必要です（installer が preview 用の限定 AppArmor profile を読み込むため。
   詳細は「Operational notes」の unprivileged userns 節）。
4. Actions 専用 ed25519 鍵を作ります。

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github-vm-ops -C github-vm-ops-actions
```

5. 公開鍵と `ops/vm_actions/` を既存の信頼済みVM接続経路で置き、gatewayをroot所有で導入します。installerはdocich Git worktreeに加え、投影先 `/home/ubuntu/soren` の存在も確認し、root-owned configにだけ projection mapping を保存します。

```bash
sudo bash ops/vm_actions/install_vm_gateway.sh ~/.ssh/github-vm-ops.pub ubuntu
```

6. docich Environment に以下の secrets を設定します。

- `VM_SSH_HOST`
- `VM_SSH_USER`
- `VM_SSH_PORT`
- `VM_SSH_PRIVATE_KEY`
- `VM_SSH_KNOWN_HOSTS`
- `TYPESAFE_API_KEY`（Jevコメント分類器を有効化するときだけ。値はチャット、Issue、workflow input、ログへ貼らない）

7. 初回だけ Actions → **VM operations** で `bootstrap / production / ref=main / confirm=production` を実行します。bootstrap は本番コードを変更しません。

## Daily use

- **docich main merge → production**: main push で最新mainを本番へ反映します。
- **soviet_now更新を本番へ出す**: soviet_now側変更をmainへ入れた後、docichで `games/soviet_now` gitlinkをそのcommitへ更新してPR → docich mainへマージします。docich gatewayがgitlink差分だけをlive Sorenへ安全に投影します。
- **branch/commitをVM test areaへ**: `deploy / preview / ref=<branch-or-sha>`。成功時は展開済みpreviewを現在要求されたSHAを含む最大2世代へGCします（dirty/drift releaseは削除しません）。Git bundle は別の bounded retention で安全にローテーションします。
- **preview command**: 同じrefで `exec / preview`。本番filesystem/networkから隔離されます。
- **production command**: `exec / production / ref=main / confirm=production`。stdout/stderr本文はVM private logだけに保存します。
- **status**: `status / production` または `status / preview`。production status は総容量・利用可能bytes・使用率だけを返し、pathやログ本文は返しません。
- **reclaim**: `reclaim / production / ref=main / confirm=production` + `apply`（false=dry-run 既定 / true=適用）+ `voicevox_archive`（opt-in）。reviewed helper `ops/vm_actions/storage_reclaim.sh` の固定 allowlist だけを対象にします: `soren/tmp` の21日超 stale 検証残骸・deploy-backups（`manual_challenge*` の日付付き兄弟含む）、7日超の snapd download cache（root 0700 のため `sudo -n`、snap 本体は非対象）、origin/参照/7日 gate を通った `/tmp` の古い docich clone、dangling image と7日超 build cache の docker prune（`until=168h`。tagged image・container・volume は绝不）、apt clean / journal vacuum / snap disabled revision / `/etc/logrotate.d/soren` の適用。出力は VM private log のみで workflow には終了コードだけが現れます。安全条件と実測は `docs/operations/vm-storage-reclaim-runbook.md` を参照。
- **market_paper**: `market_paper / production / ref=main / confirm=production` + `market_paper_action`(install/enable/disable/restart) + `market_paper_market`(stocks/fx)。opt-inのstocks/FXペーパートレードworker(`docich-market-worker@<market>.service` 等、`scripts/systemd/docich-market-*`)を `systemctl --user` だけで install/enable/disable/restart します。root/sudoは使いません。`config/market-paper.toml` は変更しません（enabled/mode切り替えは通常のcode reviewを通るdeployで行う）。結果は本操作自体では返らず(exec同様output withheld)、後続の `diagnostics` の `market_paper.{stocks,fx}` セクション(unit_active/health_status/feed_unavailable等)で確認します。
- **Jevコメント分類器の有効化**: 先に Environment `vm-operations` へ `TYPESAFE_API_KEY` を登録し、`configure_jev / production / ref=main / confirm=production` を実行します。キーはstdinで固定gatewayへ渡され、呼び出し元のshellや任意commandは実行されません。VMの`.env`をatomic更新し、chat workerだけを完全再起動して、backend=`jev`・モデル・キー存在を実効環境で確認します。配信encoder、radio worker、共通基盤は再起動しません。
- **Jevコメント分類器の無効化**: `disable_jev / production / ref=main / confirm=production` を実行します。`COMMENT_CLASSIFIER_BACKEND=`を明示し、Jev設定とキーを`.env`から除去してchat workerだけを完全再起動します。どちらの操作も直前の`.env`をVM内のmode 0600バックアップへ保存し、キー値はworkflow出力へ返しません。
- **webui のコード更新を反映**: `restart_webui / production / ref=main / confirm=production`。`docich-webui.service` は常駐プロセスなので、deploy で `src/docich/webui.py` が更新されても再起動するまで旧 UI のままです。この operation は trusted main の固定スクリプト `ops/vm_actions/restart_webui.sh` だけを送り、`systemctl --user restart docich-webui.service` → 5秒以内に active を確認 → **ローカルポートに配信されている HTML がデプロイ済み `src/docich/webui.py` の `INDEX_HTML` とバイト一致するまで成功にしない**（fail-closed）。ユニットが active でも別プロセスがポートを掴んで旧 UI を配信し続けるケースを検出します。root/sudo は使わず、unit 名も固定で任意コマンド入力は受け取りません。出力は VM private log に留まり、step に現れるのは終了コードのみ: `0` 成功 / `10` restart 失敗 / `11` active にならない / `12` ExecStart が配備 root 以外（または `INDEX_HTML` 読込不能）/ `13` ローカル webui 到達不能 / `14` 配信 HTML が古い（別プロセスがポート保持 or restart 未反映）。arbitrary exec が無効な public repo でもこの operation は使えます。

## Preview release / Git bundle retention and storage alert

preview deploy の成功後、gateway は `state/releases/docich` の展開済みreleaseを世代GCします。現在要求されたSHAを必ず保護し、それを含めて最大2世代を残します。古いreleaseでもHEAD不一致、tracked drift、symlink等がある場合は証拠保全を優先して削除しません。

Git bundle は `ops/vm_actions/prune_bundles.py` と `.github/workflows/vm-bundle-retention.yml` で別途ローテーションします。current production / previous HEAD / retained preview / active pending repair から参照されるbundleは常に保護します。未参照bundleも7日以内は保護し、それより古くても新しい方から8世代を残します。deployment recovery中、非active repair、production tracked/submodule drift、scan不完全、未知entry/symlink、またはscan後の候補変化がある場合は1件も削除しません。mutationは既存owner-only production `exec` を使うため、gatewayの `vm-operations.lock` 内でupload/deploy/previewと直列化されます。詳細は `BUNDLE_RETENTION.md` を参照してください。

`.github/workflows/vm-storage-monitor.yml` は毎時17分・47分にproduction `status` だけをforced-command gateway経由で読み、80%でWARN、90%でCRITICALのGitHub Issueを1件だけ作ります。同一severityのopen Issueがあれば最新snapshotへ更新し、severity変化時は旧Issueを閉じて新しいseverityを作成し、80%未満へ戻るとopen alertを閉じます。通知には使用率・空き容量・総容量・workflow URLだけを載せ、VMログ、filesystem path、資格情報、command outputは載せません。手動確認は workflow_dispatch でも実行できます。

インストール済みgatewayは `/usr/local/libexec/azumag-vm-ops/gateway.py` を実行するため、gateway sourceを更新したmainをproductionへdeployしただけでは実行ファイルは切り替わりません。既存のowner-only運用経路でroot-owned installed copyを明示更新し、repo sourceとの一致とproduction statusの新フィールドを確認してから反映済みと扱います。

## Operational notes

### Ubuntu 24.04 の unprivileged user namespace 制限（preview sandbox）

Ubuntu 24.04 は AppArmor の `apparmor_restrict_unprivileged_userns` により unprivileged user
namespace を既定で制限します。この状態で素の `bwrap --unshare-all` を使うと、preview の
`exec` が `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` で失敗します。

対処（installer が実施済み・host 全体設定は変更しない）:

- host-wide の `apparmor_restrict_unprivileged_userns` は `1`（制限）のまま維持します。
- network 隔離を弱める `--share-net` は使いません。gateway は `--unshare-all` を維持します。
- installer が `/usr/bin/bwrap` を `/usr/local/bin/bwrap` に **root 所有・operator group 限定
  （mode 0750）** で複製し、その実体にだけ `userns` を許す AppArmor profile
  `/etc/apparmor.d/usr.local.bin.bwrap` を読み込みます。
- gateway は固定 PATH（`/usr/local/bin:/usr/bin:/bin`）先頭の `/usr/local/bin/bwrap` を先に解決
  するため、profile が付いた複製だけが使われます。一般ユーザーからは 0750 で実行できません。
- `apparmor_parser` が無い環境で userns 制限が有効な場合、installer は fail-closed で停止します。

検証: `aa-status | grep bwrap` に `/usr/local/bin/bwrap` が出ること、`stat -c '%U:%G %a'
/usr/local/bin/bwrap` が `root:<operator-group> 750` であること、preview の
`exec / preview` が正常終了すること。

### 同一 commit の Git bundle 再 upload（idempotent）

同じ commit SHA を production と preview の両方で使うと、GitHub Actions が生成し直した
bundle のバイト列が一致しないことがあります。旧 gateway はこれを弾いていました。

現在の `upload` は次の順で処理し、同じ SHA の再 upload は **idempotent** です:

1. incoming bundle を state 配下の temp file へ書く。
2. `git bundle list-heads` で要求 SHA が advertise されていることを確認する。
3. `git bundle verify` で bundle 自体の整合を確認する。
4. 検証が通ったときだけ、既存 bundle を atomic replace する。

このため、壊れた既存 bundle も有効な再 upload で自己回復します。検証に失敗した incoming は
temp file ごと破棄し、既存 bundle は触りません。

## Current migration note

既存VMの `/home/ubuntu/docich` に tracked差分またはowned submodule差分がある場合、bootstrapは拒否します。VMとrepositoryのどちらを正とするか確認して差分を整理してからbaselineを登録してください。`/home/ubuntu/soren` はbootstrap時に丸ごとsourceへ戻しません。以後、gitlink変更時に変更対象pathだけ旧sourceとの一致を検証して投影するため、既存runtime stateは保持されます。driftを無視して上書きする経路は用意しません。
