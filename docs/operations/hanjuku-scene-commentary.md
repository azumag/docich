# 半熟英雄の場面実況

固定テンプレートの投入を停止し、確認できた観測事実から短い実況を生成する。
ゲームの入力処理は生成・音声再生を待たない。材料不足、生成失敗、古い場面では無音にする。
固定文へのfallbackは用意しない。

## 設定と更新

`config/games/hanjuku-hero.toml` の `[hanjuku.narration].enabled=false` は旧固定文の配送を止める。
未配送の旧terminal recapも、配送retry入口で実設定を読み直して停止する。
一方、場面生成は `[hanjuku.scene_commentary].enabled` で独立に制御する。

新しいbotコマンドprocessは観測ごとにproducer/launcherを読み込み、設定も毎回読む。
workerは1件処理したら終了するため、次の生成は配備済みの実装を読み込む。
既に動いている一回workerは開始時のコードを使うが、enqueue前に設定と現場面を確認する。
新設定を無効にしても、共通audio workerや配信を再起動せず、再生開始済みの音声は完走させる。

## 境界

| 担当 | 契約 |
| --- | --- |
| `brains/hanjuku/bot.py` | 決定記録と同時に型を限定したfactを保存し、nonblocking lockが取れた時だけ一回workerを起動する。生成・bootstrap・audio I/Oを入力processで待たない。 |
| `hanjuku_scene.py` | runtime/世代/lease、場面、fact最大3件、元の観測期限をatomic sidecarに保存する。同一factで期限を延長しない。 |
| `hanjuku_scene_worker.py` | 1回の生成、出力検証、既存の配送関数への受渡しを行う。失敗した同じeventも再生成しない。 |
| `hanjuku_scene_process.py` | 検証済みの専用workerだけをLinux subreaperにする。今回jobの子孫をnamespace PID・starttime・pidfdで識別し、別process group/sessionになった子も期限後に終了・回収する。 |
| `hanjuku_scene_generate.sh` | 既存Soren bootstrapと正規 `ai_generate_list` を呼ぶ。provider・model・schedulerを実装しない。 |
| `hanjuku_narration.deliver_scene` | 直前の場面/実設定を再確認し、従来のfence付き `enqueue_audio_text` へ渡す。音声は既存共有queueが担当する。 |

専用worker以外でsubreaperを有効化しない。既存の子を持つprocessは新jobを開始しない。
Linux procfs/pidfd/subreaperが使えなければ `generate_failed / unavailable` とし、生成を開始しない。
ゲーム入力には例外を伝播させず、固定本文も作らない。

## 正規AI呼出しと予算

Sorenの既存 `eloop_lib.sh` をsourceし、現在のoperator設定とpolicy overlayを読み込む。
sourceの付随stdout/stderrは生成本文に混ぜない。
roleは既存の `BATCH_COMMENTARY_AGENTS`、空なら `RADIO_AGENTS`、さらに空なら
`AI_COMMON_AGENTS` をそのまま使用する。モデル名・認証情報・追加providerを設定しない。

通常のgame-only切替がpauseするのはimprove・prediction・watchdog・soren_loopの4つで、
radio/audioの `.paused` は作らない。後者はdurable operator stopなので、空ファイルを含めて
存在すれば場面生成を止め、本文や存在状態を変更しない。全体stop・explore・streaming停止も尊重する。
共有 `ai_generate_list` は半熟activeを理由に拒否しない。通常radio scheduler専用の
`RADIO_GENERATION_ENABLED` は時刻／新試合による番組生成の設定であり、既存batchと同様、
この直接entryには適用しない。半熟の生成停止は `scene_commentary.enabled=false` で行う。

`ai_generate_list 'RADIO:hanjuku-commentary' ...` が既存radio laneの取得・解放を担当する。
helperは別slotを取らず、共有backoff・改善待ち・provider lockの既存判定を利用する。
今回のqueue ownerは短命shellのPIDとし、終了後は既存のdead-owner回収に任せる。
共有queueやlockをdocich側から削除しない。

生成開始の間隔は最短25秒、元の観測の有効期限は最長20秒、生成は最長15秒。
enqueueのために少なくとも2秒を残すよう生成時間を短くする。
既に短いoperatorの待ち時間は延長せず、改善待ちの0秒も維持する。
生成終了・timeout後はこのjobの子孫だけをTERM、必要ならKILLし、回収を確認する。
この間もゲーム入力processは待機しない。

参照した正規契約はSoren
[`9e35f600b4a3938d7272598a7fa792e405b9c33e`](https://github.com/azumag/soviet_now/tree/9e35f600b4a3938d7272598a7fa792e405b9c33e)
の `eloop_lib.sh`、`lib/ai_generate.sh`、`lib/ai_generate_policy.sh`、
`lib/ai_prepass_budget.sh`、`lib/ai_queue_observability.sh`。
本番の実効model chainやproviderの成功はコードの照合だけでは証明できない。

## 話せる材料

開戦時の両将軍のHP、分類済み勝敗と終了時HP、観測で確認した城の所有変更、
支払いと総兵士数を確認した補充、支払いと増築結果を確認した築城を材料にする。
卵回復を優先する方針変更とv132の札の損害評価も、実行結果と区別した判断として扱う。

勝利から城の占領、敗北から将軍の死亡、選択から札の使用成功を推測しない。
札の `raw_damage_min` は敵兵士の吸収前、`damage_lower_bound` は吸収後の損害下限であり、
実際に与えたダメージではない。`lethal=false` は下限で撃破を保証できない意味であり、
撃破不能の証明ではない。

生成結果は本文と参照fact idだけのJSONに限定する。120文字以内の日本語を受理し、
未知の数値・名前、勝敗の創作・反転、HPの将軍間転置、補充人数と総数の取り違え、
計算値の実ダメージ化、死亡の断定などは `invalid_output` にする。
数値の対象や項目が省略されて照合できない文も配送しない。
この検証は任意の自然文の真偽を完全に証明するものではなく、観測材料と短い出力に限定して
誤った断定を抑えるための制約である。

## 場面の寿命

開戦時HPや未実行の方針は、生成前後・enqueue直前に同じ場面であることを確認する。
戦闘終了や購入完了などは直前の履歴として扱い、同じ章・戦闘番号のままfield/menuへ戻っても
元の20秒以内なら説明できる。次の戦闘、章変更、新しいfactによる置換、世代切替、
lease変更、terminalでは破棄する。本文は過去の確認事実として話す。

Sorenへのruntime fenceは従来のgame/runtime/世代/lease/期限の5項目を使う。
Soren側の再生開始時に場面IDを再照合する機能はこの変更には含めない。
したがってenqueue後に同じruntime内で場面が進んだ場合、元の短い期限内の履歴音声が
再生される余地はある。再生開始済みの音声を途中で切らない。

`game_over` を含むterminalでは新しい生成とenqueueを止める。
旧固定terminal recapを動的本文へ置き換える機能は今回追加していない。
全ての出来事を読み上げる保証はなく、間隔制限・queue待ち・生成時間によって無音になる。

## 診断と配備後の確認

manifestは `ops/vm_actions/runtime_registry.py` の `HANJUKU_SCENE_ONESHOT`。
常駐supervisor workerとして登録せず、PIDが無い待機状態を異常扱いしない。
診断は [runtime-diagnostics.md](runtime-diagnostics.md) の
`corners.retro_corner.scene_narration` を使う。

同じcanonical runtimeでproducerの更新・request seq、workerの `generate_started`、
`generate_succeeded`、`deliver_enqueued` を順に確認する。
`generate_failed` と `deliver_failed` は別段階であり、`skipped` の理由も固定enumで公開する。
本文・prompt・生OCR・env・lease・queue owner token・例外本文は診断へ出さない。

`deliver_enqueued` は共有queue入口の成功であり、実際に聞こえたことを意味しない。
再生は `narration_playback` のstarted/completedと照合する。
`queue_failed_fence_rejection` は対応する固定fence拒否markerを検出した件数であり、
`queue_failed_unclassified` は残りの失敗件数。期限判定が再生後に行われる経路もあるため、
失敗件数だけから無音・途中切断を断定しない。

ローカル回帰ではproviderを呼ばず、mock bootstrap/JSON/共有queueを使う。
pinned Sorenの実dispatcher/policy/budget/queue libraryとmock backendを使う契約試験でも、
半熟active＋通常lifecycleの4 pauseから既存radio queueの取得・解放へ到達することを確認する。
実VMでのsubreaper/pidfd利用、正規roleの生成成功、queue投入、発声は配備後に別々に確認する。
配備は通常のPR・必須CI・main・owner-only gatewayを使い、共有音声・配信のrestartは不要。
