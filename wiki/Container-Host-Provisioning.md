# コンテナ基盤プロビジョニング

本番 VM のコンテナ実行基盤 (Docker Engine 29.8.0 と gVisor runsc 20260907.0) を、手作業導入からレビュー済み・ピン固定・再現可能 (IaC) にした記録である。一次情報は repo の手順書で、本ページは要約と as-built の要点のみを記す。詳細な手順・全文は複製しない。

一次情報: [docs/operations/container-host-provisioning.md](https://github.com/azumag/docich/blob/main/docs/operations/container-host-provisioning.md)

## 目的

これまで本番 VM に手作業で導入され文章記録しか無かった Docker Engine 29.8.0 と gVisor runsc 20260907.0 を、レビュー済み・ピン固定・再現可能 (IaC) にした。版・APT source・GPG 鍵指紋・設定をコードとして固定し、導入器と検証器で差分を検出できるようにする。

## 構成ファイル

- `ops/container_host/pins.env` — as-built の版・APT source・GPG 鍵指紋・keyring・runsc platform を固定する
- `ops/container_host/install_container_host.sh` — root 必須・冪等・fail-closed のインストーラ
- `ops/container_host/verify_container_host.sh` — read-only の検証器 (`key=value` 形式で出力し `result=ok|drift` で判定する)
- `ops/container_host/README.md` — 上記 3 点の使い方の説明

## as-built (2026-09-13 実測、Ubuntu 24.04 arm64)

- パッケージの版 (APT ピン):
  - `docker-ce` / `docker-ce-cli` / `docker-ce-rootless-extras` は `5:29.8.0-1~ubuntu.24.04~noble`
  - `containerd.io` は `2.3.5-1~ubuntu.24.04~noble`
  - `docker-buildx-plugin` は `0.37.1-1~ubuntu.24.04~noble`
  - `docker-compose-plugin` は `5.5.1-1~ubuntu.24.04~noble`
- Docker APT source は `https://download.docker.com/linux/ubuntu` の noble / stable / arm64 で、鍵指紋は主鍵 `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`、サブ鍵 `D3306A018370199E527AE7997EA0A9C3F273FCD8`
- gVisor APT は `https://storage.googleapis.com/gvisor/releases release main` (arm64) で、鍵指紋は `6F1DF85E3A71C24918E727D56FC6D554E32BD943`、実体は `/usr/bin/runsc`、既定 platform は `systrap` (KVM 追加なし)
- `daemon.json` は `iptables=false` / `ip6tables=false` / `ip-forward=false` と `runsc` ランタイム登録のみを持つ。既定ランタイムは `runc` のまま変えない
- `docker info` の要点は OSType=linux / cgroup v2 / CgroupDriver=systemd / DefaultRuntime=runc / runsc 登録あり / MemoryLimit・PidsLimit・CPUCfsQuota=true
- FW 不変条件は DOCKER 連動 iptables ルール 0 件、TCP 2375/2376 の listen なし、`net.ipv4.ip_forward=0`、`net.ipv6.conf.all.forwarding=0`
- `ubuntu` ユーザーは `docker` グループ非所属 (グループは空)。Docker socket の権限は free-strategy worker の systemd unit の `SupplementaryGroups=docker` のみで付与する

## 再現手順 (要約)

全文は一次情報 ([docs/operations/container-host-provisioning.md](https://github.com/azumag/docich/blob/main/docs/operations/container-host-provisioning.md)) を参照。要点のみ記す。

- 検証のみの場合: `ops/container_host/install_container_host.sh --check` (verifier へ委譲する read-only 動作)
- 導入の場合: `sudo ops/container_host/install_container_host.sh`
- `daemon.json` が既存で意味的に異なる場合は中断する。上書きするのは `FORCE_DAEMON_JSON=1` を明示したときのみ
- gVisor の実行 probe (使い捨て `docker run --runtime=runsc --network=none`) はインストーラの対象外で、[docs/operations/free-strategy-paper-worker.md](https://github.com/azumag/docich/blob/main/docs/operations/free-strategy-paper-worker.md) §2 の別手順として行う

## 安全境界

インストーラと運用が守る境界は次の通り。詳細は一次情報を参照。

- packet-filter (iptables / nft / ufw) のルールを書かない
- ポート公開 (`-p` / `--publish`) と host network を使わない
- TCP Docker socket (2375/2376) を有効化しない
- `docker` グループへ恒久追加しない
- rootless 設定をしない
- インストーラは導入前後で FW 不変条件を照合し、差分があれば中断する

## 実測 (2026-09-13)

- 非 root の `ubuntu` から `sudo -n` 経由で verifier を実行し、全項目一致・`result=ok`・exit 0 を確認した
- CI では `tests/test_container_host_provisioning.py` が契約テストとして走る

## 未確認 / 残件

- インストーラのフル再実行は本番 VM 未実施 (read-only 検証のみ)。初回は `--check` から始める
- ピン版はレビュー時点の as-built で、上流パッケージ差し替え時は再レビューが必要

## 関連

- free-strategy PAPER worker はこの基盤上で動く。worker の unit は repo テンプレート `scripts/systemd/docich-free-strategy-worker.service` を `__DOCICH_ROOT__` 置換して導入する。worker 側の手順は [docs/operations/free-strategy-paper-worker.md](https://github.com/azumag/docich/blob/main/docs/operations/free-strategy-paper-worker.md) を参照
