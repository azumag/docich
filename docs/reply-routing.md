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
       → 実行イベント・出典を検証
       → 同じHTTP API＋元のpersonaで返答を整形
       → 検証した出典をコード側で付記
    └ runtime / unknown / JEV失敗 / 低confidence
       → 固定の回答保留
 → 既存の削除整合・重複抑止・送信・memory commit
```

調査完了と返答生成を分離し、Codexの人格が会話のpersonaを置き換えない。
JEVにpersona/著者名/ユーザーID/メッセージID/永続記憶/assistant発言を渡さない。現在のuser本文と最大2件の同じ入力内にある過去user本文だけを渡し、見つからないreferentは`unknown`にする。
元のcategory分類の「本文限定」契約は変更しない。新purposeは `reply-evidence-v1`。

## 判断契約

| ラベル | 意味 | 経路 |
|---|---|---|
| `api_only` | 挨拶、反応、祝い、雑談、提示済みの情報だけで十分な返答/推論 | APIのみ |
| `web` | 未知の名称・用語、現在情報、明示的な調査 | Codexの公開Web調査 |
| `code` | ゲーム、確率、アルゴリズム、実際の実装の説明 | 承認済みソースの調査 |
| `web_and_code` | 公開情報と実装の両方 | 両方の根拠を要求 |
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
このegress bridgeとDNS/redirect拒否には合成negative testを追加したが、Linux/bwrap上の実受入は未確認。PRの既存`c9ceb203`で記録されたGitHub Actions Ubuntu 24.04 canaryはchildを起動し、host側の`127.0.0.1` listenerへ接続できないことを確認した。これはその時点のloopback負例だけを証明し、新bridge、RFC1918/link-local/host internal到達不可、Codex API通信を証明しない。
従って `DOCICH_REPLY_RESEARCH_ENABLED` は引き続き本番offとし、配信コメントへのrouting接続もこの受入が終わるまで進めない。現在のDocker設定の権限を緩めない。

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
Web出典は完了済み検索結果のURL、完了済みページopenのURLと本文、出典JSONに含む引用文字列の完全一致を確認する。URLはHTTPSに正規化し、資格情報・秘密らしいquery・非443 port・IP addressを拒否する。
この確認で証明できるのは取得イベントと引用文字列の一致までで、引用に対するモデル要約の意味的正確さや全主張の含意は機械判定できない。実Codex JSONL event schemaは未確認で、不一致の場合は調査不可になる。JEV/CLI実機の受入が必要。

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

`tests/fixtures/reply_routing_canary.json` に依頼された19件の合成本文と期待scopeを固定する。既存の`scripts/reply_routing_live_canary.py`は明示的な`--live`と`DOCICH_REPLY_CANARY_CONFIRM=I_HAVE_APPROVED_POSSIBLE_PROVIDER_COST`の両方がなければ送信しない。
実行時は本文/ユーザー属性を出力せず、ID別scope・status・confidence・latency、全体accuracy/coverage/low-confidence件数/平均・p95 latencyを記録する。provider failureで残りの要求を止める。今は課金条件と資格情報の安全性が確認できないため起動しない。テストfixture/モックはJEV精度の実測ではない。

#829の配信経路はこのPRでは未接続。作業用worktreeでは`games/soviet_now` submoduleを初期化していない（このPRのgitlinkは`2bb57e6c04f235557c9e5e3376948b5f11d4d9b0`）。そのためlegacy `broadcast/comment.sh` / `lib/ai_generate.sh` の現在の呼出し・fallbackを実コードで確認できず、そこでAPI/provider失敗が通常CLI chainへ抜けないとも主張しない。primary checkoutの該当submoduleには別作業の未コミット変更があるため読取検証に使っていない。

安全なPR-3e案は、各eligible commentのcategory `c{i}`とscreen `s{i}`にevidence scope `r{i}`を同じ`build_request`へ足してJEVのHTTP callを1回に保つこと。自動通知単独は既存`NOTIFICATIONS`保護のまま除外し、同じ本文に質問が続く場合は別の`r{i}`で質問範囲を判定する。API-onlyはconfidence≥0.80のみ。timeout/invalid/low-confidenceならその対象の返信を保留し、通常`ai_generate_list()`で穴埋めしない。runtimeは現在のscreen OCR/evidenceだけで代替せず保留する。

根拠が検証できた場合だけ既存のprompt/persona・画像/context・translation・Japanese/safety guard・ack/retry/dedup/delete-suppression・単一送信契約へ資料として渡す。classificationとevidenceを同じrequestに入れる統合点は#829 PR-3e orchestrationが用意された後に設計し、今はlegacy shellやsoviet_now submoduleを変更しない。

## 検証と残件

```sh
PYTHONPATH=src python3 -m pytest -q tests/test_reply_routing.py tests/test_reply_research.py
```

CIと同じsuite `DOCICH_REQUIRE_BWRAP_PROBE=1 PYTHONPATH=src python3 -m pytest -q -rs tests/test_discord_chat.py tests/test_discord_memory.py tests/test_reply_routing.py tests/test_reply_research.py` はmacOSで **164 passed, 4 skipped, 34 subtests passed (1.04s)**。skipはDiscord SDK未導入、Linux/bwrap canary、Linux Unix-socket/egress-close tests。
変更後の同suiteを既存 `docich-discord-chat:offline-verify-test` image (`sha256:87ebf148fdfbd4281a22dcc075e54e3e091e2a549604e7878bbe22c48171f1db`)内で、合成source/test bundleをstdinから展開して**host mount/secret/socketなし**で実行: `docker run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --tmpfs /tmp:rw,noexec,nosuid,nodev,size=128m --env DOCICH_ALLOW_REAL_AI=0`。Linux container結果 **167 passed, 1 skipped, 1 warning, 34 subtests passed (1.23s)**。新しいidle-upstream/half-close/proxy-exit regressionを含むUnix socket testが通過。唯一のskipはtest imageにbwrapがないためのLinux/bwrap canary。warningはtest SDKのPython 3.12 `audioop` deprecation。GitHub ActionsのUbuntu+bwrap実probeとDockerfile full verify/Compose testとは区別する。
固定CONNECT authority、nonpublic IPv4/IPv6/metadata拒否、DNS解決後の同IP直結、Linux Unix socket mode、redirect host拒否、secret環境非継承、子起動前capability drop、idle upstream half-close時のsocket/thread cleanupをmock/合成negative testで固定する。bwrap実機 canaryは名前空間内loopback起動、host loopback拒否、interface分離、子のcapability/no_new_privsを検査し、GitHub Actionsでは必須child probeにする。
Mockの成功をJEVの意味精度、Codex実機、network隔離、API課金、本番反映の成功と混同しない。JEVの実ラベル/latencyとLinux/bwrap実機受入は未実施。GitHub ActionsのこのHEAD上の結果はpush後に記録する。
primary checkoutの `handoff.md` relevant sectionsを読了した。運用状態は変更していない。

次の工程:
1. 課金条件/既存key利用安全性を確認後、親の明示承認を得て合成JEV canaryを実行する。
2. 専用Linux隔離環境でCodex event schema、read-only snapshot、host HOME/secret/socket/loopback/internal/network/timeout process-treeのnegative canaryを行う。未検証のまま本番enableしない。
3. #829の正確なshell契約を安全なisolated sourceで確認してから、PR-3e orchestrationに同request evidence routingを接続する。API/provider failureを通常CLI chainへfail-openさせないテストを先に作る。
4. 必要なら独立したread-only runtime evidence provider、OpenCode研究adapterを別途設計する。JEVに実行権限を付与しない。

参考仕様（2026-10-04確認）: Codex CLI reference / configuration reference、Debian bubblewrap manpage。
- https://developers.openai.com/codex/cli/reference/
- https://developers.openai.com/codex/config-reference/
- https://manpages.debian.org/trixie/bubblewrap/bwrap.1.en.html
