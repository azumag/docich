# NetHack production canary smoke (P5i)

P5h の Docker/gVisor NetHack workerを production VM で1 episodeだけ実測するための運用境界。

## 権限分離

通常の owner-only VM gateway `exec / production` は `ubuntu` のまま実行し、Docker socketへ直接触れない。

Docker権限は次の systemd oneshot だけに付与する。

```text
docich-nethack-canary-smoke.path
  -> request.json を検知
  -> docich-nethack-canary-smoke.service
       User=ubuntu
       SupplementaryGroups=docker
       -> nethack_canary_production_smoke.py
       -> Docker + runsc
```

`ubuntu` を docker groupへ恒久追加してはならない。runner自身も `/etc/group` のpersistent membershipを検査し、検出時はfail-closedする。

## 初回導入

production VM の canonical checkoutが `/home/ubuntu/docich` にあり、container host provisioningが既に適合していることが前提。

trusted root shellで:

```bash
cd /home/ubuntu/docich
sudo ops/vm_actions/install_nethack_canary_smoke.sh
```

installerは:

- `ops/container_host/verify_container_host.sh` を通す
- request/result spoolを `ubuntu` owner / 0700で作る
- root-owned service/path unitを `/etc/systemd/system` へinstall
- path unitだけをenable/start
- smoke service自体は起動しない
- `User=ubuntu` / `SupplementaryGroups=docker` / finite cgroup limitsを再確認

する。

## 実測

GitHub Actions の **NetHack production canary smoke** を protected `main` から手動実行する。

workflowは:

1. current protected mainを確定
2. production deployが同じSHAへ収束するまで待つ
3. normal production gatewayからbounded requestをspoolへatomic publish
4. path unitがDocker権限付きoneshotを起動
5. oneshotがreviewed main commitのbuild入力だけを `git archive` で一時contextへexportし、そこからNetHack canary imageをimmutable IDでbuild
   - production checkoutは長寿命で、improve daemonのruntime生成物・backup・`._*` などをuntrackedのまま持つ。commit objectからexportすることで、reviewed SHA以外のファイルがimageへ混入しない
   - reviewed host stateはコンテナegressを持たない（`iptables=false` / `ip-forward=false`）が、image buildは `apt` / `curl` / `fetch-lua` にegressが要る。このbuild stepだけ `docker build --network=host` を使う。episode実行containerは常に `--network=none` で、build時のhost networkとは別
6. baseline P3b 1 episodeを `network=none` / `runsc` containerで実行
7. production save/xlog/dump fingerprint不変とcontainer cleanupを確認
8. fixed categoryだけをgateway exit codeとしてActionsへ返す
9. production diagnosticsがcriticalでないことを再確認

する。

production VM上の詳細reportは:

```text
<state_dir>/nethack/canary/p5i-smoke/<request-id>/report.json
```

gateway control resultは:

```text
/home/ubuntu/.local/state/docich/nethack-canary-smoke/results/<request-id>.json
```

にmode 0600で残る。Actionsへraw VM outputやproduction filesystem内容は返さない。

## 正常カテゴリ

初版のaction schemaでは戦闘・door操作などが未実装なので、以下はいずれも「隔離実行経路が成立した」smoke成功として扱う。

- `terminal`: dead / ascended / ended / ended_unknown
- `policy_stall`: reviewed policyが安全に停止
- `turn_limit`: canary max turn到達
- `other_timeout`: worker自身が正規のterminal timeout resultを返した

一方、container launcher timeout、Docker/runsc preflight失敗、production fingerprint変化、container cleanup失敗はsmoke失敗。

## 停止

path triggerを止める場合:

```bash
sudo systemctl disable --now docich-nethack-canary-smoke.path
```

serviceはoneshotなので常駐しない。

## 次

実測でpolicy-stallの種類を確認した後、canary専用 attack / open-door / directional tactical action schemaを別PRで追加する。production P3d executorはこの段階でも変更しない。

Relates to #586.
