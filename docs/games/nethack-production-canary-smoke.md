# NetHack production canary smoke (P5i)

P5h の isolated Docker/runsc worker を production VM で1 episodeだけ実測する運用経路。

## 権限境界

通常のVM gateway `exec` はDocker socketを使わない。`ubuntu` ユーザーもdocker groupへ恒久所属させない。

Docker権限はsystem-scopeの:

```text
docich-nethack-canary-smoke.service
```

だけが `SupplementaryGroups=docker` で受け取る。unitはoneshotでenableしない。

常駐するのは:

```text
docich-nethack-canary-smoke.path
```

だけで、review済みmainの `ops/vm_actions/nethack_canary_smoke_epoch` が変わった時だけoneshotを起動する。

## 初回install

P5iがmainへ入ってproduction deploy済みになった後、container hostを管理できるrootセッションで1回だけ:

```bash
cd /home/ubuntu/docich
sudo ops/container_host/install_nethack_canary_smoke_units.sh
```

installerは:

- root必須
- Docker service active必須
- `docker` group必須
- `ubuntu` がdocker groupに恒久所属していたら拒否
- rendered unitを `systemd-analyze verify`
- service/pathをroot:root 0644でinstall
- path unitだけenable/start
- serviceがinstall直後にactiveなら失敗

する。

## 発火

smokeを実行したい時だけepochをmainで更新する。

例:

```text
0 -> 1
```

このmain pushをトリガーに `NetHack production canary smoke` workflow が起動する。

workflowはowner ID / triggering actor / protected main / `vm-operations` environmentを固定し、production deployがそのexact SHAへ到達するまで待つ。

PathChanged serviceはgit checkout途中に起動し得るため、`ExecStartPre` がepochを含むsecurity-relevant path群について:

- working tree diffなし
- staged diffなし
- 同じHEADを連続観測

になるまで待つ。途中checkoutのPython/Dockerfileは実行しない。

## Reviewed build context only

production smokeはrepoディレクトリをそのまま `docker build` contextへ渡さない。

```text
git archive HEAD containers/nethack-canary src brains
```

からtemporary contextを作る。そのためproduction checkoutにuntracked secret/fileが存在してもDocker daemonへ送られない。

image buildは公式NetHack sourceやAPT packagesを取得するためbuild段階のみ `--network=host` を使う。candidate/game runtimeはP5hの `--network=none` 固定。

build後は `--iidfile` のimmutable `sha256:` image IDをP5h launcherへ渡し、runsc/image provenance preflightを再実行する。

## 1 episode smoke

P5iはcandidate比較を行わず、まずbaseline armだけを実行する。

```text
controller = baseline_p3b
max_turns = 250
seed = null
isolation_mode = container
```

成功条件は攻略成功ではない。現行P3bは戦闘等でstallし得るため `terminal_status=timeout` も正常なsmoke結果になり得る。

ただし以下は失敗:

- worker error
- container/runsc preflight失敗
- arena attestation不一致
- production state touched
- unexpected action source
- NetHack processがxlogなしで異常終了
- production NetHackが途中で起動
- production save/xlog/dump fingerprint変化

## Production persistence guard

smoke開始前にproduction NetHackがactiveならimage buildすら開始しない。

image build前のproduction:

- save_dir
- xlogfile
- dump_dir

fingerprintを保持し、image build後とepisode終了後に一致を確認する。途中で通常NetHack cornerが始まった場合も中止する。

## Result

VM private:

```text
<state_dir>/nethack/production-smoke/result.json
```

にbounded resultを保存する。build logやarena pathはworkflowへ返さない。

production gatewayはstdout本文を公開しないため、workflowは:

```text
nethack_canary_smoke_verify.py
```

のexit codeだけを読む。

- `0`: exact SHA+epochのsmoke成功
- `3`: pending / 別run
- `4`: exact runが失敗
- `5`: system units未install

GitHub側にはreviewed SHA / epoch / verifiedの安全なsummaryだけ残す。

## 実行順

1. P5i merge/deploy
2. root installerを1回実行
3. path unit active / oneshot inactiveを確認
4. epochをmain PRで更新
5. production deploy
6. path unitがrunsc smokeを実行
7. workflowがread-only verifierをpoll
8. production diagnosticsがcriticalでないことを確認

## 次

P5jではこの実測経路を使って、canary専用のattack/open-door/direction schemaを追加し、`policy_stall:*` の主要原因を減らす。その後にbaseline/candidateを複数episodeで比較する。
