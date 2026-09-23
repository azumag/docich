# 半熟英雄のscript bot

`config/games/hanjuku-hero.toml` は `brains/hanjuku/bot.py` を1.5秒間隔で起動する。
Python標準ライブラリだけで画像を判定し、決定的なルールでSNESのpad入力を返す。
Claude、OpenCode、API、認証情報を操作時に使用しない。旧`brain.py`は互換テスト用に残すが、
この設定からは実行されない。OpenCodeによる試合結果からの自動改善は後続作業（Issue #954）とし、
今回のbot操作はそれに依存しない。bot版は `hanjuku-chart-v2`。

## 画面の構造化（`hanjuku_font.py` / `hanjuku_screen.py`）

- 文字はSNESの8×8タイル格子に描かれる。名前入力表から採取した字形137種（ひらがな・カタカナ・
  数字・英字・記号）と濁点/半濁点（上段タイル）の2値ビットマップ（`hanjuku_glyphs.py`）と
  完全一致したタイルだけを文字として読む。一致しないタイルは不明として扱い、推測しない。
  画像そのもの・ROM・セーブはリポジトリに含めない。
- 読めるもの: 月初ヘッダー（年・月・所持金・メインメニューでは話数）、メニュー項目と手カーソルの
  選択位置、会話文（「〜しょうぐんが 〜じょうに のりこんだ!!」「〜じょうが てきに せめこまれました」
  「きょうさく」等）、商人の品目と価格、個数入力、戦闘パネルの将軍名とHP、出撃確認の携行切り札。
- マップ: 通常カーソル（白い角括弧）と出撃先マーカー（青いG）、城の屋根（敵=青、自軍=赤）。
  本番スクリーンショット（897×672）でも同じ読みになることを、過去に本番で取得した画像で確認済み。

## 操作と終了条件

- 名前入力: ひらがな表を手カーソル位置で移動し「どうし」を1文字ずつA入力する。名前欄の実表示が
  「どうし」と一致した時だけSTARTで確定し、目標の前方一致でなければBで1文字削除する。未分類の字形を除去して名前一致とせず、
  未読文字が残る場合や名前画面を構造化できない場合は入力・確定を保留する。
  手が目標文字に重なって読めない場合は実測済みの表配置から目標セルを求める。
- チャート（`hanjuku_chart.py`、出典 `games/hanjuku-sfc-speedrun` の charts/1.md・overview.md）:
  第1話は 1-A1 どうし→キカンドン、1-V1 ヴィーナス→ナキューメラ、1-C1 ココット→ジョンリギ（チャート表記
  ジョリンギ）を切り札なしで出撃し、占領を確認した城から 1-A2 フットバース→ゴーメン、
  1-V2 フットバース→カストーラ、1-C2 ダイチスイム×2・ブラッキー→スペンソニア、1-A3 どうし→スペンソニア、
  全城占領後に 1-B1 クースカン・ノリウツールを持ってボス（結界の塔）へ向かう。
  戦術は ガルバンゾー戦で主人公は敵HP24以下でフットバース、ヴィーナスは開幕フットバース、
  ココットは白兵後に敵HP13以下でダイチスイム、タピオカ戦は開幕ダイチスイム・ブラッキー、
  クイーン戦は最初のぶつかり合いの後にクースカン→ノリウツール。それ以外は白兵（戦闘中は入力しない）。
- 月一（1ねん4のつき→5のつき、画面上は「1ねん 5のつき」）: 所持金がチャート想定214G以上なら
  チャートどおり（イッテツーン9・ノリウツール2・クースカン4・ゼンマイン1・兵士41）。
  不足時は `budget_boss_kit_first`（ボス用クースカン・ノリウツールを先に、次にイッテツーン最低6個）へ
  分岐し、逸脱理由・期待値・実測値を記録する。商人の品揃えにない・買えない品はスキップして記録する。
- マップ移動: カーソルの画面位置を毎回実測し、城の屋根の配置（複数の屋根の相対位置で照合）から
  ワールド座標を再測位する。戦闘・イベント・月初メニューから戻った直後は位置を「不確か」とし、
  再測位するまで城の決定操作をしない。城で決定してメニューが出なければ再測位する。
  押下は1回あたり最大28フレーム（加速しない範囲）で、FPS揺らぎは実測移動量で補正する。
- 戦闘の勝敗は戦闘パネルの最終HPだけで判定する（敵0かつ自軍非0=勝ち、逆=負け、それ以外=未分類）。
  対応する侵攻イベントを観測した攻撃側の勝ちだけを占領として扱う。出撃予定だけでは城・攻守を確定しない。
  ボス撃破だけでは次章と判定せず、画面の章表示を確認してから旧章の座標・出撃・購入状態を消去する。
  フェード中の欠けた表示は同じ読みが2回続くまで採用しない。
- 一騎打ちの申し出は主人公の損失リスクを避けて断る（`decline_duel`）。凶作はチャートでは
  リセット指示だが、botはリセットしないため `reset_forbidden` として記録する。
- 攻撃で負けたチャート手順は同じ攻撃を最大2回まで再指示する（`retry_after_loss`）。
  出撃元の城に将軍がいなければ本城から出し直す（`source_fallback`）。
- 2話以降はチャート未実装。`chart_unavailable` として記録し、従来の確認入力だけで進める。
  全ステージ攻略・勝率は未確認であり、成功扱いしない。
- 文字・カーソル・戦闘表示のどれも読めない画面は「状況判定保留」（`situation_held`）として記録し、
  従来の安全な規則（確認入力・マップでは待機）だけを使う。
- 固定20分の終了・強制セーブは行わない。名前入力とプレイを観測した後、実ROM由来のタイトル画像の
  特徴に3回以上・2秒以上一致した場合、`game_over`（根拠`title_return_after_gameplay`）とする。
  ゲームオーバー演出は確認入力で進め、タイトルへ戻った時点で入力を止める。起動時のタイトルや
  デモをゲームオーバーと扱わない。タイトルへ戻る前の敗北演出の種類までは判定しない。
- 正規化したゲーム画面のRGBが連続300秒変化しなければ`screen_stalled`とする。
  15秒超の観測欠落、一時停止、時計の巻き戻り、画面変化で計測をリセットする。
  背景アニメーションを含めて変化する画面は「無変化」ではない。
- 終了候補・終了確定時は実行境界でも入力を拒否する。runtime/generation/leaseが一致する終了証拠を
  `retroarch_boundary.json`へ引き継ぎ、既存coordinatorがゲーム側の子プロセスを停止して前のゲームへ戻す。
  他のRetroArchゲームの明示pause/save契約は変更しない。
- 復帰直前にcorner監視は `hanjuku_run.terminal()` で永続化された終了証拠を runtime_id・generation・
  lease と照合し直す。一致しない・証拠が不正な場合は復帰せず失敗として止める（fail-closed）。
  起動成功receiptが返した完全なidentityをcornerのactive保存前に固定する。初回観測時に
  現在のruntimeを後から所有したことにはせず、identity欠落や同名別leaseへの置換はfail-closed。
  coordinatorにも固定した `bot_identity` を `payload.expected_source` として渡す。排他ロック内で
  完全一致を確認してから停止へ進み、観測後に別leaseへ変わった場合は `source_fence_lost` で拒否する。
  元のゲームがあればそのゲームへ、なければcoordinatorのstopでidleへ戻す。

## 音声

半熟英雄の`[retroarch] audio_enabled=true`と`audio_sink="soren_null"`で、
ゲームのBGM・効果音を既存の配信busへ出す。共通の音声busや配信エンコーダは再起動しない。
Sorenの`bgm_worker.sh`は半熟英雄中の共通BGMを止め、次のCLIゲームでは再開する。
読み上げ音声は同じbus上で維持する。
`audio_latency_ms=289`で音声バッファを確保する。本番の64ms設定で音切れがあり、
バッファ拡大と実行中エミュレータの全スレッドをnice -5へ調整した後、ユーザーが改善を確認した。
設定値は次回起動にも反映される。niceの調整は当該runtimeだけの復旧操作であり、
この設定からsudoや優先度変更は行わない。

`audio_volume_percent=80` で、presentationがゲームのプロセスツリーに属するPulseAudio
sink-inputだけを80%にする（共通sink・読み上げ・BGM worker・他ゲームのstreamは変更しない）。
ゲームがstreamを作り直しても再適用し、sink-input番号・sink名・音量・muteを
runtimeの `audio_volume.json` に記録する。

## 実況

- botは決定記録から、画面で読んだ事実（将軍名・城名・HP・所持金・切り札・チャート手順）だけを使って
  実況文を作り `hanjuku_commentary.jsonl` へ候補として書く（`hanjuku_commentary.py`）。
  事実が読めない決定は文を作らず `held`（状況判定保留）として記録する。
- 配信へ出すのは agent/corner監視の観測処理（`hanjuku_narration.py`）。runtimeごとの非ブロッキングの
  ファイルロックで候補を1回だけ取り出し、最新の1件だけを既存の `enqueue_audio_text`
  （`hanjuku_commentary`）へデーモンスレッドで渡す。読み上げworker・共通音声は再起動しない。
- 抑止: 25秒のcooldown、同じ意味keyの連続、直近32件と同じ文、20秒より古い候補、処理中の重複、
  120字超。送信直前にも完全なruntime identity・終了状態・期限を再検証する。
  キューのsidecarへidentityとexpires_atを渡し、再生側でもclaim時・再生直前に照合して
  終了済み・別lease・期限切れを破棄する。結果は `hanjuku_narration.jsonl` に残す。

## 改善用ログ

世代別の`run/runtimes/<runtime_id>/`へ保存する。ゲームを停止しても削除しない。

| ファイル | 内容 |
|---|---|
| `hanjuku_run.json` | bot版、世代、観測数、実入力数、戦闘開始/終了数、現在の判定、終了理由と根拠 |
| `hanjuku_events.jsonl` | 時刻、画面SHA-256、画面判定、実際に送信したボタンと押下時間、戦闘遷移、終了理由 |
| `hanjuku_events.previous.jsonl` | 4 MiBごとのローテート先。現行と合わせ最大約8 MiB |
| `hanjuku_frames/frame-*.png` | 画面判定の変化、60秒間隔、終了時の画像。120枚のリング |
| `hanjuku_frames/decision-*.png` | 名前確定・章確認・戦闘結果、将軍一覧・携行品選択/確認、いばら解除、切り札選択中/関連判断、代役選択・装備付き出撃開始に実際に使った画像。別の120枚リング |
| `hanjuku_bot.json` | botの現在の方策状態（チャート手順・占領・勝敗集計・所持金）。別世代へ持ち越さない |
| `hanjuku_decisions.jsonl` | 決定記録: `decision`、`chart_step`、`strategy_variant`、状況（画面種別・将軍・城・HP・所持金）、`deviation_reason`、`expected_metric`、`observed_metric`、`resulting_event`、`reason` |
| `hanjuku_commentary.jsonl` | 実況候補（文・意味key・生成元の決定・理由、または状況判定保留） |
| `hanjuku_narration.jsonl` | 実況の配信結果（enqueued/delivery_failed/skipped:理由） |
| `audio_volume.json` | ゲーム音声streamのsink・音量・mute実測 |

ログにLLMプロンプト、認証情報、ROM本体、セーブデータを含めない。画像と詳細イベントはVMローカルに保ち、
固定diagnosticsは許可されたphase、終了理由、入力数、戦闘回数、無変化秒数のみを公開する。
画像はリングで上書きされるため、イベントのSHAを使って対応を確認する。
`action_plan` は未送信の入力予定として記録し、実送信は `input_sent` だけで確認する。
両者を `decision_id` で結び、実際に判断に読んだ画像SHAと送信時の最新観測SHAは別フィールドに残す。
別leaseの方策状態から実入力の判断IDを推測しない。
再出撃の移動・将軍/装備選択・出撃確認は、元の `retry_context` の戦略名・逸脱理由・期待指標を
決定と `action_plan` に継承する。入力のない判定保留も理由を残し、読取不能を欠品と数えない。

## runtime変更の確認項目

- registry/manifest: 既存`retroarch` adapterと世代別agent/game manifestを使用。新worker/queue/providerなし。
- health: 既存agent修復とcoordinatorのownership/lease確認を使用。別runtimeの終了証拠では停止しない。
- telemetry/diagnostics: 上記JSONLと`collect_diagnostics._project_corner_state`の固定射影。
  `bot_chart` は話数・チャート手順・占領数・勝敗/未分類・切り札消費・将軍損失・所持金・月だけを
  許可リストで公開し、文・理由・将軍名は公開しない。後続の semantic decision（#882）や
  終了後リプレイ比較（#838/#954）は `hanjuku_decisions.jsonl` の「状況・選択・結果」を入力にできる。
- regression: `tests/test_hanjuku_script.py`、登録、RetroArch境界、corner rotation、diagnosticsのテスト。
- deploy: docich protected mainの正規gateway経由。共有配信・音声を再起動せず、次回ゲーム起動から適用する。
- 実機受入: 新規ゲーム→入力→戦闘、終了後の復帰・子プロセス解放、配信PID維持を別々に検証する。
  ユニットテストや設定の有効化だけで実機受入の完了とはしない。

## 固定の手動起動操作

公開リポジトリでは汎用の`VM operations / exec`は拒否される。
ownerは`Corner rotation operator`の`start-hanjuku`と`confirm=production`を使用する。
protected mainとVMのSHA一致を検証し、ゲーム・コマンド・unitを入力できない固定scriptだけを送る。
`docich-hanjuku-corner.service`が共通coordinator経由で起動し、現在試合の境界・pause・cooldownを守る。
要求の受理とゲームの実起動は別であり、`retro_corner.json`と世代別botログで実起動を確認する。
他のコーナーが稼働中の場合に強制停止したり、cooldown/stateを削除したりしない。
