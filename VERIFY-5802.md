# VERIFY-5802

対象: azumag/docich#5802「[半熟英雄] 切り札を残して敗北: ch4 ピスタチオ」

検証範囲は issue 本文と、この worktree にある現行コードのみ。本番 decision log は参照していない。git 書き込み操作・`gh` は実行していない。

## 結論

**(a) 実欠陥**

検出自体は誤検知ではない。ピスタチオは実際に `cards_selected=[]` / `cards_used=[]` のまま敗北しており、少なくとも `ノリウツール` は使用を試みるべき実資源だった。敗北経路は、単発必殺を要求する damage gate の拒否、拒否済み札を「救済手段なし」へ寄せる状態遷移、および退却メニューを開く処理が未完了の切り札 flow と競合する処理の組み合わせで説明できる。

ただし本件は #5776 とまったく無関係な別経路ではなく、#5776/#5777 系の「負けている戦闘で damage gate が札を拒否し、退却 open が切り札 flow を遮る」問題の通常将軍戦バリアントと判断する。v1595 の残件修正で主経路はかなり塞がれているが、現行コードにも以下の残余がある。

- damage-gate 拒否の撤回は無条件廃止ではなく、低 HP 帯かつ限られた role に限定されている。
- `_retreat_waits_for_card_path()` は `HP <= 4` の即敗北帯では例外として待たない。退却そのものは一律禁止なのに、この帯では退却 open が未選択の切り札 flow を中断しうる。
- 本件で 01:07:33 時点の HP が 4 以下だったかは issue 本文だけでは確定できないため、現行 main で同一時刻の挙動が完全に修正済みとは断定できない。

## fingerprint / occurrence の再計算

### 実行可否

ハッシュ値の再計算は**未実行**である。`python3` / `sha256sum` を使うローカル実行が承認されなかったため、ここでは計算式と入力値だけを記録する。検品ゲート側で再計算されたい。以下の値を自分で計算したとは扱わないこと。

### fingerprint

`src/docich/hanjuku/month_audit.py` の `_fingerprint()` は、

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

である。battle resource incident の呼び出しでは signals は `[event['kind']]` だけなので、本件の入力は次のとおり。

```text
4|battle_loss_with_resources|battle_loss_with_resources
```

issue 記載の期待値:

```text
1c8a1b742bd6e237
```

### occurrence / incident_id

`src/docich/hanjuku/month_audit.py` の battle resource incident builder は、

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

である。issue 本文の値を代入すると、入力は次のとおり。

```text
g1373-b4c4126f|1373|battle_loss_with_resources|card|ピスタチオ|I:604494:23e61e11|1795828069
```

ここで `1795828069` は issue 本文 JSON の `created_at: 1795828069.8532088` を `int(float(now))` した値である。`resources` は `['card']` なので join 結果は `card`。

issue 記載の期待値:

```text
2cf810b117d13da7ed21
```

## 経路の確認

### 1. 残り札の検出は実態を表している

`cards_selected=[]` / `cards_used=[]` でも `cards_remaining=['バグストーム','ノリウツール']` が出るのは、監査側が携行・出撃記録・計画札から未使用の残り札を再構成するためである。実戦闘で一度も選択が確定しなくても残存資源として検出される仕組みであり、この点は誤検知ではない。

### 2. 計画されていた札はノリウツールと強く推定できる

issue の `battle_card` 理由は「出撃時に携行した強い切り札を強い将軍へ開幕で使う」であり、これは `_strong_card_tactics()` が作る文言と一致する。現行 `STRONG_CARDS` は `クースカン / ミックミー / マグネガキン / ノリウツール` であり、残り札2枚のうち該当するのは `ノリウツール` だけである。`バグストーム` はこの strong-card 開幕経路の対象ではない。

したがって、2回の `battle_card` はいずれも `ノリウツール` の再計画だった可能性が高い。厳密な card 名は decision row の表示だけでは確定しないが、コード上の文言と残り札からの推定としてはこれ以外に自然な候補がない。

### 3. damage gate の拒否で実使用へ進まなかった

`battle_card_damage_rejected` は「単発で倒し切れる保証がない」「敵召喚リスクがある」場合に、実一覧の代替候補を確認させる damage gate の記録である。

`ノリウツール` は「使用者の現在HPの半分ダメージ。めをまわしてる！の追加効果」であり、将軍への固定大ダメージ札ではない。敵 HP が最終的に 37 残っていることからも、ピスタチオの HP が下がった状況では単発必殺の保証を出しにくい。したがって拒否自体は当時の gate ルールに沿って発生したと考えられる。

問題は、退却が一律禁止であり白兵劣勢のまま敗北する状況で、「倒し切れない札」を使わず温存することに実益がないことである。owner は #5777 で「ダメージ判定が札を拒否する挙動を廃止する」方針を出し、#5776 残件修正でも低 HP 帯の拒否撤回を広げた。したがって、本件は単なる観測ミスではなく、当時の実装方針が生んだ実欠陥である。

### 4. `battle_survival_unavailable` は「物理的に札がない」意味ではない

`ノリウツール` は `SURVIVAL_CARDS` に含まれる。一方 `バグストーム` は自爆・犠牲札系であり、通常将軍戦の survival card としては扱われない。したがって、救済手段として問題にすべきなのは主に `ノリウツール` である。

コード上、damage gate で拒否された札は `_card_gate_close()` で `card_damage_list_blocked` に記録され、survival state では `cards_exhausted=True` になる。つまり `battle_survival_unavailable` は「カードが一枚も残っていない」ことを直接意味しない。本件では「残っているが damage gate で unsafe とみなされ、救済候補から外された」状態を意味すると読むのが正しい。

このため、issue 本文だけの「読み取れる未試行の救済手段がない」という文は字義どおりには受け取れない。実際には少なくとも `ノリウツール` が残っており、一覧でも読めた可能性が高いが、ゲート結果によって救済手段として消費されていた。

### 5. `battle_card` → `battle_card_damage_recheck` の連鎖

issue の時系列は次のように読める。

1. 開幕 strong-card 計画で `ノリウツール` を使おうとした。
2. 実一覧で damage gate が単発必殺を保証できず拒否した。
3. 拒否後に survival menu を開いたが、拒否済み札は exhausted/blocked 扱いとなり、卵も 0 で、利用可能な救済がないと判断された。
4. HP・兵士・読み取り状態の変化に伴い `_card_damage_recheck_resources()` が再評価し、`battle_card_damage_recheck` が記録された。
5. 再評価後に strong-card tactic が残っているため `battle_card` が再計画された。
6. しかし使用選択へ進む前に、退却 menu open 系の処理が走り、最終的に敗北した。

この連鎖は、当時 v1593 に存在した「拒否 → 一覧 close / survival exhausted → recheck → 再計画 → 別メニュー競合」の構造を示す。

### 6. 退却一律禁止との矛盾

現行コードでも `battle_retreat_banned` の記録どおり、退却は実際には選択しない・できない方針である。v1414 以降の owner 方針として、退却は停滞の原因になるため一律禁止であり、最後まで戦う。

一方で `_hero_retreat_open()` 系の処理は残っており、退却メニューを開く動作自体は切り札 flow と競合しうる。v1595 で `_retreat_waits_for_card_path()` が追加され、未選択の card flow や有界な再開待ちがある間は退却 open を待つようになった。しかし同関数は `_retreat_immediate_loss()`、すなわち `RETREAT_RESOURCE_FLOOR_HP = 4` の即敗北帯を例外にしている。

退却が実際には禁止なのに、HP 4 以下では退却 open が切り札 flow を中断しうるのは論理矛盾である。本件の 01:07:33 時点の HP が 4 以下だったかは issue 本文から確定できないが、敗北 16 秒前なので除外できない。少なくとも現行コードには同種の残余経路が残っている。

### 7. `battle_command_open_requested` の意味

`battle_command_open_requested` は救済/退却系メニューを開く B を計画・送信した記録であり、メニューが実際に開いたことや、その後の行選択が成功したことを証明しない。issue 本文の「表示成功は未確認」は正しい。実使用記録がない以上、この B 送信をもって切り札使用の成功とは扱えない。

## #5776 / v1595 との関係

#5776 はボス戦で `battle_card_damage_rejected` から退却による一覧 close へ進み、強い札を使わず敗北した事案である。本件は通常将軍 `ブカティーニ` 戦であり、残り札の内訳も異なるため同一事案ではない。しかし故障構造は同じ系列である。

v1595 の残件修正は、主に次の点で本件の主経路をカバーする。

- 低 HP 帯で `nonlethal_egg_risk` などの拒否 role を撤回する。
- survival list の候補列挙時にも撤回を適用し、拒否済みのまま閉じないようにする。
- 未選択の card flow がある間は、有界な範囲で退却 open を遅らせる。
- damage-gate recheck で低 HP 撤回により選択可能になった札を扱う。

したがって、HP が即敗北帯より上で、拒否 role が撤回対象に分類される場合には、現行 main では本件と同じ最終局面へ進みにくい。一方で以下は未解決である。

- issue 本文だけでは初回拒否時・退却 open 時の HP が分からず、撤回条件を満たしたか確定できない。
- `HP <= 4` の即敗北帯では `_retreat_waits_for_card_path()` が待たない。
- owner 方針としては退却は完全廃止だが、コードには退却 open の競合経路が残っている。

よって、#5776 残件修正で本件の**主経路は部分的にカバーされるが、完全に同一として close できるほどではない**。通常将軍戦かつ即敗北帯の残余として扱うのが適切である。

## 未決の点

1. fingerprint / occurrence の SHA-256 実計算: 実行承認が得られず未計算。上記の raw string と期待値を検品ゲートで再計算する必要がある。
2. 01:07:33 時点の HP、初回拒否時の HP、実一覧の全候補、拒否 role: issue 本文だけでは確定できない。本番 decision log がないため不明のまま。
3. `battle_card` の card 名は表示行だけでは確定しない。ただし理由文と `STRONG_CARDS` から `ノリウツール` と強く推定できる。
4. `バグストーム` は自爆・犠牲札系であり、通常将軍戦で survival card として使うべきだったとは現行コードからは言えない。本件の実欠陥は主に `ノリウツール` を使えなかった点にある。
5. この worktree は `origin/main` より v1603 だけ遅れているが、v1603 は第4話の経由城 targeting を変える差分であり、battle/card/audit ロジックに影響しないことを確認した。

## 検品ゲート確認（Muse、2026-11-28 17:15 JST、現行 main 5ead25262）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("4|battle_loss_with_resources|battle_loss_with_resources")[:16] = 1c8a1b742bd6e237、SHA-256("g1373-b4c4126f|1373|battle_loss_with_resources|card|ピスタチオ|I:604494:23e61e11|1795828069")[:20] = 2cf810b117d13da7ed21。いずれも issue の値と一致する。
- 結論 (a) 実欠陥を維持する。ただし本件の残存欠陥は、検証記録の主経路のうち退却 open による selected-flow の supersede ではなく、damage-gate 拒否の残存である。PR #5824（v1605、2026-11-28 17:01 マージ）は選択済み flow の完走を守る修正であり、#5802 の cards_selected=[] / 拒否済みという経路とは別脚である。
- 現行 main での確認：`_card_refusal_retired` は role が {nonlethal_egg_risk, control_has_no_observed_followup, damage_or_egg_risk_unclassified} の3種かつ `ally_hp * 3 <= start_hp`（開始 HP の1/3以下）の場合にだけ拒否を撤回する。`critical_card_spend` による撤回（judgment/battle.py）は `(summoned or boss)` の場合に限られ、本件の通常将軍戦（ブカティーニ）には適用されない。owner の 2026-11-28 の方針コメント（「ダメージ判定による札の拒否を廃止する」）に対し、実装はこの範囲に留まっており、通常将軍戦で敗北に向かう局面の survival card（ノリウツール）の拒否は現行 main でも残りうる。
- `HP <= 4` の即敗北帯で `_retreat_waits_for_card_path()` が待たない点は、g460 再現回避のための意図的な例外としてコードに明記されており、本結論の根拠には含めない。
- 未決の点として残るのは、初回拒否時・退却 open 時の正確な HP と拒否 role（issue 本文だけでは確定できない）である。(a) の根拠は個別時刻の値ではなく、通常将軍戦における damage-gate 拒否の残存というコード上の構造と、owner の廃止方針である。
- 修正は別途、修正 PR とセットで割り当てます。修正の対象は damage-gate 拒否の撤回範囲（通常将軍戦・敗北局面の survival card を含む）とし、#5824 で既に修正済みの selected-flow supersede の再実装は含まないこと。
