# NInvaders 自動戦略改善

## 現象と変更

作業開始時の read-only 本番診断では、過去の NInvaders 改善記録が `kept` でした。
この記録には理由が保存されておらず、診断上は `unknown` でした。現行経路は2つの数値重みだけを提案するため、照準・移動計画・敵弾回避を組み替えられません。

NInvaders 専用の構造方策ループを追加します。ほかのレトロゲームの数値改善経路や共通キューの設定は変更しません。

| 部品 | 場所 | 役割 |
|---|---|---|
| 盤面パーサ | `src/docich/ninvaders/frame.py` | tmux画面を構造化した観測へ変換 |
| 基準方策 | `brains/ninvaders/policy.py` | 改善と安全なフォールバックの出発点 |
| 方策実行 | `src/docich/ninvaders/sandbox.py` | 静的検査後に別プロセスで実行し、返すキーを許可リストへ制限 |
| 試合評価 | `src/docich/ninvaders/arena.py` | 実ゲームをライブと同じパーサ・方策runnerで評価 |
| 版管理 | `src/docich/ninvaders/store.py` | 不変の方策版と原子的な昇格ポインタを保存 |
| 改善処理 | `src/docich/ninvaders/improve.py` | LLM候補、静的・スモーク検査、比較、昇格判断 |
| ライブrunner | `src/docich/ninvaders/player.py` | 次の試合開始時に昇格版を読む |
| 試合wrapper | `games/cli-wrappers/ninvaders_docich.sh` | 試合開始・スコア記録・runnerの後始末 |

改善jobは既存のコーナー単一実行ロックと共有改善レーンを通ります。NInvadersの比較は incumbent と candidate を各6試合、3並列、1試合あたり240秒以内で行います。共通設定の `improve_matches=2` は他ゲームのままです。両評価で十分な試合が成立し、候補のpolicy fault率が2%以下、平均スコアが10%以上向上し、片側置換検定が `p <= 0.10` の場合だけ昇格します。

昇格先は `<state_dir>/resolver/ninvaders/current.json` です。ライブrunnerは試合境界で現行版を再選択します。ポリシーrunnerが起動できない、または停止した場合は固定スイープへ縮退します。agent commandは無効にして入力の競合を防ぎます。adapterは設定済みの `state_dir` をwrapperへ渡すため、改善器とライブrunnerは同じポリシーストアを参照します。

## 生成方策の制約

方策は `decide(obs, state)` を定義し、`Left`、`Right`、`Space` のみを返します。`state` は試合中だけ維持されます。

- AST検査でサイズ、構文木、import、非同期処理、クラス定義を制限します。import可能なのは `math` のみです。
- workerは `python -I`、最小環境、CPU/file size/file descriptorの上限、Linuxで256 MiBのaddress-space上限を使います。各tickは0.4秒で打ち切ります。
- キー出力は信頼側で再検査します。workerが3回再起動しても復帰しないときはライブ側がスイープへ切り替えます。
- 生成コードはプロンプト、診断、公開ログへ出しません。失敗記録は固定 `reason_code` と `phase` のみです。

これはOSレベルの隔離ではありません。workerは同じOSユーザーで動作し、network namespace、seccomp、別UID、コンテナを使いません。AST検査とPython runtimeに未知の脱出経路がないことを証明するものではありません。ホスト権限への耐性が必要な脅威モデルには、この実装だけでは不十分です。

## 診断と確認

`corner_improve_ninvaders.json` は固定enumで改善結果を残します。主な理由コードは `policy-promoted`、`policy-incomplete`、`policy-faults`、`policy-below-margin`、`policy-not-significant`、`policy-identical`、`policy-invalid`、`policy-eval` です。collectorも同じallowlistを使います。例外本文、prompt、生成コードはcollectorへ渡しません。

関連テスト:

```sh
python3 -m pytest -q tests/test_ninvaders_arena.py tests/test_ninvaders_frame.py tests/test_ninvaders_improve.py tests/test_ninvaders_nudge.py tests/test_ninvaders_player.py tests/test_ninvaders_policy.py tests/test_ninvaders_sandbox.py tests/test_ninvaders_store.py tests/test_ninvaders_wrapper.py tests/test_ninvaders_wrapper_policy.py tests/test_corner_improve.py tests/test_corner_improve_dispatch.py tests/test_retro_corner.py ops/vm_actions/tests/test_rotation_release_evidence.py
```

ローカルのテスト通過だけでは本番受入れとしません。本番では自然に実行されたNInvaders改善について、完了status・理由コード、履歴へのcandidate/baseline評価、`current.json` の更新、次試合の `match start policy=<sha> origin=promoted` と実際のスコア記録を別々に確認します。手動の試合開始や改善job起動では証明しません。

昇格版を取り消す場合は `PolicyStore(<state_dir>/resolver/ninvaders).rollback()` を呼び、次試合から親版（親がなければtracked基準方策）を使います。入力方式を切り戻す場合はwrapper引数を `brain` に戻し、同時に `[agent].enabled = true` にします。固定スイープはwrapper引数を省略します。
