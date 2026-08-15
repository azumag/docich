# soren game (Unity WebGL)

soren game は docich の `browser` アダプタで動かす。**soren 本体 (AI 戦略・自動プレイ・配信
システム) の Linux 移植そのものは docich の管轄ではない**(移植計画は下記関連ドキュメント)。
docich が担うのは「ブラウザを起動し、その画面を Xvfb 上に映して ffmpeg で配信する」という
表示の場の提供のみである。

## 本番 soren との関係 (重要)

**この VM では soren (sorengame) の本番システムが既に稼働している** (docich とは別に、
system `soren-runtime.service` として、ディスプレイ `:99`・`soren_null` PulseAudio sink で
FFmpeg 直接配信 + ネイティブ Twitch 字幕を運用中)。docich の `browser`
アダプタは、docich 自身のディスプレイ `:98` 上に同じゲームを表示する**別インスタンス**で
あり、稼働中の本番配信とは完全に独立している。`bin/docich start sorengame` などの docich
側の操作が、本番 soren のプロセス・配信に影響することはない。

## サブモジュール: games/soviet_now

sorengame 本体の実装 (Unity WebGL 自動プレイ、`soviet_local.mjs` / `strategy.py` / `soren91`
等) は git submodule `games/soviet_now` (`azumag/soviet_now`, main 追跡) として取り込まれて
いる。

`games/soviet_now` では Codex による OBS → ffmpeg 直結配信への移行が完了し、本番は
`soren-runtime.service` として FFmpeg 直接配信 + ネイティブ Twitch 字幕で稼働している。
**Codex による改善は引き続き進行中のため、docich 側からは読み取り専用として扱い、変更を
加えない。** main の毎時監視 (Routine) で新規コミット
の有無を確認し、変化があれば内容を評価したうえでサブモジュールポインタを進める運用に
なっている。現状把握・共通部品化ロードマップ (C0〜C4) の詳細は
[docs/multi_repo_plan.md](https://github.com/azumag/docich/blob/main/docs/multi_repo_plan.md)
§2・§4 を参照。

## 運転形態: viewer 専用 (現状)

**docich の `sorengame` 定義は viewer 専用である。** `browser` アダプタが chromium を
kiosk 起動し、同一 VM の Soren 本番が公開するローカル WebGL viewer
(`http://127.0.0.1:8080`) を表示して画面を docich の配信 (`:98`) に乗せるだけで、
`[agent] enabled = false` に固定されている。

```bash
bin/docich up
bin/docich start sorengame
```

本番運転 (Playwright/CDP による自動プレイ・戦略 AI・配信) は `start_all.sh` を含めて
soviet_now が所有し、`soren-runtime.service` として `:99` で稼働し続ける。`start_all.sh`
は browser アダプタ契約の対象外であり、docich はこれを呼ばない。

本番運転を docich へ移管することは**将来の別 cutover**として扱う (game 専用の Soren
エントリポイントとロールバック証明が前提。詳細: `handoff.md`「Remaining gates」、
`docs/multi_repo_plan.md` §5)。将来の cutover 後は `[browser] launch_command` をその
game 専用エントリポイントに差し替える想定だが、現時点では未達で、上記の viewer 専用構成の
ままである。

## 設定の要点

`config/games/sorengame.toml` の既定 `url` は `http://127.0.0.1:8080`
(同一 VM の Soren 本番が公開するローカル WebGL viewer) で、差し替えなしにそのまま
viewer 専用として動く。`binary = "auto"` は `chromium` /
`chromium-browser` / `google-chrome` / playwright の chrome を順に自動検出する。
`scripts/setup_ubuntu_arm.sh` は既定では chromium を導入しないため、別途用意が必要
(選択肢: snap 版 chromium の導入、または soren の Playwright chromium
(`~/.cache/ms-playwright/`) を `[browser] binary` に指定して共用する)。

## 関連ドキュメント

- [docs/games/sorengame.md](https://github.com/azumag/docich/blob/main/docs/games/sorengame.md) — 本ページの詳細版
- [docs/soren_linux_migration_plan.md](https://github.com/azumag/docich/blob/main/docs/soren_linux_migration_plan.md) — soren 本体の Linux 移植計画・リスク
- [docs/oracle_arm_setup_guide.md](https://github.com/azumag/docich/blob/main/docs/oracle_arm_setup_guide.md) — 移植先 VM (Oracle Ampere A1) の構築手順
- [[アーキテクチャ|Architecture]] §4.3 相当 (`docs/architecture.md`) — docich 側のアダプタ設計・共存原則
