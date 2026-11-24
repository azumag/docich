# 既設PAPER専用サービスのAI環境ファイル

関連: [#2083](https://github.com/azumag/docich/issues/2083)。

`docich-paper-corner.service` の古い手置き定義には、現行テンプレートの
`EnvironmentFile=-%h/.config/docich/paper-ai.env` がない場合がある。
rotation経由だけでなく専用timer経由でも同じ任意のcapability fileを参照できるよう、
正規deployの `ops/vm_actions/ensure_corner_rotation_timer.sh` で設定を補う。
PAPERの実実行経路をこの文書だけから確定したとは扱わない。

## 配置契約

既設のregular file `docich-paper-corner.service` に対してのみ、user unit directoryの
`docich-paper-corner.service.d/50-docich-paper-ai.conf` を原子的に配置する。
内容は次の2行で、capability file自体を読んだり作ったりはしない。

```ini
[Service]
EnvironmentFile=-%h/.config/docich/paper-ai.env
```

- service本体、他のdrop-in、timerの定義・enable状態・mask、ユーザーの休止stateは変更しない。
  専用serviceが未配置なら何も追加せず、`/dev/null` へのmaskも保持する。
- managed drop-inの既存内容が同じなら再配置しない。内容の不一致、想定外のsymlink、
  不正なファイル種別はexit 42で拒否し、未知のローカル設定を上書きしない。
- 既存のrotation deploy処理による `daemon-reload` で設定が読み直される。
  専用PAPERのstart/restart/enable/unmaskは実行しない。進行中のtickの環境は変更せず、
  設定が使われるのは次の起動から。共通配信・音声の再起動も追加しない。
- unit directoryは既存hookどおり `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user`。
  capability fileの場所は既存テンプレートどおり `%h/.config/docich/paper-ai.env` である。
  ファイルがない場合はoptional指定により従来経路を維持し、AIを有効化しない。

## 本番での受入（コードの単体テストとは別）

レビュー・CI・main統合後の正規Actions/gateway経路で配備する。手動でこのhookを流す
手順へ置き換えない。専用serviceが稼働中でも、この修正だけを理由に再起動しない。

承認済みの読み取り経路から、既設の専用PAPER unitとrotationの現行名・legacy名について
`FragmentPath` / `DropInPaths` / `EnvironmentFiles` を確認する。
`paper-ai.env` の本文や `Environment` の全値をログやIssueへ出さない。
別のdrop-inが後から設定をリセットしていないこと、実際の次回tickがどのunitを経由したかは
本番の読み取り記録で別途照合する。mask/未配置を「AI環境反映済み」と扱わない。

その照合後、既存の承認済み `paper_ai_canary` → `paper_ai_enable` の手順へ進む。
このコード変更やCI成功だけで、canary成功・AI有効化・本番での利用を主張しない。
コミットのrevertだけでは既設drop-inは削除されない。撤去が必要なら、managedファイルの
一致を確認する限定的な変更を別途レビューし、任意のdrop-inをまとめて削除しない。

## 回帰検証

`python3 -m unittest discover -s ops/vm_actions/tests -p 'test_paper_corner_environment.py' -v`

実際のdeploy hookを隔離したHOME/設定ディレクトリで実行し、strictなsystemctl stubで
専用PAPERへの操作を拒否する。旧rotation、移行後、epoch分岐、再実行、未配置、mask、
リンク、設定衝突、原子的配置の失敗、reload失敗、XDG設定を検証する。
rotationのtemplateとmigration helperはこのテストではfixtureであり、移行本体の検証は
既存 `test_corner_rotation_migration.py` に委ねる。systemdが利用可能なら静的verifyも行う。
本番のプロセス継続や実効環境を実測するテストではない。
