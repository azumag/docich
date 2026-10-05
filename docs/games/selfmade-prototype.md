# #380 非本番 prototype の最小骨格

[実装前設計](https://github.com/azumag/docich/issues/380#issuecomment-5987795257)のうち、鍵・永久スイッチ・出口・最大2ゲートの信頼側判定を実装する。16×12セル、640×480 PNG、20Hz、4tickごとに1セル移動、2400tick上限。壁・閉ゲートは移動を止め、取得→スイッチ→ゲート更新→出口の順で判定する。画面の K/S/E/P と操作説明がゴールを示す。

このPRは手製合成fixtureによる純粋テスト。実AIによるゲーム制作・攻略・生成コード実行の受入れは未達。`game.mjs` はhash対象の不透明なバイト列で、fixtureは意図的に偽WINと状態書換えを提案するコードを含むが実行しない。外部LLM/API・ネットワーク・支出・新ソフト導入は不要。固定コーナーの本番登録・配信・rotation・実ゲーム切替には接続しておらず、enabled=false相当の未登録状態を維持する。

```sh
PYTHONPATH=src python3 -m pytest -q tests/test_selfmade_prototype.py
```

## 受入境界

`Artifact.load(path)` は256KiB/32ファイル以内の game.mjs、rules.json、manifest.json と任意の assets/*.png/*.txt を読む。ディレクトリdescriptorからの相対open、NOFOLLOW、通常ファイル検査でsymlink・FIFO・未知ファイル・ネストを拒否する。manifestの全payload hash、artifact ID、schema/engine/judge版、seed、20Hz、操作/ゴール/上限、依存なしを検査する。manifest自身を含むhash一覧から固定identityを作り、バイト列・RuleSpecをimmutableなメモリsnapshotにする。ディスクをread-only化した、コードレビュー/隔離スモークが合格したという意味ではない。開始前・replay前後にディスク版と再照合する。修正は別artifact IDと新挑戦にする運用が必要。

RuleSpecの全フィールドと型は `Rules.parse` を正本とし、fixtureが具体例。全192セルを壁/床で列挙する。player/key/switch/exitはそれぞれ一つの `{id, cell:[x,y]}`、gatesは最大2個の `{id, cell, requires:["key","switch"]}`。範囲外、壁上、位置/ID重複、未知項目、任意条件・循環するゲート参照を拒否する。この最小schemaは巡回敵を含まない。隠れた条件や任意コードによる状態遷移は受理しない。

`FixtureSession.observation()` はPNG・frame_id・操作説明・残りtickだけを返す。制作artifact/内部State/証拠は攻略観測に含めない。`submit(bytes)` の契約は1KiB以内の厳密JSON、`frame_id/seq/buttons/ticks` の4項目のみ。seqは1から連続、buttonsは空配列かUP/DOWN/LEFT/RIGHT一個、ticksは整数1〜60。未知項目・重複JSONキー・古いframe・重複seq・bool/小数・巨大入力は状態を進めず拒否し、3連続拒否でinvalid_input。拒否後の正常入力で連続拒否数を戻す。任意のhost Python objectへのアクセスを防ぐsandbox APIではない。

`transition` だけが毎tick状態を更新する。候補勝利時点で攻略入力と攻略wall watchdogを閉じ、期限後のwatchdog/後着入力でも候補snapshotを変更しない。`verify()` が新しい信頼側engineで受理入力を再生し、artifact/seed/版・全tick hash・勝利tickを照合してverified_winにする。replayは開始時点から独立した30秒のmonotonic clock上限を持ち、超過はreplay_mismatchで未検証にする。拒否入力は適用せず、受理/拒否と開始tickを記録し、拒否理由は別の固定診断コードにする。timeout/取消/invalid_inputもcutoff tickまで再現する。攻略wall上限240秒は呼出し時のmonotonic clockで確認し、無応答時はcontrollerが `expire_wall()` を呼ぶ。wall時間そのものはreplayせず、保存したcutoffと理由を使う。証拠は信頼側controllerの記録を前提とし、任意に差替えられたログの署名/永続化は未実装。

`check_proposal` は将来の隔離IPC向けの純粋一致検査。生成側のWIN/success/位置や所持品変更を勝利証拠に使わない。現時点では生成側との通信を行わず、生成描画も合成しない。すべての重要entityを信頼側だけが描画する。

## runner / IPC の純粋準備契約

`selfmade_contract.py` は実行に接続しない独立した準備契約（`CONTRACT_VERSION=1`）を持つ。既存artifact schema/engine/judge版、`image_digest=None` のfixture、`FixtureSession` は変更しない。適合するreportを作っても実行許可やOS隔離の証拠にはならず、`start_generated` は引き続き常に拒否する。

`validate_preflight` は将来の信頼側collectorのreportについて、版と明示されたimmutable `sha256:` image digest、OS隔離、非root、networkなし、capabilityなし、bundle read-only、host HOME/資格情報/Docker socket非露出、host fallbackなしを厳密検査する。CPU1000 millicores、RAM256MiB、process16、出力16MiBはIssueの初期上限案と一致する整数値のみ。tmpは正の明示上限を必須とし、未決定の実サイズやsandbox製品を選ばない。これはreportの型・契約一致検査だけで、collector、実mount、OS資源制限、遮断probeは未実装。ゲーム/providerの自己申告をreportの根拠にしてはならない。

`parse_input` は既存の1KiB以内・4項目solver入力を状態更新なしで検査し、immutableな値を返す。frame/連続seq/buttons/ticksの既存契約を維持し、受理時のseq更新は呼出し側の責任とする。

`parse_proposal` の内部準備envelopeは `version/seq/tick/state` の4項目。seqは信頼側が受理済みのsolver入力batchを指し、そのbatchの各tickで同じseqを使える。信頼側が与える現在seqと独立計算済みtick/stateに完全一致する場合だけ、生成JSONへの参照ではなく元のimmutableな信頼側Stateを返す。未知項目、重複キー、非有限値、偽WIN、所持品/位置/ゲート等の書換え、不正seq/tickは拒否する。これはlive wire ABIの採用ではなくpure fixture用の版付き準備契約。channelのartifact identity、framing、生成guest APIは別途設計・検証する。

`OutputBudget` はsession全体の生成出力16MiBを数える。将来のtransportはstdout/stderr・診断・framing・描画を同じbudgetへ、保存やparseより前に算入する必要がある。拒否/不正JSONも算入し、超過後は継続を拒否する。JSONのdecode前には呼出し側が明示した `max_message_bytes` も検査する。Issueはこのper-message上限を定めていないためruntime既定値を新設せず、選定を残件とする。現helperはstreamの読取り・buffer上限・process強制終了を実装しない。

追加回帰はstdlibだけで実行できる。合成report、入力/提案、総出力とmessage境界を検査し、process/network起動口をテスト中に禁止したまま既存fixtureの信頼側replayも確認する。これらの成功は実OS隔離・生成実行・実AI生成/攻略の成功を示さない。

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_selfmade_contract.py -v
```

## fail-closed と未完範囲

`start_generated(path)` は常にsandbox_violationを返す。隔離環境を用意していないため、生成コードのimport/eval/子プロセス起動/host fallbackは存在しない。無限loop/crash/外部アクセスのsourceを渡す回帰も起動前に拒否する。OSによるnetwork/HOME/秘密/Docker socket遮断、資源上限・実子プロセスkill/reapを実測したという主張ではない。

`MockCorner` は所有権取得、キー解除→子停止/reap→所有権解除→元画面復帰の順序をメモリ内で一度だけ記録する。共通PID/子プロセスは模擬値。復帰不成立時にはverified_winでもreport.success=falseにする。生成失敗・timeout・invalid_artifact/input・sandbox_violation・replay_mismatch・取消の模擬cleanupを検証する。本番のCornerExecutionCoordinator/GameSwitchCoordinator契約へ接続していない。

残件: 巡回敵と接触死、生成固有コードの制作/レビュー/隔離スモーク、非root/networkなし/CPU/RAM/process/出力制限を保証するrunnerとIPC、毎tick生成提案との実照合、隔離プロセスでのreplay、controllerの壁時計watchdog、証拠の永続化、実アダプタによる子プロセス終了と元画面復帰。モデル/provider/課金枠と本番枠の決定も別工程。これらを既に動かしたと扱わず、初回PRの境界として明記する。
