# container-host プロビジョニング手順

本番VM（Ubuntu 24.04 arm64）のコンテナランタイム基盤
（Docker Engine + gVisor runsc）を再現する手順です。
手作業導入の記録ではなく、レビュー済みas-builtを再現する宣言的スクリプトが正本です。

- ピン: `ops/container_host/pins.env`
- インストーラ: `ops/container_host/install_container_host.sh`
- ベリファイア: `ops/container_host/verify_container_host.sh`

## as-builtピン（2026-09-13実測）

- OS: Ubuntu 24.04.4 LTS、arch arm64。
- docker-ce `5:29.8.0-1~ubuntu.24.04~noble`
- docker-ce-cli `5:29.8.0-1~ubuntu.24.04~noble`
- docker-ce-rootless-extras `5:29.8.0-1~ubuntu.24.04~noble`
- containerd.io `2.3.5-1~ubuntu.24.04~noble`
- docker-buildx-plugin `0.37.1-1~ubuntu.24.04~noble`
- docker-compose-plugin `5.5.1-1~ubuntu.24.04~noble`
- Docker APT鍵指紋: `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`、
  `D3306A018370199E527AE7997EA0A9C3F273FCD8`
- gVisor APT鍵指紋: `6F1DF85E3A71C24918E727D56FC6D554E32BD943`
- gVisor APT suite: `20260907`（movingな `release` suite は再現用に使わない）
- runsc `release-20260907.0`、`/usr/bin/runsc`、既定platform `systrap`
- `daemon.json` は `{"ip-forward": false, "ip6tables": false, "iptables": false,
  "runtimes": {"runsc": {"path": "/usr/bin/runsc"}}}` と意味的に等価
- `docker info`: OSType=linux、CgroupVersion=2、CgroupDriver=systemd、
  DefaultRuntime=runc、Runtimesにrunsc、MemoryLimit/PidsLimit/CPUCfsQuota=true
- FW不変条件: DOCKER連動iptablesルール0件、TCP 2375/2376のlistenなし、
  `net.ipv4.ip_forward=0`、`net.ipv6.conf.all.forwarding=0`、UFWなし
- `ubuntu` は `docker` グループに属さない（`docker` グループは空）。
  free-strategy workerのsocket権限はunitの `SupplementaryGroups=docker` のみ。

## 手順

```sh
sudo ops/container_host/install_container_host.sh
ops/container_host/verify_container_host.sh
```

1. インストーラはUbuntu 24.04 arm64以外では中断します。
2. 変更前にFWベースライン（DOCKERルール数、転送sysctl、2375/2376 listen）を
   取得し、レビュー済み不変条件に反していれば中断します。iptables系または
   socket listenerの検査手段が無い場合も、安全側で中断します。
3. Docker公式APT鍵・gVisorアーカイブ鍵を指紋検証のうえ登録し、
   `docker.sources` / `gvisor.list` をピン内容で書きます。gVisorは日付固定suite
   `20260907` から `runsc` パッケージを導入し、導入後の `runsc --version` が
   `release-20260907.0` と一致しなければ中断します。
4. Docker/containerd/buildx/composeもピン版を導入します。
5. `daemon.json` が無い場合のみピン内容を書き込みます。既存で意味的に異なる
   場合は中断します（`FORCE_DAEMON_JSON=1` でのみ明示上書き）。
6. `docker.service` / `containerd.service` をenable+startし、
   daemon.jsonかパッケージが変わった場合のみ `docker.service` をrestartします。
7. 最後にベリファイアを実行し、FW不変条件の再照合も行います。

検証のみ行う場合:

```sh
ops/container_host/install_container_host.sh --check
```

## 安全境界

- packet-filter（iptables/nft/ufw）ルールを書かない・変えない。
- packet-filterやsocket listenerを検査できない状態を「ルールなし」とみなさない。
- コンテナのポート公開（`-p`/`--publish`）・host networkを使わない。
- TCP Docker socket（2375/2376）を有効化しない。
- ユーザーを `docker` グループへ追加しない。rootless設定も行わない。
- free-strategy workerの手順は `docs/operations/free-strategy-paper-worker.md`
  を参照（本基盤のうえで動くPAPER専用worker）。

## 検証

`ops/container_host/verify_container_host.sh` は read-only で次を確認し、
`key=value` を出力したあと `result=ok` / `result=drift` を出します。
1項目でも不一致、または安全境界を検査できない場合は非0で終了します。

- OS、Docker/containerd/buildx/composeの各パッケージ版がピン一致
- `runsc` パッケージが導入済みで、`runsc --version` が `release-20260907.0`
- `docker info`: OSType=linux、cgroup v2、DefaultRuntime=runc、runsc登録、
  MemoryLimit/PidsLimit/CPUCfsQuota=true
- `daemon.json` がピンと意味的に等価
- `docker.sources` / `gvisor.list` がピン内容と完全一致
- DOCKER連動iptablesルール0件、`net.ipv4.ip_forward=0`、
  `net.ipv6.conf.all.forwarding=0`、TCP 2375/2376 listenなし
- `ubuntu` が `docker` グループに属さない

2026-09-13に本番VMで `ubuntu` 権限（`sudo -n` 経由の特権読み取り）から旧ベリファイアを実行し、
当時のas-builtが全項目一致・`result=ok`・exit 0 であることを確認済みです。本修正で追加した
APT source suite固定・runscパッケージ導入契約・検査不能時fail-closedは、マージ前にCIで検証し、
本番ではまず `--check` で再確認します。

## 未確認・残件

- インストーラのフル再実行は本番VMで未実施。初回の実ホスト再実行は
  `--check`（検証のみ）から行うこと。
- ピン版のDocker/gVisorはレビュー時点のas-builtである。上流が日付suite内の
  パッケージを差し替えた場合でも、`runsc --version` の不一致でfail-closedとなる。
- gVisor実行probe（使い捨て `docker run --runtime=runsc`）は本インストーラの
  対象外。`docs/operations/free-strategy-paper-worker.md` §2 の手順で別途行う。
  これによりインストーラ自身はコンテナを作成しない。
