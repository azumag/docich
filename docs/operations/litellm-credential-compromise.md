# LiteLLM 1.107.0 汚染（2026-10-21）の確認と初動

PyPI の `litellm` 1.107.0 が汚染された事象について、docich の影響範囲と、
LiteLLM を使う環境（本番 VM・開発機）を **Python を起動せずに** 確認する手順をまとめる。

## 1. 事象

- 2026-10-21 11:39Z、PyPI に `litellm==1.107.0` が公開された。7 つの wheel すべてに
  `cook.pth` / `cook.py` / `sitecustomize.py` が混入していた。
- `cook.pth` は Python 起動時に `cook.py` を import させ、credential stealer（privcollect）を実行する。
  `litellm` を import しない Python 起動（`pip` 自身を含む）でも発火する。
- `sitecustomize.py` は hourly で dead-drop を読み、取得したコードを実行する loader を起動する。
- 混入は wheel のみで、sdist と git tag には payload が無い。
  **ソースや sdist を確認しても検出できない。**
- PyPI は 2026-10-21 13:23Z に 1.107.0 を削除した。Docker の `latest` / `main-latest` /
  `main-stable` は削除まで汚染イメージを指していた
  （digest `sha256:1bbcb1443bc962e45f5aad53fe3d2c30600e0379715fb6c664cdd3ac126c339e`）。
- 2026-10-21 時点の安全版は `1.106.2`。2026-03 の 1.82.7 / 1.82.8 事件とは別キャンペーン。

## 2. docich の依存状況

docich は `litellm` を Python 依存として持たない。

- `requirements*.txt`、`pyproject.toml`、lockfile、CI workflow、Dockerfile に `litellm` の記載は無い。
- 実行時の関与は loopback の LiteLLM プロキシへの HTTP 呼び出しだけ
  （`src/docich/captions.py`、`strategy/ai.sh` の health 確認）。
- したがって、このリポジトリの CI ランナー・コンテナ・テスト環境が 1.107.0 を踏む経路は無い。
  影響するとすれば、docich が接続する **プロキシ側のホスト** である。

## 3. 判定手順（Python を起動しない）

汚染環境では `python` / `pip` の起動が stealer の再実行トリガーになる。
確認はシェルとファイル操作だけで行う。

```bash
# 起動フック（最重要）
sudo find / -xdev \( -name cook.pth -o -name cook.py \) 2>/dev/null

# 実行痕跡
ls -l /tmp/remote_code_loader.lock /tmp/.pc_once_* 2>/dev/null
printenv PRIVCOLLECT_ACTIVE   # 未設定なら空。値の有無だけを見る

# バージョン（dist-info のディレクトリ名で判定する）
find / -xdev -type d -name 'litellm-*.dist-info' 2>/dev/null
grep -m1 '^Version:' <dist-info>/METADATA

# 実行形態
ps -eo pid,etimes,args | grep -i '[l]itellm'
docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}'
systemctl list-units --all --no-legend | grep -i litellm
```

ネットワーク側は次を確認する。

- `www.litellm.dev`（正規は `litellm.ai`）への POST
- `raw.githubusercontent.com/letters9813/pythonclient-test/main/domain.txt` の取得
- dead-drop が返したドメインの `/codepy` 取得

`/tmp` の marker が無いことは安全の根拠にならない（コンテナや tmp 掃除で消える）。
外向き通信が可能なホストで 1.107.0 を実行した場合は、exfil 成功として扱う。

## 4. 2026-10-21 の実測

読み取り専用の確認のみ行い、production の状態は変更していない。

| 対象 | 結果 |
|---|---|
| docich リポジトリ（requirements / CI / Docker / git 履歴） | `litellm` のインストール経路なし |
| 開発 Mac（system / Homebrew / user site / venv / pipx / uv tool） | `litellm` 未インストール。`cook.pth` / `cook.py` / `/tmp` marker なし |
| 本番 VM | `litellm` のパッケージ・プロセス・コンテナ・systemd unit・起動フックはいずれも無し。port 4100 の listener も無い（プロキシの最終ログ更新は 2026-08-13） |

本番 VM については該当なしと判断した。なお `wiki/Game-Sorengame.md` の
「VM 上の `soren-litellm`（port 4100）」は 2026-08-19 時点のスナップショットであり、
2026-10-21 の実測では稼働していない。AI 経路の現行構成は別途確認する。

## 5. 感染していた場合の初動

1. 影響環境で `python` / `pip` を実行しない。
2. site-packages から `cook.pth` / `cook.py` / `sitecustomize.py` と、
   `__pycache__/cook*` / `__pycache__/sitecustomize*` を削除する。
3. `/tmp/remote_code_loader.lock` を保持する detach プロセスを停止する。
4. `litellm==1.106.2` で venv / コンテナイメージを作り直す（削除だけで済ませない）。
5. 外向き通信が可能だった場合、exfil は成功したものとして扱う。

`/tmp/.pc_once_72129118c59f5636c0bb` は exfil 成功時だけ作られる。
存在しないことは「未感染」の証明にならない。

## 6. rotate チェックリスト

影響ホストから到達できたものはすべて rotate する。

- SSH 秘密鍵、GPG 秘密鍵、TLS 秘密鍵、shell / DB の履歴
- クラウド資格情報（AWS / GCP / Azure / OCI）とインスタンスロール、IMDS 由来の一時資格情報
- Kubernetes config / service account token、Docker config
- GitHub CLI、npm、PyPI（`.pypirc`）、`.netrc`、rclone
- AI CLI / エディタの認証（Claude、Codex、Cursor、Copilot、Gemini など）
- LiteLLM の master key / virtual key、`LiteLLM_CredentialsTable` /
  `LiteLLM_ProxyModelTable` に保存された provider key、`DATABASE_URL`
- デスクトップ環境ならブラウザ保存パスワードと cookie
- AWS を使う場合は CloudTrail で `GetCallerIdentity` の実行を監査する

## 7. 再発防止

- `litellm` を導入する場合も当面は `litellm==1.106.2` に固定し、
  Docker はタグではなく digest で固定する。
- wheel の `RECORD` と起動フック（`.pth` / `sitecustomize`）を確認する。
  sdist や git tag の確認だけで安全と判断しない。
- 依存追加時は lock / digest を CI で検証する。docich は現状 `litellm` 非依存を維持する。

## 参照

- [BerriAI/litellm Issue #47826](https://github.com/BerriAI/litellm/issues/47826)
- [JFrog: LiteLLM Hijacked Again](https://research.jfrog.com/post/litellm-hijacked-again/)
- [Wiz: LiteLLM Compromised](https://www.wiz.io/blog/litellm-compromised-steals-credentials)
- [GMO Flatt Security: LiteLLM パッケージ 再侵害の概要と対策指針](https://blog.flatt.tech/entry/litellm_2nd_compromise)
