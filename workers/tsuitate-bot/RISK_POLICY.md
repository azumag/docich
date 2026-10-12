# ついたて将棋: v12リスク評価候補版の詳細設計

Issue: #1888 / 基準main: `457644e40b9e94f743b720cc6decf7c2c0b4368a`

## 1. 提供物と変更しないもの

`src/brain/risk-aware.js` の `chooseRiskAwareMove(observation, options)` が、
明示的に呼び出す比較用候補版 `tsuitate-brain-v12-risk-candidate1` である。
既存の `chooseMove`、`BRAIN_VERSION = tsuitate-brain-v11`、profile検証契約、
Webhook/beta adapter、Worker entry、Durable Object、対局単位の版固定を変更しない。
本番の既定選択器から候補版は呼ばれない。単にmerge/deployしても候補版への切替は起きない。

今回の実装は候補選択・説明用診断・オフライン比較まで。
新規実対局、配備、公開status、配信盤面、manual_only、自動rotation、秘密・権限・予算の変更は含まない。
完全な相手盤面の事後分布、対戦相手別prior、段階別戦略、数手先探索、校正済み情報利得、
live診断保存とadapter接続は未実装であり、本候補をそれらの完成版とは扱わない。

## 2. データ境界

`inspectMoveCandidates` は既存 `normalizeObservation` と候補生成器を共用する。
入力は `ruleset/color/turn/moveNumber/pieces/hand/inCheck/opponentInCheck/attemptBudget/knownEnemies`。
自駒・自持ち駒と既存のown-view由来証拠だけを使用する。
不明マスは「空いている」と見なさない。相手所有と明記された駒、未知キー、相手盤面、
秘密、完全情報の合法手一覧は取り込まない。adapter側で所有者を先に分離する契約も維持する。

候補は二歩、行き所のない打駒、自駒への移動、自駒による射線遮断、任意/強制成りを既存生成器で処理する。
王手時は既存 `checkResponses` の必要幾何条件を優先する。幾何候補が空の場合のfallbackも維持する。
現行の `knownEnemies` による打ち・射線の候補制限は変更しない。

- `forbiddenMoves`: ACK不明を含む既試行の正確な手。これだけでは成り兄弟を除外しない。
- `foulMoves`: 確定反則。現在の可視条件で生成可能だった手だけが成り兄弟も除外できる。
- 無効な成り/不成の申告は、合法な兄弟手を消す証拠にならない。
- 相手の手番、残り予算0、不正入力は `null`。候補版はlinear-v1 profileのみを受け付ける。
- 基礎候補上限4096、禁止手/確定反則の入力は各4096、seedは512文字。

## 3. 評価式

既存linear特徴量から得る基礎スコアをBとする。Bには既存の未知通過マス当たり0.25の減点を含む。

```
R = 0.25 * unknownPathSquares
  + 0.75 * unknownDrop
  + 0.025 * (kingExposure + openedRaySquares)
  + 2.0 * uncoveredFraction
score = B - multiplier * R
```

`uncoveredFraction` を計算できない場合、当該項の寄与は0にし、診断値はnullのままにする。
unknownDropは候補の打ち駒なら1。空きが証明されたマスを入力する新契約は設けていないため、
候補に残った打ち先も未知として扱う。打ち駒の一律禁止はしない。
kingExposureは移動後の玉から自駒/現在の占有証拠に遮られない射線上のマス数。
openedRaySquaresは非玉の移動によって玉の射線上で新たに露出したマス数の増加分である。
いずれもリスクの尺度であり、合法・非合法の確定判定ではない。

| 残り試行 | 1 | 2 | 3 | 4以上 | 不明 |
|---|---:|---:|---:|---:|---:|
| multiplier | 8 | 4 | 2 | 1 | 4 |

固定係数は未校正の初期値。確率として表示・説明しない。
既存v11の新鮮な捕獲証拠に対応するtierを最初に比較し、そのtier内でscoreを比較する。
同点だけseed由来hashとUSIの固定順で決める。探索ノイズは使わない。
情報利得を校正する前に、残り反則を「無料の探索」として消費しない。

## 4. 王手仮説と限界

王手通知がtrueで自玉が1枚の場合、現在の可視情報と整合する単一王手駒の
「マス・移動種」を列挙する。10移動種を用い、成小駒は同じ動きの金にまとめる。
これは盤面全体の粒子でも、敵配置の確率分布でもない。

全81マスを調べ、自駒のあるマスを候補から外す。自駒とage=0の相手占有証拠で射線を遮る。
age>0は仮説モデルでは現在の占有の確定証拠にしない。
仮説上限128を超えた場合は任意順で切り捨てず、solverを無効にして理由を返す。
現在の移動種・9x9で全81の玉位置に対し上限と先後180度対称性をテストする。

応手ごとに移動元を除去し、移動/打ち先を配置して玉安全を再評価する。
途中の仮説敵駒を飛び越える移動は仮説に対応できない。
打駒は王手駒を捕獲できず、桂の王手は途中に駒を置いても消せない。
移動元の玉を遮蔽物として残さない。捕獲で仮説王手駒を除去できる場合は対応と数える。

`coveredHypotheses / checkHypotheses` はその限定された集合に対応した比率にすぎない。
隠れた別の駒の利き、両王手、他の敵駒による遮断、持ち駒構成、打ち歩詰めは完全には表現しない。
100%でも合法とは主張しない。最終審判は既存のサーバであり、棋力向上は未証明。

## 5. APIと診断

- `inspectMoveCandidates`: 正規化済み観測、linear profile、seed、候補の特徴量/基礎スコア/経路リスク。
- `checkingHypotheses`: 可視情報から作った仮説と生成状況。完全盤面は受け取らない。
- `analyzeRiskCandidates`: 候補全体のprivate順位、リスク内訳、仮説対応数。
- `chooseRiskAwareMove`: USI/特徴量/候補版version/profileId/候補数と選択候補のdiagnostics。

診断は schemaVersion、policyVersion、残り予算、係数、仮説生成状況、選択理由、基礎スコア、
リスク内訳、仮説数・対応数を返す。入力オブジェクトは変更しない。
これらは非公開の呼び出し結果であり、公開statusやbroadcastへの接続を追加しない。
候補全体やUSIは自分の隠し情報なので、そのまま配信・公開ログへ出さない。

## 6. 比較コマンド

Node >=22.18.0（既存package.jsonの指定）を使用する。

```sh
cd workers/tsuitate-bot
node --test test/risk-aware.test.js test/risk-comparison.test.js
node scripts/compare-risk-policy.mjs /private/own-view-cases.json
```

入力例（架空の可視局面。対局棋譜ではない）:

```json
{
  "schemaVersion": 1,
  "cases": [{
    "observation": {
      "ruleset": "tsuitate-9x9", "color": "b", "turn": "b", "moveNumber": 1,
      "pieces": [{"square":"5i","role":"K"},{"square":"7g","role":"P"}],
      "hand": {}, "inCheck": false, "attemptBudget": 3
    },
    "options": {"seed":"fixed-case"}
  }]
}
```

各ケースに任意の `judgments` を渡せる。USIをキーとし、値は
`{"status":"accepted"}`、`{"status":"foul","reason":"into_check"}`、
`{"status":"unknown"}` のいずれか。ラベルは評価器だけが読み、選択器へ渡さない。
未提供/不正/unknownラベルはunknownで止まり、再試行・反則推定しない。
確定反則だけ残り試行を減らして正確な手と成り兄弟の除外へ進む。
予算不明の反則はunknownBudgetとして止まり、最大32再試行はretryCapReachedで別集計する。
比較上の除外入力は各4064以下にして、32試行追加してもcoreの4096上限を越えない。

stdoutは集計JSONのみ。変更された初手の件数、受理/反則/判定不明/予算切れ/再試行上限、
提案した打ち駒数、固定語彙の反則理由、ローカルwall-timeのp50/p95/max、入力SHA-256を出す。
手、盤面、対局ID、任意理由文字列、ファイルパスは出さない。失敗時も固定文だけをstderrへ出す。
入力は通常ファイル・5 MiB以下・1〜1000ケース。ファイルサイズの変化も有界読み込みで検査する。

**これは外部提供ラベルを使う固定局面比較であり、完全な棋譜再生審判ではない。**
常に `labelsVerified=false` / `winRateMeasured=false` とする。
受理は「一手の受理」であって対局の勝ちではない。ラベルがない例では双方unknownとなる。
厳密リプレイ由来のラベルを準備しない限り、反則削減の実測根拠にはならない。

## 7. 検証と昇格ゲート

新規36テストに、既存v11の2400判断のgolden digest、候補生成共有、ACK不明/反則の区別、
強制成り、打ち制限、低予算リスク、非公開フィールド除去、入力不変性、先後対称性、
王手対応を独立した移動規則実装と突き合わせる検証、CLI漏洩/サイズ検査を含む。
これは同じ担当が書いた自己検証であり、別担当による独立レビューではない。

goldenは基準brainのblob `f915ae87d0c2256fc412bdbab50a19b32cc268aa` と
取得コードのgit blob hashが一致することを確認して生成した。
テストで再生成して期待値を自動更新してはならない。

今回のローカル環境はNode 22.16.0で、新規36テストと構文検査は成功した。
既存packageの要求するNode >=22.18.0上の確認、リポジトリ全テスト、cf build、workerd、
exact-head CI、独立レビュー、新規実戦は未実施。部分取得した検証用ソースでの結果を
フルcheckout/本番の検証結果と混同しない。

昇格前には、厳密な王手判定を含むprivate固定局面・holdoutで、受理数/予算切れ/反則/物得/
時間をv11と比較する。adapter接続・対局単位の候補版固定・private診断保存を別変更でレビューし、
明示的な承認範囲の非稼働境界で配備して新規10〜20局を確認する。
数局の勝率や同一局面の反則だけを根拠に既定切替をしない。

## 8. 運用・引き継ぎ

今回の実装は新しい課金API、依存パッケージ、永続状態、ネットワーク通信を必要としない。
共通runtime registry/queue/health/projectionの変更はない。
`DOCICH_HANDOFF_PATH` は未設定で、非公開正本は未読・未更新、最新性は未確認。
既存ops brief、VMのバナー/音声、稼働プロセスへは触れない。
コミットと検証結果はIssue #1888を引き継ぎ先とする。
