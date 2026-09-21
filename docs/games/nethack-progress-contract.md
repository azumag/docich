# NetHack 本番の行動・待機契約 (#490)

この変更は `ec3fce3`（#758）を基点とする実装で、本番適用・実戦での停止解消は未検証です。
実装中に `761c3b9`（#760）の submodule 参照同期を取り込みました。NetHack 本体の並行差分はありませんでした。
添付された停止例（HP 4/16、player=(40,14)、隣接 `:` と `f`、南東だけ `#`）を再構成した
回帰テストでは、brain が南東 `n` を1個返します。添付には実フレーム全文がないため、これは局面の再構成です。

## 安全性と進行性の範囲

ターン制なので「判断保留＝無入力」を続けても局面は変わりません。通常の完全な gameplay frame で
行動を選べない場合は、NetHack の明示コマンド `.` を1回送り、1ターン進めて再観測します。
質問・未知画面・player不明など、`.` が質問への回答になり得る局面だけは fail-closed で無入力にします。
全局面の無停止・生存・昇天は保証しません。

ここで「安全」は入力の種類と観測条件の契約です。可視床にも未知の罠があり、通常戦闘でも死亡します。
旧 P3b の「creature には一切踏み込まない」を、**可視退避先がない場合の通常方向キー1個**に限って緩めます。
強制攻撃 `F`、攻撃承認 `y`、複数キーやマクロ、推測したアイテム・祈り・扉・階段操作は追加しません。

## 優先順位

| 観測・状態 | 本番で許可する次の入力 |
|---|---|
| 最上段に明示 `--More--`、ほかの質問・折り返し疑いを検出しない | Space 1個。同じraw frameには再送せず `progress_blocked` |
| 最上段に完全な未回答 `Really save? [yn] (n)` / `Really attack …? [yn] (n)`（既定表示は省略可） | `n` 1個。全く同じ観測には再送しない。save拒否は下記のcanonical所有権確認が必須 |
| その他の質問、方向・選択・命名・メニュー、曖昧/回答済み/切れた確認 | 無入力 |
| `Sick/FoodPois/Ill/Slime/Strngl/Stone/TermIll`、`Weak/Fainting/Fainted/Starved` | 完全な gameplay frame なら `.` 1ターン。低HPが同時でも無入力で凍結しない |
| player・HP・HP最大値・階層を確認できない、死亡HP | 無入力 |
| 隣接creatureなし、低HP/移動障害の判断、空腹で移動候補なし | `.` 1ターン |
| 隣接creatureあり、移動障害なし | 可視安全マスへの縦横1歩 → 斜め1歩 → creature への通常接触1回 → 全候補拒否後は `.` |
| `Blind/Conf/Stun/Hallu` と隣接creature | 退避移動はしない。完全な gameplay frame で他の行動がなければ `.` |
| `Hungry`（critical HPとの同時発生も含む） | 可視探索/退避 → 通常接触 → 全候補拒否後は `.` |
| 通常の探索 | 可視 `.` / `#` / `<` / `>` への縦横優先の1歩。縦横候補なしなら斜め |
| 安全マスなしの探索判断 | `.` 1ターン |

`n` は確認では拒否、地図上では南東です。`assert_production_safe` は intent・キー形だけでなく、
現在の prompt、状態、player、移動先glyphを再検証します。未知プロンプトを `none` と扱わないよう、
未分類の質問・選択肢・メニュー終端も `unknown` とします。文字による推定なので、未対応の表示形式は残ります。

### TTYのメッセージ領域と折り返し

ゲームプレイ用parserの質問・More判定は最上段だけを対象にします。地図・statusの文字列は検索せず、
防具 `[` とcreature `y` / `n` が並ぶ地図をyes/noとして扱いません。地図の座標は従来どおり維持します。
最上段が右端1列以内まで達する場合、または幅を超える結合captureの場合は、
ハード折り返し・欠落の疑いとして `unknown` にします。次行は地図の可能性があるため、
文字列だけでなく単語の幅も折り返し判定に使いません。80列設定で60・73・78文字の通常メッセージに
壁・通路・monster・`[yn`列が続いても `none` と既存の進行アクションを維持します。
折り返したsave/attackを連結して応答可能な確認へ昇格させません。完全な短い最上段の確認だけが従来の拒否候補です。

plain TTYにはメッセージウィンドウ境界・cursor情報がなく、右端より手前で折り返された任意の質問を
通常メッセージ＋地図と完全には区別できません。最上段の `?` / 選択肢 / 末尾 `:` 等、または
`Really`、`Would/Could/Should/Do/Did/Can/Will/Are you` で始まる質問の断片を根拠に入力を止めます。
これらは質問としての安全弁であり、通常 gameplay の判断保留を意味しません。
これらの手掛かりも幅超過もない短い最上段は `none` になり得ます。`none` はprompt不在の証明ではなく、
**そのような未知の折り返し形式での自動入力安全性は保証対象外**です。対応にはmessage/cursor等の追加観測が必要です。
79列以上の通常メッセージを保守的に止める可能性も残ります。別port・手動改行・独自レイアウトの完全解析や、
実TTYでの無停止は保証しません。startup専用gateとcoordinatorのsave処理は今回変更していません。

## 送信直前の再検証とsave境界

NetHack brainに限り、agent loopが `adapter.act` の直前に再観測します。fenceがある場合は、その確認・再観測・
再検証・送信・送信完了通知を同じ `shared_section` 内で行います。sidecar/LLMはこの区間では呼びません。
計画と新しい正規化観測が完全一致することを要求し、prompt/player/map/depth/HP/conditions/targetのほか、
messageやturn、raw frameが変わった場合も古い入力を破棄します。Observationの取得時刻 `ts` は比較対象外です。
起動ゲートの入力にも同じ画面一致を要求し、送信しなかった起動応答は再試行可能に戻します。

`decline_save` は同じ共有ロック内でcanonicalを読み、`phase=ready`、active gameがNetHack、
`operation/request_id` が空の場合だけ送ります。`draining`、境界要求処理中、状態不明、
`state_dir`なしではfail-closedで `n` を送りません。state_dirあり・fenceなしの明示CLI実行もこのロックを使います。
state_dirなしのunit/単体利用では他の観測検証は有効ですが、save拒否だけは許可されません。
coordinator adapterが未回答save promptを処理してcancelを確認する既存契約は変更しません。

このロックはcoordinatorとの競合を防ぐもので、ゲーム自身や外部入力を停止する仕組みではありません。
fresh captureから入力到達までの短い区間のTTY変化を原子的に排除する保証はありません。

## 通常接触のリスク

`f` は猫科、`{` は噴水です。色は敵味方の証明にはなりません。
対象は標準の ASCII creature glyph（英字、`& ; : ' @`）で、不可視マーカー `I` は除外します。
複数 `@` は従来どおり player が曖昧なので動きません。

通常方向入力を受けた NetHack が、ペット移動・敵との近接戦闘・peaceful 確認を処理します。
確認は拒否し、強制攻撃しません。ただし、**`confirm` / `safe_pet` が無効、Stormbringer 等の特殊装備、
受動攻撃・石化などには完全な保護を提供できません**。地図だけで装備や生物種の安全性を確定できません。
親担当はこの接触面を独立レビューし、実環境の標準glyph・vi方向キー（number_pad無効）・確認/ペット保護設定を
配備前に確認してください。今回、設定を書き換えたり未知の環境で保護済みと推定したりはしていません。
**number_pad/vi設定・確認設定・glyph設定の実効値はコードでは取得も検証もしていません。**
キーの意味が想定と異なる環境で安全に動くとは主張しません。

根拠は NetHack 3.6.7 の
[通常攻撃・ペット保護](https://github.com/NetHack/NetHack/blob/NetHack-3.6.7_Released/src/uhitm.c) と
[移動制限](https://github.com/NetHack/NetHack/blob/NetHack-3.6.7_Released/src/hack.c) です。
本番の装備/実効設定/入力結果はこのコード調査からは確定できません。

## キー送信の空回り防止

- 斜めの到達先が床でも、入口の扉・狭い隙間・変身形態等により移動できないことがあります。
  通常移動を2回試して、同じ地図・位置・階層かつ `T` が進まなければ、その方向を候補から外します。
  最初の1回は観測/送信の遅延を許容します。`T` がないときは完全同一フレームを根拠にし、地形移動だけでなく
  通常接触も同方向2回までに制限します。
- 通常接触後の攻撃確認は即拒否し、原因となった方向を除外します。別方向の退避/接触があれば次に試します。
  全候補を拒否されたら、完全な gameplay frame では `.` を送り、同じ相手へ接触→拒否を永久に繰り返しません。
- 除外は現在の地図・位置・階層に限定し、これらが変われば解除します。メッセージや `T` の変化だけでは解除しません。
  別brain/runtimeには引き継ぎません。地図が変わると同じpeacefulへ再度接触する可能性はあります。
- 戦闘は位置不変でも `T` が進めば継続可能です。`T` なし・完全同一フレームで候補を有限回試した後も、
  gameplay frame が有効なら `.` を送り、次の観測で変化を確認します。prompt/未知/不完全観測だけが
  `progress_blocked` の無入力対象です。

pending/応答回数はfresh検証後に `adapter.act` が正常終了した入力だけから更新します。
fresh検証の拒否・capture失敗・送信例外・fence lossでは未送信の候補をpending/blockedにしません。
送信例外が実際の部分送信後に起きた場合は判定不能で、成功とは記録しません。正常終了もゲームの受理やターン進行の
証明ではなく、次の観測で確認します。観測遅延による保守的な停止はあり得ます。

## 観測と検証

`brain.last_decision` は従来のpolicy判断、`last_progress_decision` は実行候補の判断です。
intent変更時の `[nethack-progress] planned=...` は計画の記録であり、送信成功やターン進行の証明ではありません。
advisory/narrator/observation shadow は元の判断を受け取り、candidate shadow には解決後の候補Actionsを渡します。
LLMが本番Actionを差し替える経路は追加しません。

`tests/test_nethack_progress.py` を CI の Retro corner contract 明示リストに追加しています。
さらに `test_nethack_canary_tactics.py`、`test_agent_loop.py`、`test_agent_fence.py`、
`test_agent_action_lock.py` も同リストで実行します。`rg --files`で実在する関連lockテストを確認しました。
報告局面・斜め4方向・隣接8方向・混合状態・攻撃拒否連鎖・移動拒否・通常戦闘のターン更新・未知プロンプト・
送信前文脈ガードを検証します。既存 NetHack/operator スイートと、修正を無効化した変異検証も実行します。

本番の長時間プレイ、死亡/xlogfile、終了検知、セーブ再開、実ペット/peaceful/敵、特殊武器、狭い通路の実挙動は
親担当の検証範囲です。自動改善のLLM・課金・実行契機・昇格配線は今回変更しません。
canary は既存の別方策を維持し、production resolver は移植しません。共有parser/explorer/base policyの変更は
canaryにも届くため、既存スイート成功だけで production の進行性や候補評価の同等性を主張しません。
攻撃promptの `decline_attack` はproduction resolverだけが生成し、共有base policyと空catalogのcanaryは従来の
`prompt_decision`（無入力）を維持します。既定canary catalogの `confirm_attack` は従来どおり `y` です。
`unknown` と `Stone/TermIll` はshadow・candidate replayのschemaへ同時に追加し、round-trip/comparisonを検証します。

### 前回のローカル検証結果（c0f1616）

- 指定 Python 3.14 venv、`PYTHONPATH=src`、NetHack/VM actions の `test_nethack_*.py`: 700 passed / 216 subtests passed。
- 同スイートに `test_agent_loop.py` / `test_agent_fence.py` を加えた検証: 727 passed / 220 subtests passed。
- `python3 -m unittest discover -s ops/vm_actions/tests -p 'test_nethack_corner_operator.py'`: 27件 OK。
- `git diff --check`: 成功。
- 別Pythonプロセス内の変異17件を全て検出。斜め・f分類・接触・未知prompt・重篤状態・移動障害・拒否記憶・
  文脈ガード・fresh再検証・送信前ack・Stone/TermIll欠落・More連打・Tなし連打・Hungry欠落・save所有権・
  shadow unknown・canary攻撃prompt混入を壊すと関連テストが失敗することを確認。ファイル自体は変異していない。

### 前回のローカル検証結果（d3997c8、次行幅判定は後続修正で撤去）

- 指定venv、NetHack/VM actions の `test_nethack_*.py`: 741 passed / 216 subtests passed。
- 上記に `test_agent_loop.py` / `test_agent_fence.py` / `test_agent_action_lock.py` を追加: 770 passed / 220 subtests passed。
- operator unittest: 27件 OK。`git diff --check`: 成功。
- 80×24再構成フレームで通常More/save/attack/direction/selection/text、地図内 `[yn`、
  80桁文字/単語折り返し、結合capture、短い分割save、確認後の続行文字、実送信ゼロを検証。
- プロセス内変異3件を検出: 折り返しガード無効化（12 failures）、単語折り返し検出無効化（4 failures）、
  全画面prompt検索へ逆戻り（19 failures）。sourceファイル自体は変異していない。
- CI明示リストの追加4件と既存の観測/進行テストを確認し、同step内の全参照ファイルの実在も確認。
  補助確認でPyYAML未導入だったため標準ライブラリで確認。スイートに環境要因の失敗はなし。

### 次行地図幅による誤保留の修正後

- 指定venvの必須NetHack/VM actionsスイート: 787 passed / 216 subtests passed。
- agent loop/fence/action-lock込み（canaryも含む）: 816 passed / 220 subtests passed。
- operator unittest: 27件 OK。`git diff --check`: 成功。
- 60/73/78文字＋次行の壁/通路/monster/[yn列で `none` と既存の南東退避を確認。
  地図内[yn除外、既存の折り返し確認拒否、質問の断片だけでの保留、未知の語頭のハード折り返しを検証。
- メモリ内の変異3件を検出: 次行幅ヒューリスティック復活（24 failures）、最上段幅ガード無効化（3 failures）、
  質問断片ガード無効化（20 failures）。テスト収集エラーなし、ソースファイルの変異なし。
- 変更したテストは既存のCI明示リストに含まれることを確認。今回CIリスト自体の追加変更はなし。

リモートCI・実ゲーム・本番・この追加修正へのSol再レビューは未実施です。
