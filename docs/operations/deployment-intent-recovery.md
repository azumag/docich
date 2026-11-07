# deployment_intent recovery（deploy 中断時の正規回復手順）

deploy または rebaseline が transaction 途中で失敗すると、gateway は
`current/<repo>.json` に `deployment_intent` を残し、それ以降の全 operation を
`deployment recovery required` で fail-closed にする。`recovery_required: true`
の場合は rollback 自体が完了しておらず、unknown drift が保存されている。

**現状 gateway に `recover` operation は存在しない。** 回復は owner による
intent 除去 → submodule 同期 → 通常 deploy の再実行で行う（docich#3709 で
実施済みの手順を正規化したもの）。

## 手順

1. `gateway status` で `current/<repo>.json` を確認し、
   `deployment_intent`（`from` / `to` / `predeploy_head` / `recovery_required`）、
   `state.sha`、`pending_repairs` を記録する。通常 `to` が意図した着地点。
2. `current/<repo>.json` から `deployment_intent` キーだけを除去する。
   他のキー（`sha`・`projection_base`・`pending_repairs` 等）は触れない。
   state ファイルの手編集が許されるのはこのキー除去のみ。
3. production checkout（`/home/ubuntu/docich`）を `to` へ正規化する:
   `git fetch`（bundle または origin）→ `git reset --hard <to>` →
   `git submodule update --init --checkout` で submodule を gitlink へ
   fast-forward。`git rev-parse HEAD` が `to`、`git status` が clean、
   submodule HEAD が gitlink と一致することを確認する。
4. 通常の Actions 経路で `to` の deploy を再実行し、state を正規に確定させる。
   `status` が configured、`state.sha == to`、`pending_repairs` なしを確認する。

## 注意

- intent 残存中・deploy 実行中に checkout を動かさない（AGENTS §9。
  docich#3709 は deploy 実行中の checkout に由来）。
- `recovery_required: true` の場合、rollback で保存された unknown drift を
  上書きする前に内容を診断・記録する（§9 unknown drift 無条件上書き禁止）。
