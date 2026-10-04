# 会話に必要な根拠による返信ルーティング

## オーナー方針（2026-10-04）

軽い会話は既存API、調査は既存OpenCodeを使用する。広い公開Web（一次資料・ニュース）と承認済みコードを対象にする。根拠不足なら別の検索・資料を試し、時間・回数上限後に確認できた範囲と不足理由を返す。対象が曖昧なら聞き返す。Codex実行・Codexへのfallbackは除外する。

本番flagは既定off。新しい有料検索契約、鍵作成、権限変更、本番有効化、worker/配信/ゲーム再起動はこのPRで行わない。独立レビューと実受入前はDraftを維持する。

## 判定と最終回答

JEVの固定scopeは `api_only / web / code / web_and_code / runtime / unknown`。質問の長さやキーワード数ではなく、正確に答えるため会話外の根拠が必要かを判定する。API-onlyには有効なJEV結果と有限confidence ≥ .80が必要。分類と画像分類・カテゴリ判定は既存combined requestへまとめる。

### reply-evidence-v2（意味精度は未実測）

public facts・project implementation・現在のlive/private観測の必要性を独立に判断し、その和集合でscopeを選ぶ。Webだけならweb、実装だけならcode、公開規則との実装比較や適合確認にはweb_and_code、現在の観測が必要ならruntime。code/web候補は必要な根拠がその一種類だけの場合を表す。Discordと配信combinedは同じ候補定義・説明を使う。

対象の名前が省略されても必要な根拠の種類が明らかなら、その種類を保つ。必要な種類自体が分からなければunknown。会話の書き換え依頼は、privacy投影に過去assistant本文がないことだけを理由に外部事実の確認へ変えない。過去assistantの主張を確認済み事実として採用する許可ではない。

v1の実測で「公式仕様とdocichの実装は一致してる？」がconfidence .88でcodeに分類された。観測した誤判定をfixtureへ保存し、codeのみ/webのみ/両方/曖昧を各3件、合わせて12対照ケースを追加した。mockは送信rubric・選択後の経路・mixedの両種類の引用要件を検査し、v2のJEV精度を証明しない。閾値.80は維持する。

JEVへ渡すのは直近のuser本文だけ。persona、表示名、userID、過去assistant発言、不要な長期記憶、環境は投影から除く。既知credential/明示secret assignment/identityパターンはprovider前にfail-closedする。これは任意の機密文字列を完全に検知するDLPの保証ではない。

分類timeout/低confidence/invalid/provider failureをAPI-onlyに変換せず、調査CLIへの昇格理由にも使わない。unknownは固定の対象確認質問、runtimeは現在の観測がなく確認できない説明を返す。ソースから現在のVM・配信状態を推測しない。

最終回答は既存のAPI＋canonical persona。取得引用は参考データとしてuser messageへ挿入し、system/personaを置換しない。取得資料にない事実を補わず、不足を明示する。実取得のreceiptは出所を示すが、資料内容の真偽や質問全体の解決を保証しない。

## OpenCodeとcontrollerの分担

既存 `opencode:<model>` / `opencode-go:<model>` のprovider/model対応を再利用する。通常dispatchの環境継承・fallback runnerは研究用に流用しない。現在の研究adapterは既存OpenCode/Goの公開 `opencode.ai` profileに限定する。他の既存AMD/Vercel/internal proxyは、隔離したprofile/既存認証の確認なしに追加しない。

operatorが既存の調査用認証を `DOCICH_REPLY_OPENCODE_API_KEY`、既存モデルを `DOCICH_REPLY_OPENCODE_MODEL=opencode/<model>` または `opencode-go/<model>` として選択する。認証ファイルや既存HOMEをmountしない。モデルはJEV/モデル出力から選ばせない。値の探索・新規作成・本番設定は行わない。選択済み認証がなければ調査不可と説明する。

OpenCodeは全tool denyの固定 `docich-evidence` agentで、JSON提案だけ返す。bash、edit/write、subagent、skill、任意MCP/pluginを許可しない。builtin websearch/webfetch/readも実行させない。許可される提案は検索語、検索結果候補URL、manifest内ファイルと行範囲、引用選択、確認質問の固定schema。実処理は親controllerが検証後に行う。JSONLのtool lifecycle/error/未完了/重複keyを拒否する。

親controllerは最大8 model rounds、異なる検索2回、本文取得4件、コード読取4回（各80行）。Discordの研究分岐は分類開始から研究・最終回答まで45秒の共通deadlineを使い、最終回答も残時間で監督する。API-only分岐の既存動作は維持する。モデルのnotes/「確認済」自己申告は採用しない。成功には親が取得した本文receipt/hash/完全一致引用、またはmanifest hash/実読取行/引用一致を要求する。mixedは両種類が必要。一方のみ確認できた場合は検証済み引用だけをpartialとして保ち、不足を明示する。

## 登録済Goのnative HTTP transport（2026-10-05、opt-in）

`DOCICH_REPLY_RESEARCH_TRANSPORT=api` は既存 `ChatBackend._complete_api` を共用する固定Python workerを、同じbwrap/network namespace/CONNECT bridge内で実行する。unsetは既存`cli`、未知値はfail-closed。CLI不在でもAPI transportは使えるが、bwrap/Pythonの欠落時は裸HTTP workerへfallbackしない。

初期対応は `DOCICH_REPLY_OPENCODE_MODEL=opencode-go/deepseek-v4.1-flash` かつ有効なoperatorの`AI_COMMON_AGENTS`にliteral `opencode-go:deepseek-v4.1-flash` が登録されている場合だけ。未解決chainや他model/providerは拒否する。接続先は固定Go `https://opencode.ai/zen/go/v1/chat/completions`。明示選択された既存research keyだけを渡し、auth.json/HOME/秘密設定を探索しない。新model登録、設定変更、新credentialsはない。

各proposalは送信前に1回枠を予約し、1研究につき最大8 POST、retry/fallback0、`max_tokens=256`、JSON request16KiB/response64KiB/content8KiB。finish_reason=stop、非tool、出力usage上限、重複key/NaN拒否後に既存proposal parserへ接続する。truthful User-Agentとrun内で一定のランダムsessionを使う。CLI側の内部retryをこの8 POST保証に含めない。

namespaceには公開worker/chat/memory/packageの4ファイルだけをread-onlyで追加mountする。snapshot/checkout/DB/persona/host設定は追加mountしない。親controllerが検索・本文取得・コード行読取とreceipt/引用照合を行う契約は維持する。最終回答は同じoperator設定のAPI・元のpersona/messagesで1回だけ実行し、残時間でkill+reapするprivate workerへ渡す。Discord token・memory path・研究key・ambient proxyを回答workerへ継承しない。一般の注入callback利用者はbounded callbackを渡した場合に限り最終回答の壁時計上限を強制できる。

合成fixtureは実`ChatBackend.complete → routing → research(api) → native HTTP → coordinate → broker receipt → quote verification → bounded answer`の接続、invalid body hash/引用/receipt/command/provider失敗時の回答抑止、送信前cap、同じsession、persona保全を検証する。HTTP/model/JEVとbwrap実行をfixtureにしたこの接続検査は意味精度・実provider・本番隔離の成功受入ではない。別のlocalhost HTTP fixtureは実回答workerの1 POST・timeout・終了/reap・限定環境を検査する。Ubuntu CIでは両transportの実bwrap negative probeとnative公開modulesのnamespace内実行を必須にする。

先の本人承認済みGo単発疎通は1 POST/HTTP200/入力34・出力15tokens、API1665ms、reply OKだった。これは直接APIのみで、検索・JEV・persona返信のE2Eではない。承認枠は消費済みで今回の追加有料callは0。IANA公式登録の短い本文を合成fixtureへ使い、RFC broker拒否を回避する検証条件変更は行っていない。実IANA本文取得と有料E2Eは未実施。配信companionのGo API生成対応・rolloutは独立範囲で、この接続だけで配信全体完了にしない。

## 広いWebの安全条件

旧4host allowlistを撤廃。検索は公式OpenCodeと同じ既存Exa hosted MCPの固定 `https://mcp.exa.ai/mcp` に、認証なし・固定 `web_search_exa` requestを送る。新しい検索キー、有料契約、Google scrapingは追加しない。未確認の契約条件・料金を無料と断言しない。後続のkeyless公開preflightは検索1回・RFC本文GET1回で候補を取得したが、本文brokerは拒否しreceiptは得られなかった。拒否原因は旧診断から確定できず、上限・SSRF・charset等の検証を緩和しない。

検索結果は候補選択だけで、snippetを本文根拠にしない。モデルが勝手に提案したURLは検索候補登録なしでは取得不可。本文workerはcredential-freeで固定GETのみ。HTTPS443、認証userinfoなし、秘密queryなし、control/backslashなし。全DNS回答がglobalであることを検査し、multicast/reserved/IPv4-mapped/6to4/Teredoを除外。検査したsockaddrへ直接接続し再解決しない。TLS hostname/証明書を検証、redirectは追わない。

workerはproxy/cookie/Authorization/親環境を継承せず、localhost/RFC1918/link-local/metadata/internal servicesに接続しない。MIMEはUTF-8 plain/html、本文128KiB、抽出text16KiB、1取得8秒。大きい記事、redirect、認証/paywall、PDF、他charset等は取得不可として別資料を試す。上限後は不足説明する。短命プロセスのtimeout/outputlimit/successすべてでprocess group kill＋reapする。search workerも同じDNS/TLS/deadline/環境境界を使う。

## 外側隔離

Linux/bubblewrap必須。user/PID/net/IPC/UTS namespaceを分離し、nested user namespaceを禁止する。host HOME/.git/Discord DB/Docker socket/VM logs/secret/非snapshot repoをmountしない。OpenCodeは承認snapshot自体も見ず、controllerが選択された読取範囲だけをデータとして渡す。snapshotはoperator管理のmanifest/revision/hashを検証し、symlink/hardlink/dotfile/AGENTS/private/credential pathを拒否する。

OpenCodeの外向き経路はnamespace内loopbackの固定CONNECT bridgeだけで、公開 `opencode.ai:443` のモデルAPIに限定する。Web workerは別プロセスで検索・公開本文のみ取得する。bridgeはloopback初期化後、OpenCode起動前に全capabilityを落としno_new_privsを設定する。host書込は不可、CLIのsession/cache書込は匿名の一時HOME/tmpだけ。空XDG/config、share/snapshot/autoupdate無効、project/外部rules無効。隔離・依存が欠けても裸CLIへfallbackしない。

通常COMMENT/RADIO dispatchでもCodex specをrejectし、retired adapterからプロセスを起動しない。Soren compatibility runnerのCodex実行関数もfail-closedする。ゲーム/履歴中の文字列や旧設定名を消すことと、実行除外は区別する。

## 配信コメントの有限終端

既存persona/カテゴリ/画像/翻訳/safety guard/queue dedup/単一送信/削除中抑止を維持する。API-onlyの最終生成候補は既存direct HTTP `local[:model]` に限定し、研究失敗から通常CLI chainへ抜けない。

分類失敗、unknown/runtime、研究失敗、最終API候補不足は固定の不足説明または確認質問に終端する。固定文にも既存guard/queue/ackを適用し、生成providerは呼ばない。ackはqueue投入・既存dedup処理が成功してから行う。研究成功時は検証済み引用だけを既存reply promptへ加える。

同じbatchの検証済みroute envelopeをprivate既存stateへ原子的にcacheし、後の配送失敗で分類・調査をやり直さない。配信コード上のholdは未確認自由生成の禁止を意味し、silent pendingの無限調査retryを意味しない。配送障害そのものは既存queue運用の対象で、無条件ack/ユーザーメッセージ破棄をしない。

## 検証の区別と残件

### 承認済みv1 canaryと低confidenceの分析

canaryの`complete`/exit 0は全requestの正常応答を条件にする。最終個別ケースや最終combined batchのprovider失敗でも、その失敗を`terminal_failure`に保持してexit 2となり、retryしない。`requests_attempted`と`requests_succeeded`を区別し、`cases_measured`には正常応答したケースだけを数える。低confidenceは測定済みとして数えるが、coverage/意味精度の成功には数えない。

[秘密・identityなし測定記録](evidence/reply-routing-canary-2026-10-04.json) はHEAD c2849616 / reply-evidence-v1で、合成19個別＋8/8/3combinedの3、計22 POSTを一度だけ実施した結果。全POST正常応答、retry/fallbackなし。公開単価でのusage-basedモデル料金概算は$0.00124593（input29,665 tokens）。請求書や残高を照合した値ではない。22回枠は消費済みで再測定しない。

Discordは有効10/19・正解9/10・低confidence9/19、mean332.782ms/p95464.460ms。combinedは有効15/19すべて正解・低confidence4/19、mean421.264ms/p95440.259ms/batch。低confidenceはholdされ、API-onlyやresearchへ昇格していない。

Discordの低confidenceはreaction/rewrite、伏字や参照先がないWeb質問、卵条件、mixed比較、現在の配信、通知＋質問にまたがる。combinedでは通知2件・料金・通知＋質問だった。同文SSR通知2件もcombinedではconfidence .54/.34で、閾値を下げる根拠にはしない。

v1の広いcode定義・短いmixed定義の競合と、「missing referentならunknown」と会話書き換えとの衝突を原因候補としてv2で整理した。batch内の他質問やcategory/evidence同時評価の影響も候補だが、この一回の観測だけでは原因を確定できない。v1低confidenceの候補label/確率分布は当時のcanary出力に保存されておらず、推測で補わない。

### 承認済みv2の13ケースcanary

[合成測定記録](evidence/reply-routing-canary-v2-2026-10-04.json) はcode HEAD `f4c39ec6` / `reply-evidence-v2`。旧22枠と別の15 POST・$0.05承認枠で、観測mixed事例＋12対照の13個別と同13件8/5combined2を一度だけ実施した。単一direct / `jev-1.13.0`、15/15正常応答、retry/fallback/再実行0、残POST枠0。送信JSON最大31,343 bytes（上限32,768）、受信上限131,072 bytesを維持した。実会話/PII・研究・最終生成は含まない。

観測mixed事例は個別`web_and_code/.92`、combined`web_and_code/.98`となり、この一回では旧`code/.88`誤判定を再現しなかった。個別は12/13有効・有効分12/12正解・低confidence1/13（7.69%）、combinedは13/13有効・13/13正解・低confidence0。個別Webhook質問だけraw choice `web` / confidence .64（probabilities web .70 / unknown .16 / code .10 / mixed .04）でhold。閾値.80を維持しAPI-only/研究へ昇格しない。生の候補・confidence・確率分布をtransport検証後の形で記録した。

mean/p95 latencyは個別363.141/625.637 ms、combined batch519.012/570.148 ms。入力22,988 tokensのモデル料金概算は$0.000965496、事前保守見積は$0.04128768。公式入力$0.042/百万・出力無料に基づき、請求額・残高との照合ではない。

これは選んだ13ケースの一回の観測であり、元の19件やSSR通知、API/runtimeケースをv2で再測定した結果ではない。分布が異なるv1/v2全件率を改善率として比較しない。反復時の安定性、実会話分布、実OpenCode/Exa、最終回答、production全negative受入は未確認でDraftを維持する。

オフライン合成テストはルーティング・取得照合・SSRF拒否・上限・process cleanup・実queue/ackを検査する。JEVの意味精度、実OpenCodeのmodel/API通信、Exaの実結果schema、ニュース取得成功率、実Linux hostの全negative受入を証明しない。

このDarwin executorのLinux/bwrap受入は未実施。GitHub Ubuntu CIではcredential-free外側bwrap negative probeを必須にする。旧Codex binary/probeのdownload・実行をCIから削除した。実OpenCode＋model fixtureの受入は追加確認が必要で、実API費用を伴うcanary/本番有効化は親の別承認を必要とする。

VM棚卸し結果・運用影響はprivateな親引継ぎで扱い、公開文書には記録しない。

### 任意のresearch診断

`research(..., diagnostic=callback)` は、CLI spawn/running/exit/reaped、model呼出し境界とproposal検証、固定failure理由を任意callbackへ渡す。providerのJSONL errorは既知のerror名・HTTP status整数・transport code・固定分類だけを投影し、observerが拒否する前にも分類を保存できる。終了コード・経過msは上限付き整数で、prompt・argv・環境・PID・stderr・生成文・message/body/header/path/URLは含めない。providerの自己申告は診断metadataであり、HTTP POSTの実測や引用取得の証明ではない。

既定ではcallbackも永続logもない。診断だけのJSONL解析は返り値を変えず、callback失敗も既存の失敗時結果・kill/reapを変えない。proposal不正は内側の固定理由を残し、最終結果は従来どおり`research_unavailable`とする。資格情報・sandbox・egress/model/source allowlist・API fallback・personaの変更はない。単体テストはローカル合成Python子プロセスだけを使用する。

先に承認された無料モデル→検索→返信の一回は`research_unavailable`で終了し、CLI起動1回、観測model step開始/終了0、検索/本文取得/返信/JEV呼出し0だった。実model HTTP POST回数は未測定。旧記録にstderr・終了コード・失敗stageがなく、原因は復元できない。今回の診断追加は再実行の承認を含まず、実CLI/model/search/reply/JEVは追加実行していない。実検索・最終返信の成功受入は残件で、Draftを維持する。

参考: [OpenCode tools](https://opencode.ai/docs/tools/)、[permissions](https://opencode.ai/docs/permissions/)、[config merge](https://opencode.ai/docs/config/)、[official MCP search transport](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/mcp-websearch.ts)。仕様は実機の版と一致確認が必要。


配信batchはDiscordの直近3turnと別契約で、最大10件の調査対象を欠落なく投影する。全対象について必要なscopeの実取得引用の対応を検証し、不足があるbatchをreadyにしない。固定終端はplatform/batch単位で投入・再生dedupし、異なるbatchの同本文を抑止しない。claim拒否だけでackせずpendingを保つ。legacy bug dispatcherとstrategy Codex分岐は起動不可。podcast経路は明示オーナー例外として対象外にし、機能を保持する。
