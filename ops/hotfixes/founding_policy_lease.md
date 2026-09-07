# 方針installerのkernel lease（docich #96 / soviet_now #194）

`.improve_spawn.lock.lease` の同じ永続inodeをruntimeとinstallerで共有する。
runtimeはディレクトリguard取得/回収/解放中、installerは全policy transaction中に
`flock(EX|NB)` を保持する。通常の90秒TTLを延長するのではなく、長時間のpolicy
更新に別の有効期間を持たせる。leaseファイルは削除・置換・清掃しない。

installerは実行前とkernel lease取得後に、runtime helper全文とshell wrapper部分の
レビュー済みhashを確認する。古いruntime/欠落/未知の差分はfail-closed。
これはディスクの整合性確認であり、既にロード済みshellの証明ではない。

## 今回の依存順序

1. soviet_now #194を検証して統合（source `c025faae892168454512e7dc16b72ba6f22ea431`）。
2. 本番の既存scheduler pauseを保持し、改善PID/guard/lockの不在を確認する。
3. helperを先に配置し、`strategy/improve.sh` はレビューしたwrapper部分だけを
   原子的に置換する。本番独自差分は保持する。別policy更新はこの間実行しない。
4. soren_loopの次試合先頭とimprove_daemonの次監視周期で新moduleの読み込みを
   確認する。配信service/soren_loopは再起動しない。
5. このinstallerを使う。本PRのmerge自体はpolicyを更新しない。#192の訂正を
   今回の古いmanifestへ黙って混ぜない。別のreview済みmanifestで反映する。
6. 更新中の例外は既存transactionの復元範囲で扱う。SIGKILL/電源断の自動rollbackを
   保証しない。途中停止後はbackupと全target hashを検査し、復元/完了までpauseを保持。

owner-only VM gatewayの権限・構成と既存保護設定は変更しない。
同時の手動path削除等、非協調なwriterに対するOSレベルCASではない。

## 検証

installer 11件、実Soren sourceとの相互運用3件。CIはSorenのsource SHAを固定取得し、
両者を実プロセスで実行する。100秒/1日前のguard、保持者SIGKILL、先行spawner、
別inodeへ置換されたguardの解放拒否、例外cleanup、symlink、旧protocol拒否を検査。
Soren側のshell入口8件と既存retry/shadow31件も独立CIで実行する。

実AI/APIや本番strategy.pyの実行はテストで行わない。テスト成功は改善1サイクルの
完走や建国率改善を意味しない。
