# Owner-only VM operations via GitHub Actions

`docich` を VM / 配信運用の唯一の control plane とする GitHub Actions 構成です。
main の本番反映、branch/commit の preview 反映、状態確認、owner command 実行、初回 baseline 登録を扱います。

## Repository boundary

- VM credential、Environment `vm-operations`、SSH gateway、production/preview workflow は **docich だけ**が所有します。
- `soviet_now` 側には VM 用 Environment / Secrets / workflow を置きません。`soviet_now` はゲーム・AI・ロジックの source repository として独立させます。
- 本番で使う `soviet_now` の版は、docich の `games/soviet_now` gitlink が固定します。`soviet_now` main の更新だけでは VM は変わりません。
- docich production deploy は親 commit を更新した後、allowlist 済み submodule を親 gitlink の commit へ同期します。現在の allowlist は `games/soviet_now` と `games/hanjuku-sfc-speedrun` です。
- submodule URL、HEAD、tracked working tree が期待値と違う場合は fail-closed します。

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

親 commit の `games/soviet_now` gitlinkが変わった場合、docich gateway がその固定commitへ submodule を更新します。これにより `soviet_now` 自身はVM資格情報や配信運用を知る必要がありません。

## One-time setup

1. docich の main protection/ruleset を設定します。
2. docich に Environment `vm-operations` を作り、deployment branch を main のみに限定します。
3. VM に `git` と `bubblewrap` があることを確認します。
4. Actions 専用 ed25519 鍵を作ります。

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github-vm-ops -C github-vm-ops-actions
```

5. 公開鍵と `ops/vm_actions/` を既存の信頼済みVM接続経路で置き、gatewayをroot所有で導入します。

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
- **soviet_now更新を本番へ出す**: soviet_now側変更をmainへ入れた後、docichで `games/soviet_now` gitlinkをそのcommitへ更新してPR → docich mainへマージします。
- **branch/commitをVM test areaへ**: `deploy / preview / ref=<branch-or-sha>`。
- **preview command**: 同じrefで `exec / preview`。本番filesystem/networkから隔離されます。
- **production command**: `exec / production / ref=main / confirm=production`。stdout/stderr本文はVM private logだけに保存します。
- **status**: `status / production` または `status / preview`。

## Current migration note

既存VMの `/home/ubuntu/docich` に tracked差分またはowned submodule差分がある場合、bootstrapは拒否します。VMとrepositoryのどちらを正とするか確認して差分を整理してからbaselineを登録してください。driftを無視して上書きする経路は用意しません。
