# IMPLEMENT-5725

## 目的

docich#5725「ソ連ゲーム issue発行」に対応し、soviet_now で次の条件を満たす配置だけを issue 起票用の証拠として記録できるようにした。

- 実際に着地したピースの上端がデッドラインを超えている。
- 直前の盤面に、別のドロップ X でデッドライン内へ置ける安全な代替配置、または実用上の同 type 併合代替が存在した。
- 代替配置がない場合は issue の指示どおり対象外とする。

## 調査結果

- `games/soviet_now` はこの worktree では空の gitlink で、ピン止め先は `fafcf8be02d2ffbb04f7a2e8784beeb71a29f3f2`。本体は submodule `azumag/soviet_now` 側にある。
- soviet_now 側の配置モデルは、同 type ピース 2 個の接触で併合進歩し、プレイヤーはドロップ X のみ指定する。デッドライン超過がゲームオーバー条件である。
- ピン止めコミットの `deadline_misplacement_monitor.py` は、履歴スナップショットから実際に追加されたピースを検出し、物理的に着床している場合だけデッドライン超過を評価する。イベントには `prev_pieces` / `curr_pieces` が入り、安全な下位代替や同 type 併合代替の有無も記録される。
- 既存の issue 起票パターンは `src/docich/hanjuku/month_audit.py` で、runtime 配下の JSONL incident を封印し、fingerprint dedupe、occurrence comment、secret scan、detached `gh` filer を行う。`fault_issue` もこの pipeline を再利用している。

## 実装内容

### 1. Soren 用監査スキャナを追加

`src/docich/soren_deadline_audit.py` を新設した。

主な機能:

- soviet_now の `logs/deadline_misplacement_monitor.jsonl` を bounded tail で読む。
- `kind=deadline_misplacement_check` かつ `status=inappropriate` のイベントだけを候補にする。
- スナップショット付きイベントについては、docich 側で独立に geometry を再評価する。
  - 追加ピースが床または既存ピースに物理接触していること。
  - 追加ピースの実測 top が `DEADLINE_Y=3.38` を超えること。
  - 別 X にデッドライン内の着地候補がある、または同 type 併合代替が実測 top を改善すること。
- 上記を満たさないイベント、落下途中の transient snapshot、`status=appropriate`、代替なしのイベントは記録しない。
- soviet_now#943 以前のスナップショットなし legacy event は、monitor 自身の `best_lower_alternative` / `merge_alternative_improves_top` がある場合だけ `monitor_status` として扱う。スナップショット付きイベントにこの fallback は使わない。
- 同一イベントの再記録を防ぐため、書き込み時刻 `ts` を除いたイベント内容ハッシュで冪等化する。これにより monitor の bootstrap再 scan で `ts` だけ変わった重複行を再発行しない。
- 証拠 incident には、実着地ピース、選択位置の独立推定、最良の安全代替、併合代替、候補数、検証方式、正規化した前後盤面を含める。
- 状態は `soren_deadline_audit.json` に bounded 保存する。

### 2. 既存 audit pipeline を再利用

新しい外部送信や独自 issue 形式は追加していない。

- incident の封印には `month_audit.record_incident()` を使う。
- 任意の起票は `month_audit.spawn_issue_filer()` を使う。scanner 本体は既定では証拠記録のみで、`spawn=True` または CLI `--spawn` のときだけ既存 detached filer を起動する。
- fingerprint は `soren_deadline_misplacement` 固定で、再発は同じ issue への occurrence comment として扱える。incident_id はイベントごとに一意である。

CLI 例:

```bash
python3 -m docich.soren_deadline_audit --soren-root /home/ubuntu/soren scan
python3 -m docich.soren_deadline_audit --soren-root /home/ubuntu/soren scan --spawn
python3 -m docich.soren_deadline_audit --soren-root /home/ubuntu/soren scan --dry-run
python3 -m docich.soren_deadline_audit --soren-root /home/ubuntu/soren watch --interval 60
```

既定の入力・出力:

- 入力: `<soren_root>/logs/deadline_misplacement_monitor.jsonl`
- 出力: `<soren_root>/tmp/state/soren_deadline_audit/`

### 3. issue 本文・再発コメントを Soren 用に追加

`src/docich/hanjuku/month_audit.py` に `soren_deadline_misplacement` の表示だけを追加した。

- issue title は `[ソ連ゲーム] 安全な配置先があるのにデッドライン超過へドロップ (...)`。
- 本文は半熟英雄の月次・出撃表ではなく、ドロップ X、piece type、実着地 top、deadline、独立推定、安全代替、併合代替、盤面証拠を示す専用レイアウト。
- 再発コメントも同じ条件を短く表示する。

## 変更ファイル

- `src/docich/soren_deadline_audit.py` — 新規。検出確認、証拠 incident 化、JSONL scan、冪等 state、CLI。
- `src/docich/hanjuku/month_audit.py` — Soren incident の title / 本文 / 再発コメント renderer を追加。
- `tests/test_soren_deadline_audit.py` — 新規。検出条件、代替なし除外、落下途中除外、scan 冪等性、legacy fallback、既存 filer 再利用、dry-run をカバー。
- `IMPLEMENT-5725.md` — 本報告。

## テスト

追加した対象テスト:

- 安全な床代替があるデッドライン超過を `inappropriate` と判定する。
- 全候補がデッドライン超過で代替がない局面を `appropriate` とし、incident にしない。
- 物理支持のない空中スナップショットを除外する。
- scan が証拠 incident を 1 件だけ書き、再 scan で重複にしない。
- snapshot 付きイベントは independent geometry で false positive を弾く。
- snapshot なし legacy event は安全代替の宣言がある場合だけ `monitor_status` で記録する。
- `spawn=True` が既存 `month_audit.spawn_issue_filer` を使う。
- dry-run が state / incident を書かない。

実行状況:

- この環境では shell 実行が拒否され、pytest / compileall / Python 構文確認コマンドを実行できなかった。したがってローカルでの pytest 実行結果はない。
- 実行を試みた代表コマンドは `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider tests/test_soren_deadline_audit.py -q`。pytest の有無を確認する `python3 --version && command -v pytest` も実行前に拒否された。
- そのため検品ゲート側で少なくとも `tests/test_soren_deadline_audit.py` の実行を依頼したい。既存パターンに従い、フルスイートは CI 任せとする。

## BOT_VERSION 採番判断

採番不要。

理由: 今回変更したのは Soren 監査 scanner と audit issue の renderer であり、`BOT_VERSION` が識別する半熟英雄の操作 policy / 判定ロジックは変更していない。`ops/vm_actions/collect_diagnostics.py` の bot_version allowlist や固定値テストを更新する対象はない。

## その他の確認メモ

- handoff 正本の参照確認を試みたが、この環境では参照コマンドが拒否されたため未読。内容を推測して記載はしていない。
- 要求どおり commit / push / PR 作成は行っていない。
- `games/soviet_now` submodule 本体は変更していない。soviet_now が既に出力する monitor event を docich 側で検証・証拠化する実装である。
