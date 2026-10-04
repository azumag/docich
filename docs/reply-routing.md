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
    ├ web / code / web_and_code
       → 明示承認された隔離Codexによる読み取り専用調査
       → Webは固定4host brokerのreceipt、codeはsnapshot読取・出典を検証
       → 同じHTTP API＋元のpersonaで返答を整形
       → 検証した出典をコード側で付記
    └ runtime / unknown / JEV失敗 / 低confidence
       → 固定の回答保留
 → 既存の削除整合・重複抑止・送信・memory commit
```

調査完了と返答生成を分離し、Codexの人格が会話のpersonaを置き換えない。
JEVにpersona/著者名/ユーザーID/メッセージID/永続記憶/assistant発言を渡さない。現在のuser本文と最大2件の同じ入力内にある過去user本文だけを渡し、見つからないreferentは`unknown`にする。認識できるcredential形式、秘密値の明示代入、メール/Discord ID/self-name表現がその3件に含まれる場合はJEV・research・返信APIのいずれも呼ばず、固定保留にする。JEV本文にアプリ環境を展開せず、transportは選択済みJEV credentialだけを認証ヘッダーに使う。
このローカル保留は一般的なcredential/identity形式を検出し、自由文の任意の秘密値を完全に検出するDLP機能ではない。任意の秘密値を含み得る文面について網羅的保護の受入は未完了。
元のcategory分類の「本文限定」契約は変更しない。新purposeは `reply-evidence-v1`。

## 判断契約

| ラベル | 意味 | 経路 |
|---|---|---|
| `api_only` | 挨拶、反応、祝い、雑談、提示済みの情報だけで十分な返答/推論 | APIのみ |
| `web` | 未知の名称・用語、現在情報、明示的な調査 | 固定4hostのcredential-free取得brokerで本文・引用を検証 |
| `code` | ゲーム、確率、アルゴリズム、実際の実装の説明 | 承認済みソースの調査 |
| `web_and_code` | 公開情報と実装の両方 | Web receiptと承認済みソースの両方を要求 |
| `runtime` | 実際の稼働状態、非公開ログ、障害原因など | 初期実装は未対応と明示。本番権限を与えない |
| `unknown` | 曖昧、必要な根拠が不明 | 固定の回答保留 |

「SSRが出た！」と「このガチャの抽選処理は？」は同じ語を含んでも別の判断になる。
「XXって何？」は短くても調査が必要になり得る。コードの説明を求められたらモデルの想像で済ませない。
この表とテストの例は**判断rubricと経路契約**であり、JEV実APIの日本語精度測定ではない。

`api_only`はJEVが有効なラベルを返しconfidenceが0.80以上のときだけ採用する。閾値は未校正の初期値で、正解率80%の意味ではない。
低confidence、不正回答、timeout、キー欠落はAPI-onlyへ倒さず、Codex/OpenCodeにも昇格せず固定保留にする。`unknown`も調査へ進めない。JEVは既存 `semantic_decision.transport` を再利用し1.5秒・1回。
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
独立レビューで `--unshare-all --share-net` がホストnetwork namespaceを再共有していることを検出したため、`--share-net` は削除した。
user/PID/network/IPC/UTSを分離し、入れ子user namespaceも無効化する。Codex子プロセスは専用PID namespaceのPID 1配下で動かす。
`/usr`等の実行時に必要なOSファイルと証明書、一時workspaceだけを渡す。DNS設定やホストネットワークinterfaceは子に見せない。
ホストHOME、checkout、`.git`、Discord SQLite、VMログ、Docker socket、ホストnetwork namespaceは渡さない。
Codexは `--sandbox read-only --ephemeral --ignore-user-config --ignore-rules`、自動承認なし、subagentなし。
Codexのshell環境継承はnone。bwrapへ渡す環境はPATH、LANG、**調査専用のCODEX_API_KEY**だけで、Discord/Twitch/他用途キーや親のproxyを引き継がない。bridgeが子へ渡すproxyはnamespace内loopback上の固定proxyに限る。
キーと本文はargvへ入れない。本文は匿名一時ファイル経由のstdin、stdoutは上限256KiB、stderrは破棄、終了/失敗/timeout時にプロセス群をkillしてreapする。
調査子プロセスは最大45秒。全体に元のAPI通信が続くため、45秒を返信全体の保証とはしない。

**隔離を満たせない環境で裸のCodexへfallbackしない。** Linux/bubblewrap/Codex/認証/承認済み資料がなければ調査不可。
Codexのnetwork namespaceは専用で、外部interfaceやdefault routeを持たない。信頼済みbridgeだけがnamespace内loopback上でHTTP CONNECTを受け、mode 0600の一時Unix socketを通じてホスト側egress proxyへ中継する。
proxyは`api.openai.com:443`以外を拒否し、DNS回答からglobal IPだけを選んでその解決済みIPへ直接接続する。再解決せず、TLSはCodex側でホスト名を検証する。別hostへのHTTP redirectは次のCONNECTで拒否される。
DNS lookupは固定hostだけを処理する資格情報なし短命process内で行い、3秒でkill/reapする。各proxy handlerはclient/upstreamの両socketを登録し、終了時に両方をshutdown/closeして非daemon threadをjoinする。CONNECT/接続に5秒、relayの無通信に30秒、half-close後の応答待ちに2秒の上限を置く。
namespace内loopbackを上げるためだけにCAP_NET_ADMINをbridgeへ一時付与し、bridgeはCodexを起動する前にeffective/permitted/inheritable/ambient capabilityを落とし`no_new_privs`を設定する。bwrapがこの構成を実際に許可しない場合は失敗扱いで、研究を開始しない。
固定CONNECT・non-global DNS・redirect拒否、idle upstream/half-close後のsocket/thread cleanupに合成negative testを追加した。GitHub Actions Ubuntu 24.04上のbubblewrap probeも実childを起動し、host `127.0.0.1` listenerに接続できないこと、子から見えるinterfaceが`lo`だけであること、CapEff/CapPrm/CapInhが0、`no_new_privs=1`であることを確認する。PR head `512da92f`の必須offline contract jobでこのprobeを含む168 testsが通過した。
このcanaryはsandboxのhost-loopback遮断とchild capability状態を証明するが、外向きegress proxyの実通信、Web使用時のRFC1918/link-local/host-internal拒否、snapshot外のhost file/socket内容が子から見えないこと、Codex API通信と出典忠実性は証明しない。内部宛て拒否の合成testとsandbox mount/env assertionはあるが、これら全経路のLinux実機negative acceptanceは残る。
`DOCICH_REPLY_RESEARCH_ENABLED`と配信routingのfeature flagは本番offのままにする。#829向けに分類・画像・翻訳・persona・safety guard・ack/replyの既存契約と共存するopt-in経路は実装したが、Linux実機のnegative acceptanceと実JEV合成canaryは未完了で、本番での有効化は受入・レビュー完了後に限る。現在のDocker設定の権限は緩めない。

コード調査は、運用者が公開可能と承認した**別ディレクトリのsnapshot**を使う。稼働中checkoutを直接渡さず、snapshotだけを`/workspace/source`へread-only mountする。bridgeとegress Unix socketは別々のread-only mountで渡す。
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
絶対パス/親参照/隠しパス/`.git`を含むcheckout配下/AGENTS/既知のsecret・private・credential・token/passwordパス/証明書鍵拡張子/シンボリックリンク/ハードリンク/非通常ファイル/ハッシュ不一致は拒否する。openはdirfd + `O_NOFOLLOW`で行う。
manifestを作るだけで公開審査や非機密性が証明されたとはしない。作成・配布・更新は運用者の承認が別途必要で、本PRはsnapshotを本番へ配布しない。
path名のallowlistはファイル内容に秘密がないことを自動証明しないため、manifest作成者が内容を審査する。
`revision`はsnapshotの識別であり、本番の配備済みSHAだとは主張しない。

Codexの最終JSONだけでは成功にしない。完了イベント、実際のsearch/readイベント、必要な種類の出典を要求する。
code出典はmanifest内ファイルについての完了済み`cat`/`nl -ba`/単一行`sed -n 'Np'`読取イベント、snapshotの実在行、引用文字列と実読取出力の一致を確認する。他コマンドや複合shell文は出典証拠にしない。
Codex 0.157.1の公式 `web_search` JSONLは`id/query/action/results?`を持ち、`status`や独立`web_open`は存在しない。`results`はopaque JSONなので、検索URL/snippetや自己申告を取得本文として扱わない。2026-10-04のowner明示許可に基づき、**ja.wikipedia.org / en.wikipedia.org / github.com / raw.githubusercontent.com の公開HTTPSだけ**を取得する別brokerを追加した。この許可は本番有効化・有料API試験・任意host追加を含まない。

Web/mixedは既存research flagと`DOCICH_REPLY_WEB_SEARCH_ENABLED=1`の両方が必要で、既定offを維持する。Codexのsearch設定にも同じ4domainを渡す（0.157.1の`tools.web_search.allowed_domains`）。API用CONNECT proxyは`api.openai.com:443`だけのまま。モデルのnamespaceにhost network routeはなく、新しい0600 Unix socketとread-onlyの固定client helperだけを追加する。brokerはCLI JSONLの実`item.completed / web_search / action=search`の結果に現れた正規化URLだけを候補として登録する。agent_message、command stdout内の偽event、open_page、モデルが作るURLでは登録できない。URL metadataは取得許可の候補であって本文証拠ではない。

固定helper `python3 /tmp/docich-web-fetch.py --client <URL>`は`{url}`だけを送る。brokerは任意headers/method/commandを受けず、公開HTTPSの完全一致4host以外、userinfo、非443 port、query、fragment、制御文字を拒否する。未知host、subdomain、IP literalも不可。GETだけで、全redirect（同hostも）を拒否する。専用workerは資格情報・HOME/config/proxyを継承せず`python -I -B`、close_fds、新process groupで起動する。全DNS回答がglobal IPでなければ拒否し、検査したsockaddrへ一度だけ直接接続、TLS SNI/hostname/certificateを検証する。再解決・接続retry・redirect追従はしない。

workerは1取得8秒以内、research全体45秒を共有する。raw body128KiB、UTF-8 `text/plain` / `text/html`だけ、圧縮拒否、抽出text16KiB上限（成功扱いの切詰めなし）、HTTP header行4KiB/32行、最大4取得attempt、最大4handler/1active worker。成功した同一URLはrun内receiptを再利用する。HTMLはscript/style/head/template等を除き、文字列としてのみ扱う。brokerはraw bytesのSHA-256と決定的に抽出したtextを再計算し、immutable receiptを親process内に保持する。Codexが作るfile/stdoutにはreceipt authorityを置かない。終了時にlistener/clientをshutdownし、全worker process groupをkill、communicateでreap、non-daemon handlerをjoinする。

出典受理には、実検索結果のURL、許可済みhelper commandの成功（exit_code=0）、brokerが保持するreceiptとの出力完全一致、最終JSONのURL/receipt/raw-body SHA-256一致、取得text内の引用完全一致を全て要求する。mixedはさらにsnapshot読取・実在行・引用一致が必要。検索metadataだけ、モデルの確認済み宣言、架空web_open、偽receipt、改変hash、未取得/失敗/timeout/上限超過はholdする。資料は命令ではなく、権限・persona・送信経路を変更できない。

照合したprimary sources:
- [exec events](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/exec/src/exec_events.rs)
- [JSONL event conversion](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/exec/src/event_processor_with_jsonl_output.rs)
- [protocol items (opaque results)](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/protocol/src/items.rs)
- [web search tool (result passthrough)](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/ext/web-search/src/tool.rs)

executor上の研究runnerが探索する `/usr/local/bin:/usr/bin` にはCodexがない。local CLIは別のnode install配下で、Linux本番研究binaryのversionは未確認。実Codex調査は行っていない。
この確認で証明できるのは取得イベントと引用文字列の一致までで、引用に対するモデル要約の意味的正確さや全主張の含意は機械判定できない。code schemaは公式0.157.1と照合したが、Linux研究binaryと実CLI調査の受入が必要。

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

## Canary と配信コメント統合

`tests/fixtures/reply_routing_canary.json` に依頼された19件の合成本文と期待scopeを固定する。新しい`scripts/comment_reply_routing_live_canary.py`は同じfixtureを最大8件のbatchへまとめ、各batchでcategory/evidenceを同時質問する。`--live`、`DOCICH_REPLY_CANARY_CONFIRM=I_HAVE_APPROVED_POSSIBLE_PROVIDER_COST`、`DOCICH_ALLOW_REAL_AI=1`、既存route専用keyが全て必要で、複数JEV route/fallback設定なら送信せず停止する。実行時は本文/ユーザー属性を出力せず、ID別scope/status/confidence/batch latency、accuracy/coverage/low-confidence率、平均・p95 batch latencyを記録する。provider failureで残りbatchを止める。今回は課金条件と資格情報の安全性を確認していないため起動しない。mock fixtureはJEV精度の実測ではない。

#829の配信コメント経路へ専用`bin/docich-comment-reply-route`を接続した。既存category `c{i}`と根拠scope `e{i}`を同じJEV要求に含め、画像分類が有効なら`s{i}`も同じ要求に含めるため、通常は分類JEV呼出しを増やさない。コメント本文以外の表示名・persona・保存memory・assistant発言はJEVへ渡さず、表示名やローカル通知カテゴリを認証済み送信元の根拠にしない。認識したcredential/identity形式を含む行はJEVへ送らず、その行の証拠が欠けるためバッチ全体を保留する。

複数コメントは一件ずつ判定し、必要scopeをバッチで統合する。各行のbodyを独立にJEVへ判定させる。runtimeがあればruntime保留、unknown/timeout/不正/低confidence/入力検証失敗があればバッチ返信を保留する。`api_only`は有効JEV回答かつconfidence≥0.80の場合だけ採用する。Twitch/YouTube/Kickの取得上限10行を、JEV上限8行の要求へ分割し、全行の結果が揃うまで最終バッチをreadyにしない。2つ目の要求失敗時も先行8行だけを返信・ackせず全体を保留する。`SSR出た！`単独は高confidenceで相づちと判断された場合だけAPI-onlyになり、`SSR出た！このガチャの確率どうなってる？`は同じ相づち扱いしない。

`code`は既存隔離research adapterで根拠が取得・照合された場合だけ返信を進める。`web`/`web_and_code`は固定4host brokerのreceiptと取得本文・引用の一致が必須で、範囲外URLや未確認時は保留する。Codexの完了自己申告だけで資料を受理せず、Web検索/取得イベントと引用の取得本文完全一致、または承認済みsnapshotの表示出力・対象行との一致を検査する。これは引用位置の確認であり、各説明文の意味的支持を完全自動検証したものではない。runtime、調査失敗、資格情報や隔離不足では固定保留にし、未確認内容を通常生成APIに回さない。根拠資料は命令ではないと明記したJSONデータとして既存返信promptへ追加し、元persona、カテゴリ選択、翻訳、Japanese/output guardを保つ。ルート有効時はピーク順変更後の既存候補を`local`または`local:<model>`の直接HTTP API候補に絞る。main返信・翻訳ともCLI経路は除外し、直接API候補がない、またはAPI生成に失敗した場合は返信を生成せずackしない。通常経路はfeature flagが`0`のままで変更しない。

Soren側の最小統合は[companion PR #580](https://github.com/azumag/soviet_now/pull/580)で、docichのsubmodule gitlinkを動かさず、最新Soren main `2fee0e04`をbaseにした別branchで準備した。primary checkout/submoduleの未コミット変更には触れていない。Sorenの既定`COMMENT_AGENTS`は直接`local`候補を含まないため、flagを有効化しても現状設定のままならJEV前に保留になる。別途設定/secret作成や本番有効化はこの作業に含めていない。

## 検証と残件

```sh
DOCICH_REQUIRE_BWRAP_PROBE=1 PYTHONPATH=src python3 -m pytest -q -rs \
  tests/test_discord_chat.py tests/test_discord_memory.py tests/test_reply_routing.py \
  tests/test_reply_research.py tests/test_comment_classifier.py
```

現行差分のmacOSオフラインsuiteは **281 passed, 4 skipped, 34 subtests passed**。skipは任意Discord SDK未導入、Linux/bwrap canary、Linux Unix-socket/egress-close tests。Soren側の7-module unittest suiteは **89 passed**、screen/runtime suiteは **25 passed, 10 subtests passed**。peak-hours順序回帰、16件のshell回帰、shell syntax/Python compile、diff checkはpass。skipはLinux実機negative acceptanceの代用ではない。

code-bearing commit `2538c7bf` に対する以下のGitHub Actionsはpassした。後続のコード変更と最新main追随後のCIは別途実行し、最終headの結果はPR本文に記録する。

- [offline-contracts run 37179020581](https://github.com/azumag/docich/actions/runs/37179020581): **207 passed, 1 warning, 34 subtests**。Ubuntu+bwrap child probeを含む。
- [docker-contracts run 37179020581](https://github.com/azumag/docich/actions/runs/37179020581): **229 passed, 1 skipped, 1 warning, 34 subtests**。runtime/test image buildとoffline backup/restore contractを含む。skipはtest imageにbwrapがないため、warningはPython `audioop` deprecation。
- [semantic-contracts run 37179020595](https://github.com/azumag/docich/actions/runs/37179020595): semantic core **123 passed**、classifier/screen/canary **142 passed**。
- [security-regressions run 37179020578](https://github.com/azumag/docich/actions/runs/37179020578)、[comment-regressions run 37179020589](https://github.com/azumag/docich/actions/runs/37179020589)、[prediction-regressions run 37179020586](https://github.com/azumag/docich/actions/runs/37179020586)、[python-syntax run 37179020582](https://github.com/azumag/docich/actions/runs/37179020582)もpass。

現実行hostはDarwinで`bwrap`なし。Docker CLIはあるがDocker daemon socketへのアクセスはpermission deniedで、ローカルcontainer suiteは実行できなかった。hostのsecurity/network設定やsocket権限は変えていない。従ってGitHubのbwrap child probeは通過したが、Linux実機でのegress経路全域、host HOME/DB/log/socket到達不可、Webからのhost loopback/internal service非到達を一連の実機環境で受入したとは主張しない。GitHub Docker contractもreply research sandboxの実機network受入を代替しない。

固定CONNECT authority、nonpublic IPv4/IPv6/metadata拒否、DNS解決後の同IP直結、redirect拒否、専用key以外の環境非継承、子起動前capability drop、idle upstream half-close時のsocket/thread cleanupはmock/合成negative testsで固定する。これらはLinux実機negative acceptanceの代わりではない。JEVの実scope/confidence/latency、Codex API通信、host network/internal service拒否の全経路受入、本番反映は未実施。
19件fixtureは `api_rewrite` に「さっきの説明もう少し短くして」を置くが、現状はflatなuser textで、直前assistant説明を含む会話形を再現しない。JEVへassistant本文を渡さない境界は維持する。API-only時の最終APIには元の会話履歴を渡し、直前回答は書き換え対象の文面として使うが、事実確認済み根拠として採用しない。このrewrite例でのJEV意味精度は未測定で、会話構造を含む追加canary/評価が残る。
primary checkoutの `handoff.md` relevant sectionsを読了した。運用状態は変更していない。実JEV canaryは課金条件と既存認証の安全性をこの環境で確認できないため実行していない。合成fixtureを使うmockテストのaccuracyはJEV精度として扱わない。

次の工程:
1. 課金条件/既存key利用安全性を確認し、親が受入未完了をReady阻害とするか判断した後にだけ、合成JEV canaryを実行する。
2. 専用Linux/bwrap環境でhost HOME/secret/Discord DB/VM log/socket/他repo、host loopback/internal service、外向きegress policy、timeout process-tree reapのnegative acceptanceを行う。未検証のまま本番enableしない。
3. Soren側の直接API候補設定は現在の既定値にない。変更せず、運用者の承認・別途設定前はroute flag offを維持する。
4. 独立read-only runtime evidence providerとOpenCode research adapterは別途設計する。JEVに実行権限を付与しない。

参考仕様（2026-10-04確認）: Codex CLI reference / configuration reference、Debian bubblewrap manpage。
- https://developers.openai.com/codex/cli/reference/
- https://developers.openai.com/codex/config-reference/
- https://manpages.debian.org/trixie/bubblewrap/bwrap.1.en.html


### 配信キューの合成E2E

`tests/test_comment_queue_e2e.py` はcompanion PR #580の固定treeを読み、Twitch/YouTube/Kickの実fetch script、`generate_comment_response`、docichの実combined classifierと8行chunking、Sorenの実envelope readerとqueue/ack/dedupを接続する。9行と10行、次の10行、空の次fetch、第二chunk timeout時の全pending維持と再試行成功を検査する。生成器、音声/長期context/adviceはfixtureで、JEVは注入した決定的transport。credentialを継承せずコピーはsource allowlistのみ。ネットワーク/実API/意味精度/本番送信の検証ではない。timeout後のretryは独立fixture stateを使い、既存provider cooldown試験と分離する。CIでは固定companion SHA checkoutを必須にしてskipを許さない。


### 固定4host brokerの検証範囲

`tests/test_reply_research_web.py`はmock DNS/socket/TLS/HTTP responseとローカルPython childだけを使う。SSRF、混在private DNS、pinning、TLS mismatch、redirect、MIME/charset/encoding、body/frame/text上限、hash/URL/text改変、candidate gate、timeout kill/reap、JSONL streaming、Web/mixedのreceipt一致を固定する。Linux CIではUnix listener/clientの成功、wireによるheaders/method/command/host拡張拒否、broker exit中のworker cancel/joinも必須実行する。Darwin executorではUnix listener bindが拒否され、その6件はLinux専用skipとする。

これは実ネットワーク取得・実Codex API・Linux本番hostの全negative acceptanceではない。実CLIがhelperへのUnix接続を許すか、配備先CLI version/search結果DTO、4hostの実ページサイズ/MIME/redirectに対する成功率とlatencyは未確認。制限で取得不能の場合はholdし、sandboxを緩めたり裸CLI/通常生成へ抜けたりしない。本番・有料API試験は別の明示許可を必要とする。
