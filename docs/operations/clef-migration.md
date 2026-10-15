# Clef / JEV の明示切替とロールバック

## 契約

JEVを削除せず、共通 `docich.semantic_decision` に `clef` を追加する。
`DOCICH_JEV_ROUTE=clef` は Workers AI `@cf/cloudflare/clef`、
`DOCICH_JEV_ROUTE=direct` は既存 TypeSafe `jev-1.13.0` を選ぶ。
既定の `direct`、用途別rubric、confidence閾値、既存cooldownは変更しない。
backend名 `COMMENT_CLASSIFIER_BACKEND=jev` は互換性のため維持する。

2026-10-16のオーナー追加指示: JEVへすぐ戻せるようにする。
両providerの認証設定を保持し、コード変更・再ビルド・キー再入力なしで
明示的に往復切替できること。自動fallbackを追加する指示ではない。

## owner-only CLI

初回だけ、承認済みsecret管理経路で次のペアを設定用プロセスの環境へ渡す。
値をコマンド引数・チャット・公開ログへ書かない。

```text
DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID
DOCICH_JEV_CLOUDFLARE_API_TOKEN
```

既存chat workerに `TYPESAFE_API_KEY` が必要。戻し先の認証が失われている場合は、
Clefへの切替も設定ファイルを変更する前に拒否する。これは認証の存在確認であり、
TypeSafeの実API疎通を確認したことにはならない。

初回設定後の切替コマンド（レビュー済みコードの配備後に、許可されたVM環境で実行）:

```bash
# Clefへ。専用キーが呼出元に無ければ既存chat workerのペアを使用する。
python3 ops/vm_actions/configure_jev_route.py --route clef

# JEVへ戻す。保存済みClefのaccount/tokenは削除しない。
python3 ops/vm_actions/configure_jev_route.py --route direct
```

初回以降は専用キーの再入力不要。明示環境に片方だけある場合は、既存worker側の
もう片方と混ぜず失敗する。token更新時はペアを明示する。

切替は既存の原子的.env更新・保護されたバックアップ・chat workerの完全置換・
置換後の実効設定検証を再利用する。設定書換えだけを成功にせず、routeと保持した
JEV/Clef認証の一致を新workerで確認する。資格情報そのものは出力しない。
共有配信serviceは再起動しない。反映はworker置換後であり、無停止・瞬時とは扱わない。
restart失敗時はバックアップを保持して失敗を示し、自動でproviderを変更しない。

既存 `--disable` はroute設定を外して既定directへ戻す操作であり、JEV無効化ではない。
Clefの保存済み認証はこの操作でも残す。既存Vercel設定・削除の契約は変更しない。

## 安全境界

- REST接続先とmodelは固定し、account IDは32文字の小文字16進に限定する。
- Cloudflareのsuccess/errors/result envelopeを検証し、従来と同じstrict validatorへ渡す。
- 推論子プロセスへは選択中providerの認証だけを渡す。保持用の非選択キーは渡さない。
- timeout、HTTP失敗、不正応答でも、ClefからJEVへ自動fallbackしない。
- 通知保護・入力投影・目的別gateは変更しない。noul/score/画像入力は今回追加しない。

## 検証と残件

`python3 -m pytest -q tests/test_clef_switching.py`: **37 passed**。
実コードの.env書換えと一時ファイルを使い、JEV→Clef→JEV→Clefの往復、初回のみの
資格情報入力、キー保持、バックアップ権限、不正・片方だけの入力の拒否、shell quote、
秘密非出力、固定endpoint、provider別最小子環境、strict応答検証を確認した。
worker lifecycleとHTTPはmock。実際の本番restart・通信を行った証拠ではない。
対象の既存ソースと共通helperはGit blob SHA一致を確認して取得した。

別途、2026-10-16にCloudflare接続ツールから合成日本語2件を実APIへ送信し、
`api_only`（一般知識）と `stream_bug_report`（音声不具合報告）の期待ラベルを確認した。
本番コメントは送信しておらず、総合的な分類精度・p95 latencyの検証ではない。

**本番切替は未実施。以下が残るため、この変更を移行完了と扱わない。**

1. Clefをowner-only VM gateway / workflowの固定operationへ公開することと、初回の
   専用secretペアの承認済み配布経路の接続。今回CLIへは追加したが、gateway側に
   `configure_jev_route_clef` が追加済みとは扱わない。
2. runtime診断collectorの固定4キー投影を拡張し、Clefの設定・認証の存在と実効modelを
   秘密なしで観測可能にする。現行collectorだけではClefの認証を正しく観測できない。
3. 既存全体CI、独立レビュー、コメント分類/調査振り分け/radio/ゲームのconsumer確認、
   日本語評価セット、本番と同条件のcanary、実際の往復切替の確認。

ローカル単体テストと実APIの合成2件は、本番切替の完了条件の代替ではない。
非公開handoff正本は参照未設定のため未読・未更新。VM作業バナーは未操作。
本番環境、main、配信service、課金プランはこの作業では変更していない。

公式契約: https://developers.cloudflare.com/workers-ai/models/clef/
