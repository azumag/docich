# 半熟英雄の定期実況（15分ごとの進行レポート）

半熟英雄のプレイ中、およそ15分に1回、進行・直近の決定・確認できた出来事を
LLMに整理させて読み上げる。チャットやSorenコメントは投稿しない。入力BOT
（`brains/hanjuku/bot.py`）は生成にも音声I/Oにも関与せず、cornerの観測ループが
スケジュールと材料収集だけを担当する。

## なぜBOTではないか

操作BOTは1.5秒ごとの判断に集中し、生成待ちや音声I/Oをしてはいけない。
定期実況は「試合の進行を俯瞰する」処理であり、判断材料でも入力でもないため、
`retro_corner._wait_hanjuku` から独立した一回workerとして起動する。
BOTが落ちても、BOTを再起動しても、定期実況の都合で入力が止まらない。

## 設定

`config/games/hanjuku-hero.toml`:

```toml
[hanjuku.periodic_commentary]
enabled = true
interval_s = 900          # 15分。300〜3600
retry_s = 300             # 生成失敗・重複時の再試行。60〜1800
generation_timeout_s = 210 # 1回のLLM呼出しの上限。5〜240
max_attempts = 1           # 長文は1回の生成で3候補。失敗時は次の間隔で再試行
agents = "opencode-go:deepseek-v4.1-flash,..."  # 空なら従来の共通chain
persona = "半熟英雄を長く見守ってきた、落ち着きと熱量のある実況者。…まず結論を明確にし、比喩は1つまで…"
```

`persona` は実況の語り口を固定する最大500文字の説明。事実を曲げる権限ではなく、
評価・反省・ユーモア・締め方の人格を揃えるためのもの。

`enabled=false` で新しい要求を止める。workerは生成前・配送直前に実設定を
読み直すため、生成中の無効化は配送前に無音へ落ちる。音声queueや配信は
再起動しない。設定の変更は、次にcornerがdueを確認した時点で反映される。

## いつ話すか

- corner開始から `interval_s` 後が最初の期限。以後は最後に読み上げた時刻から
  `interval_s`。
- 期限に達しても `phase=battle` の間は最大 `DEFER_MAX_S=180` 秒まで待ち、
  30秒刻みで再確認する。戦闘が長引く場合は期限後180秒で話す。
- 生成・検証・配送の失敗は `retry_s` 後に再試行する。2回続けて失敗したら
  次の通常間隔まで待つ。
- 材料がない、ランが終了した、runtime identityが変わった場合は無音。
- 決まった時刻に必ず話す保証はない。LLMの失敗、queueの混雑、重複判定で
  間隔が空くことはある。

## 材料（whitelist）

`hanjuku_run.json`、`hanjuku_progress.json`、`hanjuku_bot.json` のpolicy、
`hanjuku_decisions.jsonl` の直近windowから、次の項目だけをboundする。

| 項目 | 内容 |
| --- | --- |
| `progress` | クリア済み章、現在章、年、月、chart edition |
| `window` | 経過分、window内の戦闘数・勝・敗・城の獲得・城の喪失・出撃数 |
| `events` | 直近の戦闘結果、城の所有変更、出撃、月次計画、購入、補充、chart調整など最大3件（プロンプトでは背景2件まで） |
| `focus` | この15分の戦略判断を1件。切り札、出撃、防衛、経済、チャート、戦闘のいずれか |
| `current` | focusに必要な現在戦闘・現在の狙い・経済だけ。通算成績は出さない |

`current.economy.captured` は現在の所有観測が `own` の城だけを使い、過去の喪失listを
そのまま現在の保持城として出さない。
raw OCR、`reason`、`decision_id`、runtime identity、path、自由文の内部判断は
材料に入れない。出来事は決定ログのwhitelist済みfieldだけを使い、計画
（`order_start`など）と確認済み結果（`battle_result`、`castle_*_observed`、
支払い済み補充）を区別する。

## LLMに考えさせる

1回の要求で、5〜9文・500〜800文字（検証は300〜1000文字）の候補を3つ作らせる。
単なる事実の列挙ではなく、次の5つを一つの物語としてつなぐ。

1. `focus` の判断（何を狙い、なぜその判断をしたか）
2. その判断の良かった点と、課題・反省・次に活かせる改善点
3. 他の出来事は背景として最大2個だけ（全部を列挙しない）
4. 将軍や城のドラマ。ユーモアは自然に入る場合だけ
5. 視聴者への前向きな締め

最初の2文で「今回の焦点は…」と、誰が・何を・どう判断したかを明言する。
`focus.plain` と `focus.choice` を言い換え、`focus.why` を説明する。
名前（将軍・城・札）はfocusと背景を合わせて最大6つ、1文1話題、比喩は1つまで。
結果が未観測なら「まだ分からない」と明言する。

`focus` は決定ログから、切り札・出撃・防衛・経済・チャート・戦闘の6カテゴリを
対象に1件選ぶ。直近12回の `focus_history` で使ったカテゴリを避け、最も古い
カテゴリを優先する。候補がない場合は `current.order` を「次の狙い」として使う。
これにより、毎回同じ機能の解説になることを抑える。
`focus_clear` は、具体的な将軍・城・札があるfocusで、その名前が最初の2文に
ない候補を拒否する。言及する固有名詞が6個を超える候補も拒否する。
直近の読み上げから10分未満の再試行では生成せず、最低10分の材料が貯まるまで
待つ。「この4分」のような短すぎる振り返りを避ける。
`current` はfocusに必要な項目だけを残す（戦闘focusならbattle、出撃/chartなら
order、経済focusならeconomy）。通算成績は材料から外す。

プロンプトには `persona`、直近8件の読み上げ済み本文、whitelist済みの状況を渡す。
「何が起きたか→それが何を意味したか→次にどう活かすか」を求め、
「まず」「次に」「また」で機械的に並べる列挙や箇条書きを禁じる。
数字は焦点と印象的な1〜2個に絞らせ、出来事の網羅より因果と意味を優先する。
どの候補も「状況」にない将軍名・城名・札名・数値・勝敗・城の出入りを使えない。

## 毎回同じ文にしない保証

配送済み本文はruntime内の `hanjuku_periodic.json` に最大24件保存する。
候補はNFKC正規化して空白・句読点を除き、次のいずれかで「似すぎている」と
判定したら採用しない。

- 150文字未満の短い実況では先頭8文字が同じ
- 4文字shingleのJaccard類似度が0.35以上
- 4文字shingleのcontainmentが0.65以上
- 最長共通部分文字列が `max(12, 短い方の12%)` 文字以上

長い実況では章・年月・将軍名などの事実句が共通するため、先頭8文字だけで
即棄却せず、全体のshingle重複と長い共通部分列で判定する。

3候補のうち、直近24件のどれともこの基準を超えないものだけを採用し、
複数あれば最も類似度が低いものを選ぶ。全滅ならその回は無音（`duplicate`）にし、
次の通常間隔で新しい状況から作り直す（`max_attempts=1` の長文設定）。
したがって「読み上げた定期実況どうしが基準以上に似ている」状態は作らない。
文章の意味的な同一性を数学的に保証するものではなく、文字列レベルの
反復を決定的に防ぐ契約である。

## 事実検証

LLM出力はJSON `{"texts":["...","...","..."]}` に限定し、各候補を次で検証する。

- 将軍名・城名・札名などのカタカナ語は、材料の名前に一致するか、
  ごく限られた一般語（チャート、ボス等）だけを許可する。
- 3文以上あり、評価・反省・感情の語（見事、課題、成長、面白、拍手など）と、
  「一方」「だから」「その結果」「背景」などの因果・対比の接続を1つ以上含む。
- focusの具体名（将軍・城・札）が最初の2文にあり、固有名詞が6個以下である。
  箇条書き・番号付き列挙は拒否する。事実だけを並べた文は `invalid_output` と
  して採用しない。
- 「〜城」の直前の名前、`将軍` の直前の名前も材料に一致させる。
- 数字はまず項目別のcontextで照合し、通らない場合もsnapshot内の整数値・
  list件数と一致すれば受理する。章クリアだけは `cleared` と厳密に照合する。
  材料にない数字は拒否する。
- 勝敗は `battle_result` の対象・勝者・敗者と一致する場合だけ許可する。
  window集計の「3勝1敗」はwindowの数値と照合し、長文の省略表現は集計一致を
  条件に許可する。
- 城の占領・奪還・喪失は `castle_*_observed` と一致する場合だけ許可する。
  「占領も喪失もありません」はゼロ主張として扱う。未来の「奪還へ」は
  目標表現として所有claimから外す。
- 死亡・戦死・全滅・撃破・ゲームオーバーの断定は拒否する。
- HPは将軍名と「HP」または「体力」を伴う場合だけ許可する。

この検証は自然文の真偽を完全に証明するものではない。材料の範囲と短い出力に
限定して、誤った断定・名前・数値の混入を抑えるための契約である。

## 配送

`hanjuku_narration.deliver_periodic` が既存の `_deliver` と同じfence
（game/runtime/generation/lease/期限、終了・世代不一致の拒否）を通し、
Sorenの既存 `enqueue_audio_text` に `context=hanjuku_commentary` で渡す。
チャット投稿、Sorenコメント、配信の再起動は行わない。読み上げ開始済みの音声を
途中で切らない。

## runtime内のファイル

| ファイル | 所有者 | 内容 |
| --- | --- | --- |
| `hanjuku_periodic.json` | corner | 次回時刻、seq、pending、直近本文、失敗回数 |
| `hanjuku_periodic_request.json` | corner | 1回分のidentity・期限・状況・直近本文 |
| `hanjuku_periodic_result.json` | worker | status、text（enqueued時のみ）、試行数 |
| `hanjuku_periodic.lock` | worker | 同時起動を1つに限定するflock |
| `hanjuku_periodic_worker.err` | worker | 起動直後の失敗tracebackだけ |
| `hanjuku_periodic_commentary.jsonl` | worker | requested / generated / applied / delivered の固定event |

診断では、同じruntime identityの `hanjuku_periodic.json` の
`last_attempt_at`・`pending_seq`・`last_result_seq`・`next_at`・
`recent_texts` の件数と、`hanjuku_periodic_result.json` のstatusを見る。
本文・prompt・provider出力は診断へ出さない。

## 生成経路

`scripts/hanjuku_scene_generate.sh` を既存のまま使う。Sorenの
`eloop_lib.sh`、`ai_generate_list 'RADIO:hanjuku-commentary'`、
`BATCH_COMMENTARY_AGENTS` → `RADIO_AGENTS` → `AI_COMMON_AGENTS` の
実効設定を再利用し、モデル・provider・queue・backoffを新設しない。
生成は `OwnedSceneProcess` のsubreaper配下で設定上限（既定90秒、長文設定は210秒）に制限し、timeout後は
今回の子孫だけを終了する。入力processは待たない。

## 配備後の確認

1. corner再起動後、`[hanjuku.periodic_commentary] enabled=true` を読み込む。
2. 15分後に `hanjuku_periodic_request.json` のseqと
   `hanjuku_periodic_commentary.jsonl` の `requested` を確認する。
3. workerの `generate_started`、`generate_succeeded`、`deliver_enqueued` と
   `hanjuku_periodic_result.json` のstatusを順に確認する。
4. `hanjuku_narration.jsonl` の `context=hanjuku_commentary` と
   Soren側の `narration_playback` started/completedを照合する。
5. 連続2回の本文を比較し、Jaccard・containment・LCSが基準未満であることを
   確認する（重複時は `duplicate` で無音になっていること）。
6. 各回が事実の列挙ではなく、進展・評価・課題・ユーモア・締めを含む
   一つの流れになっていることを聴取で確認する。

`deliver_enqueued` は共有queueへの投入成功であり、実際に聞こえたことや
Soren側の再生完了を意味しない。本文の事実性・聞こえ方は実測で別に確認する。

## 制約

- LLMのprovider chainが使えない環境では無音。固定文へのfallbackはない。
- 15分ごとに必ず話す保証はない。重複・材料不足・queue混雑で間隔が空く。
- 直近24件との文字列類似だけを保証し、意味的な新規性は保証しない。
- 実装はcornerプロセスの再起動で読み込まれる。生成workerは毎回新しいprocess。
- 本文は音声のみで、配信のチャット欄やSorenコメントには出ない。
