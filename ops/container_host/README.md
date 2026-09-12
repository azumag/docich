# container_host

本番VMのコンテナランタイム基盤（Docker Engine + gVisor runsc）を再現可能にする
ディレクトリです。手作業で導入された状態を、宣言的ピン・冪等インストーラ・
読み取り専用ベリファイアで再現します。

## as-builtピン

- Ubuntu 24.04 arm64、Docker CE `5:29.8.0-1~ubuntu.24.04~noble` 一式、
  containerd.io `2.3.5-1~ubuntu.24.04~noble`、
  buildx `0.37.1-1~ubuntu.24.04~noble`、compose `5.5.1-1~ubuntu.24.04~noble`。
- gVisor APT suite `20260907`、runsc `release-20260907.0`（既定platform `systrap`）。
  movingな `release` suiteは再現用には使いません。
- `daemon.json` は `iptables=false` / `ip6tables=false` / `ip-forward=false` +
  `runsc` 登録のみ。
- FW不変条件: DOCKER連動ルール0件、`net.ipv4.ip_forward=0`、
  `net.ipv6.conf.all.forwarding=0`、TCP 2375/2376のlistenなし。
- `ubuntu` ユーザーは `docker` グループに属さない（worker unit の
  `SupplementaryGroups=docker` でのみsocket権限を付与）。

詳細は `docs/operations/container-host-provisioning.md` を参照してください。

## 使い方

```sh
sudo ops/container_host/install_container_host.sh
ops/container_host/verify_container_host.sh
```

`--check` は検証のみ（変更なし）でベリファイアへ委譲します。
インストーラはDocker packageの自動起動より先にレビュー済み `daemon.json` を確定し、
その後 `runsc`、Docker一式の順で導入します。`runsc` は日付固定gVisor APT suiteから導入し、
`runsc --version` がピンと一致しなければ中断します。
`daemon.json` が既存でピンと意味的に異なる場合も、package変更前に中断します。
上書きする場合のみ `FORCE_DAEMON_JSON=1` を明示してください。

## 安全境界

インストーラとベリファイアは次を変更しません。

- ホストのpacket-filterルール（追加・削除なし）
- 公開ポート・host network・TCP Docker socket（2375/2376）
- ユーザーの `docker` グループ所属
- rootless モード設定

導入前後でFW不変条件を自動で照合し、差分があれば中断します。
packet-filterまたはsocket listenerを検査できない場合も、ゼロと推測せずfail-closedで中断します。
