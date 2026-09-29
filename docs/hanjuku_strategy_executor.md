# 半熟英雄: 戦略とexecutorの分離

関連: #1353（移行全体）、#1085（既存の非同期調整チャート）。
調査固定点: `079c7f3d7527cb8cb8dfade7961f8341b67cb4ac`。

## 状態: P1、実行制御核のみ。旧botには未接続

`src/docich/hanjuku_execution.py` は、独立した戦略データと出撃executorの
**純粋な状態遷移**を実装する。ゲーム入力、画像認識、モデル呼び出し、ファイル書込み、
resetは行わない。既存 `hanjuku_bot.py` / `hanjuku_policy.py` / game config / VM gateway
を置換しない。本PRをマージしても、本番のゲーム操作は新しいexecutorへ切り替わらない。

実装済みは、厳密な計画JSON、状態不明と実行不可能の区別、出撃コマンドの固定、
入力意図と送信結果と出撃確認の分離、計画の未実行部分の差替え、オフラインテスト。
実画面を操作するdriver、永続化、観測adapter、到着・帰還による将軍予約の解放、
LLM再計画の実接続、終了後の自動コード改善は未実装。これらをP1の完了に含めない。

## 目標設計

戦略は「何をするか」を選び、executorは指定されたコマンドを観測しながら実行する。
戦略の選択をボタン列に埋め込まない。外からはOSとアプリの二層に見せるが、
実装上は観測・実行journal・計画状態も区別する。

- 戦略: 将軍、出撃先、携行札、攻略順、資金配分、回復・撤退・防衛の好み。
- executor: 指定対象の選択、カーソル移動、割込み、中断、実行結果の確認。
- 監督部分: 証拠確定、入力権限、ラン終了・reset、次ランの版選択。

実行中に戦略の残りを組み替えてよい。実行済みの支出や進軍は巻き戻さない。
executorはラン内で固定し、ゲームオーバー／停滞reset後の分析・検証で新版へ改善する。
「executorは永久に不変」ではない。旧botのホットロードをP1で変更するわけでもない。

## 契約と現在位置

| 契約 | 責務 |
| --- | --- |
| `RunIdentity` | runtime / generation / lease。モデルではなくホストが与える |
| `WorldState` | 観測sequence、測定済み城、所有、将軍所在／進軍、札在庫 |
| `StrategyPlan` | 順序付きの戦略ノード。内容ハッシュがrevision |
| `SortieCommand` | run、採用元revision、固定した指示、採用時観測sequence |
| `Session.executions` | 計画と独立した実行journal。計画差替えで消さない |
| `InputIntent` | driverのbounded pad要求。まだ実送信とは扱わない |
| `SortieEvidence` | 観測側が生成する選択／出撃の証拠。run、sequence、対象、frame hashを束ねる |
| `CommandResult` | accepted / blocked / needs_observation / precondition_failed / departure_observed等 |

`WorldState`の欠損は不明であって、在庫0や将軍不在ではない。将軍は
`unknown / at_castle / marching / unavailable` を明示し、`unavailable` は確認済みの死亡・不在・
その他の利用不能を表す。単に一覧から読めなかった場合は `unknown` のままにする。
将軍所在や進軍が不明なら新しい出撃の実行可能とは扱わない。重複札は数量で確認する。
城名は当該章で測定済みの移動能力に含まれる必要がある。
一方「守備を1人残す」「賃金用30Gを残す」はこの制御核に埋め込まない。

観測sequenceだけでは、持ち越した全factの鮮度を証明できない。観測adapterは古いfactを
`unknown`へ落とし、確認済みの否定 (`unavailable` や source lost) と混同しない責任を負う。
frame hashも画像認識の正しさや真正性を単独で証明しない。
`SortieEvidence`はLLMの出力から作らず、測定済みobserverで生成する。

P1の計画形式は出撃ノードの順序列だけ。将来の条件分岐・依存・並行目標・期限・戦術は
P3以降の拡張であり、現在のJSONに未実装の機能を混ぜない。計画を書き換えた後も、
現在位置は「新計画 + 旧指示の固定snapshot + 実行journal + 現在観測」で定義する。

## モデルが生成してよいもの

```json
{
  "schema": 1,
  "orders": [
    {
      "order_id": "attack-2",
      "general": "将軍B",
      "source": "本城",
      "target": "城X",
      "cards": ["札A"]
    }
  ]
}
```

上記の名前は形式説明用の仮名で、実ゲームの校正済み指示ではない。
`parse_plan(text, run)` は未知key、重複key、不正schema、型違い、過大入力を拒否する。
run、revision、ボタン、コード、reset、代役指定などをモデルが注入することはできない。
入力は16KiB、計画は16ノード、携行札は3枚まで。空ordersは新規仕事なしを表すだけで、
実行中コマンドの取消しではない。

## 入力と結果の時系列

1. `begin(session, order_id, world)` は現在の前提を確認して固定コマンドを作る。
   `ready / ACCEPTED_NOT_SENT` は入力送信でも出撃成功でもない。
2. 測定済みdriverは新しい観測からpadを選び、`prepare_input`に渡す。
   `begin`後も、freshな観測が出撃元喪失・将軍の別地点/進軍/利用不能・必要札不足を明示した場合は
   そこでfail-closedにして次のpadを作らない。一方、所有・将軍・在庫が単に不明になっただけなら、
   既に固定したコマンドを自動破棄しない。
   最後の確定は、同じ観測sequence・runの完全一致した選択証拠が必要。
   将軍の代役や携行札の削減を黙って許可しない。
3. **呼出側は返ったSessionを永続化してからInputIntentをwriterへ渡す。**
   このモジュール自体は保存も送信もしない。pending状態で再度入力を発行しない。
4. writerのreceiptは `acknowledge` に渡す。`sent`は送信の結果のみ。
   最終入力なら `awaiting_departure` となり、まだ `departure_observed` ではない。
5. observerが明示的な出撃を確認したときだけ `observe_departure` を呼ぶ。
   run、sequence、指示、最終input_idを照合する。確認画面の選択証拠は出撃証拠に流用できない。
6. 出撃確認でも `goal_completed` は常にfalse。到着、戦闘勝利、占領は別イベントにする。

padはdriver専用の内部portで、モデル出力ではない。1要求は1～4入力、各入力1～500ms。
最終確定要求はA入力1個。driverが誤ったpadを選ばないことまで、この型だけで証明しない。

### 曖昧な送信と停滞

`not_sent`は「一部も送られなかった」と確実に証明できるときだけ。
一部送信、timeout、receipt喪失は`unknown`にする。`uncertain`は入力意図と資源予約を残し、
再送・別コマンド開始を止める。最終確定後の明示的な出撃証拠があれば解決できる。
マップへ戻った、部分的な将軍一覧に名前が無い、別の戦闘に勝った、という事実を
出撃証拠に自動変換してはいけない。

`mark_uncertain`は停滞時の制御核操作であり、停滞検出器やreset実装ではない。
取消しを許すのはdriver入力を一度も発行していない`ready`だけ。
一度動かしたUIを一般的なrollbackで元に戻せるとは仮定しない。

単一writerのロック、永続journalのcodec／破損検査、保存と送信のクラッシュ窓、
受信側のidempotencyはP2で検証する。現在の純粋なSessionだけではプロセス間排他や
exactly-once deliveryを保証しない。dataclassを未検証のディスクJSONから直接復元しない。

## 計画差替え

`replan(session, candidate, expected_revision=...)` はrevisionのcompare-and-swapで、
候補のrunと一致を確認する。発行済みnode IDを候補へ再利用できない。
実行journalと指示snapshotは同じimmutable objectとして維持する。
旧計画から消えた指示も、そのorder IDで再要求すれば既存の状態を返し、再送しない。

例: 将軍Aの出撃確定が未確認のまま、将軍Bによる別攻略案が届いた場合、
新しい計画は保存できるがAの実行は消さない。Aが未確認の間、共有UIへBを出さない。
Aの出撃が確認されればUIは解放するが、A本人は進軍中の予約を維持する。

**P1には到着・帰還に基づく将軍予約解放がまだない。** 一度出撃した将軍は新しい指示に
再利用できない。これは未実装を不明のまま動かさない暫定制限であり、実ゲームに接続する前に
P2で解決する。journal上限128件に達した場合も、古い／未解決の証拠を捨てず明示的に停止する。

## 旧実装との接続時に必要なこと

調査した旧実装では `hanjuku_policy.target_step()` が `_finish_order(..., 'launched')`
を呼んだ後に `[pad('a')]` を返す。そのため `order_launched` 単体を新契約の出撃確認に
変換しない。`brains/hanjuku/bot.py` の `action_plan`、既存writerの `input_sent`、
事後の実観測をjoinし、曖昧なケースは未確認として保持する。

旧実装の `general_override`、`source_override`、携行札補正は戦略判断でもある。
旧driverを丸ごとラップして「分離完了」にしない。代案選択を戦略側へ移したうえで、
新しいコマンドを発行し直す。旧ケースのpad列と状態遷移をまず比較し、意図して変更する差分を
明示する。購入・修理・戦闘のpolicy移行は別の小さな変更にする。

## 後続の改善ループ

#1353にP2～P6を追跡する。完了後の目標は以下。

- 実行中: 現在状態から未実行チャートを再生成。非同期で、操作loopをLLM待ちにしない。
- 終了時: 操作停滞／計画停滞／攻略停滞／game overを区別し、証拠を確定してからresetする。
- 分析: 戦略選択、executor操作、観測・状態管理、両者の接続の問題を分ける。
- 改善: ベースチャートとexecutor両方に修正候補を作る。executorは次ランで採用する。
- 評価: 旧戦略×旧executor、新戦略×旧executor、旧戦略×新executor、両方新版を分けて比較する。

ログ再生で確認できる判断の変化と、実ゲームで勝率が上がることは別。共通の開始条件や複数条件で
実際にゲームを進める隔離評価が必要。本PRはその自動起動や本番採用を実行しない。

## 検証・運用への影響

```sh
PYTHONPATH=src python3 -m pytest -q tests/test_hanjuku_execution.py
python3 -m py_compile src/docich/hanjuku_execution.py
```

テストは合成状態とdriver portの要求を用いる契約テスト。実フレーム・ROM・save stateは含めない。
本番driverのend-to-end実測、既存半熟英雄全回帰、性能改善の測定とは区別する。
新しいworker、queue、provider、モデル、診断権限、VM配布対象、配信・音声変更はない。
将来のP2でruntime接続する際はmanifest／registry／diagnostics／証拠永続化も合わせて確認する。

開発環境にはprimary checkoutの運用正本 `handoff.md` がなく、未読・未更新。
`ops_brief`の再生成・source照合、本番の作業バナー・音声は未実施。
この設計とPR本文を開発の引き継ぎにし、運用正本を推測して作らない。
