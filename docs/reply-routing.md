# JEVによる返信の根拠要否判定 — 初期実装

関連: #829（共通comment/radio基盤）、#882（semantic transport）。2026-10-04。

## 目的と接続範囲

文章の複雑度、長さ、キーワードではなく、**正確に返答するために現在渡されている会話以外の根拠が必要か**をJEVに決めさせる。
最終的な対象はDiscordと配信コメントの両方。今回の実行接続は `discord_chat.ChatBackend.complete()`。
未使用ヘルパーだけではなく、既存のメンション→会話履歴→返信→送信/記憶確定の入口に接続する。
**配信のlegacy `broadcast/comment.sh` / `ai_generate.sh` の全呼出しを本変更で置換したとは扱わない。**

```text
メンションと既存の会話履歴
 → JEV（直近の本文だけ、1回）
    ├ api_only → 既存のHTTP API → 返答
    └ web / code / web_and_code / unknown
       → 明示承認された隔離Codexによる読み取り専用調査
       → 実行イベント・出典を検証
       → 同じHTTP API＋元のpersonaで返答を整形
       → 検証した出典をコード側で付記
 → 既存の削除整合・重複抑止・送信・memory commit
```

調査完了と返答生成を分離し、Codexの人格が会話のpersonaを置き換えない。
JEVにpersona/名前/ユーザーID/メッセージID/永続記憶の全件を渡さない。直近最大6件の会話本文で代名詞の参照を判断する。
元のcategory分類の「本文限定」契約は変更しない。新purposeは `reply-evidence-v1`。

## 判断契約

| ラベル | 意味 | 経路 |
|---|---|---|
| `api_only` | 挨拶、反応、祝い、雑談、提示済みの情報だけで十分な返答/推論 | APIのみ |
| `web` | 未知の名称・用語、現在情報、明示的な調査 | Codexの公開Web調査 |
| `code` | ゲーム、確率、アルゴリズム、実際の実装の説明 | 承認済みソースの調査 |
| `web_and_code` | 公開情報と実装の両方 | 両方の根拠を要求 |
| `runtime` | 実際の稼働状態、非公開ログ、障害原因など | 初期実装は未対応と明示。本番権限を与えない |
| `unknown` | 曖昧、必要な根拠が不明 | 調査側へ。調査できなければ回答保留 |

「SSRが出た！」と「このガチャの抽選処理は？」は同じ語を含んでも別の判断になる。
「XXって何？」は短くても調査が必要になり得る。コードの説明を求められたらモデルの想像で済ませない。
この表とテストの例は**判断rubricと経路契約**であり、JEV実APIの日本語精度測定ではない。

`api_only`はJEVが有効なラベルを返しconfidenceが0.80以上のときだけ採用する。閾値は未校正の初期値で、正解率80%の意味ではない。
低confidence、不正回答、timeout、キー欠落はAPI-onlyへ倒さない。JEVは既存 `semantic_decision.transport` を再利用し1.5秒・1回。
`DOCICH_JEV_ROUTE`の先頭routeを使う。今回のpurposeは同一ターン内のfallback再課金をしない。
JEV応答は固定ラベルだけで、コマンド・モデル・キー・パス・URL・書込権限を指定できない。

## APIと失敗

元の `ChatBackend._complete_api()` のmodel/endpoint/persona、tool_calls拒否、返答後のmemory整合性確認を保持する。
API-onlyの生成失敗を理由にCodexへ昇格しない。**調査要否とプロバイダー障害のfallbackは別**。
調査不可・失敗・必要な根拠なしのときは、自由生成APIへ流して知識だけで回答させず、固定の「調査を完了できない」返答にする。
APIの整形が出典を省いてもコード側で最大2件を付記し、Discordの既存900文字上限に収める。
再送・自律投稿・別チャンネル投稿・DM・ゲーム入力・コード変更・取引は追加しない。

## 調査の隔離と資料

最初の実装はLinuxのbubblewrapとCodex CLI。OpenCodeアダプターは未実装。
`--unshare-all`（API通信用networkのみ共有）で別のfilesystem/PID等のnamespaceを作り、OSの実行ファイル/ライブラリ、証明書/DNS設定と一時workspaceだけを渡す。
ホストHOME、checkout、`.git`、Discord SQLite、VMログ、Docker socketはマウントしない。
Codexは `--sandbox read-only --ephemeral --ignore-user-config --ignore-rules`、自動承認なし、subagentなし。
Codexのshell環境継承はnone。プロセス環境はPATH、LANG、**調査専用のCODEX_API_KEY**だけで、Discord/Twitch/他用途キーやproxyを引き継がない。
キーと本文はargvへ入れない。本文は匿名一時ファイル経由のstdin、stdoutは上限256KiB、stderrは破棄、終了/失敗/timeout時にプロセス群をkillしてreapする。
調査子プロセスは最大45秒。全体に元のAPI通信が続くため、45秒を返信全体の保証とはしない。

**隔離を満たせない環境で裸のCodexへfallbackしない。** bubblewrap/Codex/認証/承認済み資料がなければ調査不可。
モデルのプロンプトだけをread-onlyの保証とはしない。network共有はAPI接続のためであり、外向き通信のallowlistを実装したものではない。
運用環境でCodexのread-only subprocess network制限と、ホストloopback/内部サービスへ到達・操作できないことを確認するまで有効化しない。
より強いネットワーク分離・専用worker化は本番受入の設計課題として残す。現在のDocker設定の権限を緩めない。

コード調査は、運用者が公開可能と承認した**別ディレクトリのsnapshot**を使う。稼働中checkoutを直接渡さない。
`manifest.json`は以下の形式で、`files`に列挙したファイルだけをSHA-256照合して一時workspaceへコピーする。

```json
{
  "repo": "azumag/docich",
  "revision": "公開を承認した40桁のcommit SHA",
  "files": {
    "src/docich/example.py": "そのUTF-8ファイルの64桁SHA-256"
  }
}
```

実行時にmanifestは最大128KiB/1024ファイル、ファイル単体1MiB、合計16MiB。
絶対パス/親参照/隠しパス/AGENTS/シンボリックリンク/非通常ファイル/ハッシュ不一致は拒否。
manifestを作るだけで公開審査や非機密性が証明されたとはしない。作成・配布・更新は運用者の承認が別途必要で、本PRはsnapshotを本番へ配布しない。
`revision`はsnapshotの識別であり、本番の配備済みSHAだとは主張しない。

Codexの最終JSONだけでは成功にしない。完了イベント、実際のsearch/readイベント、必要な種類の出典を要求する。
code出典はmanifest内のファイル・実在行・引用の一致を確認する。Web出典は検索実行とHTTPS参照の形式を確認する。
**Web URLの参照先が全て取得済み/正しいとの完全な証明や、モデルの解釈の正確性までこの検査は保証しない。** 実APIとCLIのイベント契約・出典忠実性の受入が必要。

## 設定と診断

すべて既定off。今回、キー作成、課金モデル変更、設定変更、配布、worker再起動はしない。

| キー | 意味 |
|---|---|
| `DOCICH_REPLY_ROUTING_ENABLED=1` | JEVの振り分けを有効化。0なら従来のAPI呼出しそのまま |
| `DOCICH_ALLOW_REAL_AI=1` | 既存の実AI利用承認。新経路でも必須 |
| `DOCICH_JEV_ROUTE` / route専用キー | 既存JEV transport設定を再利用 |
| `DOCICH_REPLY_RESEARCH_ENABLED=1` | 別途承認した調査実行を許可 |
| `DOCICH_REPLY_CODEX_MODEL` | 運用者が選んだ調査モデル。既定なし |
| `DOCICH_REPLY_CODEX_API_KEY` | 調査専用キー。CLIの既存HOME認証を流用しない |
| `DOCICH_REPLY_SOURCE_APPROVED=1` | snapshotの公開範囲を運用者が承認 |
| `DOCICH_REPLY_SOURCE_DIR` | 上のsnapshotの絶対パス。モデルから受け取らない |

`PYTHONPATH=src python -m docich.reply_routing` は存在/承認フラグだけの診断JSONを出す。パス/モデル名/キー/本文は出さず、`live_acceptance=not_measured`を明示する。
Discordのログには固定scope、decision status、research statusだけ。通常返答/調査内容/IDは追加ログに出さない。
Docker imageへPython部品は同梱するが、Codex/bubblewrapをインストールせず、Composeの権限・mount・secret・有効化設定は変えない。
**既存のhardened Docker imageで調査が稼働するとは主張しない。** 依存と隔離の実機検証、適切な配置・キー注入は次の受入工程。

## 検証と残件

```sh
PYTHONPATH=src python3 -m pytest -q tests/test_reply_routing.py tests/test_reply_research.py
```

source subsetで新規86件成功。既存 `discord_chat.py` / `discord_memory.py` / routesはGit blob SHAを照合したものを使用した。
API-only/調査分岐、同じ文面に異なるJEV判定を与えた際の従属、境界・失敗、入力/秘密情報非投影、source境界、出典偽装、実返信入口・dedup・削除中の送信抑止、ローカル子プロセスtimeout/output上限を検証。
Mockの成功をJEVの意味精度、Codex実機、network隔離、API課金、本番反映の成功と混同しない。
既存Discord suiteと新規suiteを専用CI/Docker契約へ接続する。ローカルの完全checkout/全体suiteは取得環境の制約で未実施。
primary checkoutの `handoff.md` は読めず、更新/ops_brief再生成も未実施。運用状態を変えていないためPRを作業引継ぎとする。独立レビューは未実施。

次の工程:
1. 独立レビュー、exact-head CI、合成会話による実JEV判定精度・遅延評価。
2. 専用隔離環境でCodex event schema、読取/書込/ホストHOME/loopback/プロセス終了のnegative canary。未検証のまま本番enableしない。
3. #829の配信コメント共通pipelineへ同じ判断器を接続。通知単独と「通知＋質問」の混在を区別し、可能なら既存JEV分類と同一requestへ統合する。
   legacy bridgeの失敗が通常OpenCode chainへ抜ける経路を塞ぎ、persona/画像/翻訳/guard/ackを維持する。単なるhelper追加やDiscord接続だけで配信側完了とはしない。
4. 必要なら独立したread-only runtime evidence provider、OpenCode研究adapterを追加。JEVに実行権限を付与しない。

参考仕様（2026-10-04確認）: Codex CLI reference / configuration reference、Debian bubblewrap manpage。
- https://developers.openai.com/codex/cli/reference/
- https://developers.openai.com/codex/config-reference/
- https://manpages.debian.org/trixie/bubblewrap/bwrap.1.en.html
