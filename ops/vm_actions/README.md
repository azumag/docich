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

親 commit の `games/soviet_now` gitlinkが変わった場合、docich gateway がその固定commitへ submodule を更新し、旧commit→新commitで差分になったregular tracked fileだけを `/home/ubuntu/soren` へ原子的に投影します。無関係なruntime fileや、source側で変更されていない `strategy.py` 等は触りません。source側で変更されたpathがlive側で独自変更されていればdriftとして拒否します。複数fileの途中失敗時は、それまでの投影とdocich親HEADを旧状態へ戻します。

これにより `soviet_now` 自身はVM資格情報や配信運用を知る必要がなく、`soviet_now main` の更新だけでは本番は変わりません。docichのgitlink bumpをレビューしてmainへ入れた時だけlive Sorenへの投影対象になります。

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

7. 初回だけ Actions → **VM operations** で `bootstrap / production / ref=main / confirm=production` を実行します。bootstrap は本番コードを変更しません。

## Daily use

- **docich main merge → production**: main push で最新mainを本番へ反映します。
- **soviet_now更新を本番へ出す**: soviet_now側変更をmainへ入れた後、docichで `games/soviet_now` gitlinkをそのcommitへ更新してPR → docich mainへマージします。docich gatewayがgitlink差分だけをlive Sorenへ安全に投影します。
- **branch/commitをVM test areaへ**: `deploy / preview / ref=<branch-or-sha>`。
- **preview command**: 同じrefで `exec / preview`。本番filesystem/networkから隔離されます。
- **production command**: `exec / production / ref=main / confirm=production`。stdout/stderr本文はVM private logだけに保存します。
- **status**: `status / production` または `status / preview`。

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
