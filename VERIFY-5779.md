# VERIFY-5779 — [半熟英雄] 卵を残して敗北: ch9 マフィン

## 結論

**(c) 誤検知**。ただし検出器の不足に起因する誤検知であり、現行 main v1593 でも同じ構造が残っている。

この戦闘では「卵を一度も使わずに敗北した」経路ではなく、敵の卵変身に対して味方の召喚獣戦へ入り、召喚獣の技選択を複数回行い、最後に「たまごに もどれ」を選んで通常戦闘へ戻っている。したがって battle-time の `egg_uses: 1` は使用後の実残数を示す実測ではなく、少なくとも本 issue の証拠だけでは「敗北時に使える卵が 1 残っていた」とは確定できない。現行コードは卵戦へ入った履歴を通常パネル復帰時に消し、さらに使用前に見えた白文字の卵行を戦闘終了時の未使用証拠として扱うため、この use→return→loss 経路を「卵を残して敗北」として high confidence 起票しうる。

## ハッシュ再計算

`python3` の実行が承認却下されたため、SHA-256 の再計算自体は **未実施**（検品ゲートでの再計算が必要）。現行 main `src/docich/hanjuku/month_audit.py` の該当式と入力値は以下。

### fingerprint

`_build_battle_resource_incident()` は `record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])` とし、`_fingerprint()` は:

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

本件の入力:

```text
chapter = 9
kind = battle_loss_with_resources
signals = ["battle_loss_with_resources"]
raw = "9|battle_loss_with_resources|battle_loss_with_resources"
expected = "f601c5186ce59c70"
```

### incident_id / occurrence

同じ builder は:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

本件の入力（`created_at` を builder の `now` とみなした場合）:

```text
runtime_id = g1190-ch1v9
generation = 1190
kind = battle_loss_with_resources
resources = ["egg"]  -> join 結果 "egg"
general = マフィン
step = I:c9:ハヤク>タオ:3
int(now) = 1795808993
raw = "g1190-ch1v9|1190|battle_loss_with_resources|egg|マフィン|I:c9:ハヤク>タオ:3|1795808993"
expected = "b3913d3ad03024e59363"
```

注意: `created_at = 1795808993.854283` なので `int(float(now))` は `1795808993`。builder 実行時の `now` が issue JSON の `created_at` と同一であることは、builder が `created_at: float(now)` を同じレコードへ書くことから確認できる。

## 現行 main の検出ロジック

`src/docich/hanjuku/month_audit.py`:

- `_battle_resources()` は `observed_metric['egg_uses']` を読む。`egg_uses_source == 'ledger'` の場合、`egg_available_unused is True` なら measured、そうでなく positive ledger かつ `egg_battle is not True` なら `egg_ledger_only` として、いずれも `egg = 1` にする。
- `_battle_resource_event()` は `decision == 'battle_result'` かつ `outcome == 'loss'` で resources があれば `battle_loss_with_resources` を返す。
- 本件の issue JSON は `resources: ["egg"]`、`egg_uses: 1`、`egg_ledger_only: false`。したがって現行式では ledger-only ではなく、`egg_available_unused` 側の measured 証拠として扱われた形になる。

一方、戦闘終了レコードを作る `src/docich/hanjuku/flows/battle.py` では:

- `battle_result` の `observed_metric` に `egg_uses`（ledger）、`egg_uses_source: 'ledger'`、`egg_available_unused: _egg_available_unused(mem, cur)`、`egg_battle: bool(cur.get('egg_battle') or mem.get('egg_battle'))` を書く。
- `_egg_available_unused()` は `cur.get('egg_battle') or mem.get('egg_battle') or cur.get('egg_used')` なら False を返すが、そうでなければ `own_egg_positive_observed` または `egg_white_seen`（または直近 `battle_egg_white_available`）を未使用卵の証拠とする。
- しかし通常の将軍パネルへ戻ったときの `_card_battle_reading()` は `cur['egg_battle'] = False` と `mem['egg_battle'] = False` に戻す。ここで「この戦闘で卵戦へ入った」という履歴は消え、最終的には「現在卵戦中ではない」だけが残る。
- `egg_white_seen` は `battle_menus.py` で白文字の「たまごをつかう」を見た時点で立つが、それが卵使用 **前** の表示だったか、使用後に再度利用可能な表示だったかは区別されない。

本 issue の直前決定はこの構造と一致する:

1. `battle_enemy_transform_observed`: 敵が卵を使用して変身。
2. 複数回の `monster_menu_choice`: 味方の召喚獣の技メニューで「じゃがいも」「ずつき」を選択。
3. `monster_skill_stall`: 両技とも敵 HP を動かせない。
4. `monster_menu_choice` で「たまごに もどれ」を選択。
5. 通常戦闘へ戻って `battle_command_open_requested` / `battle_command_open_wait`。
6. 数秒後に ally HP 0 で `battle_result` loss。

カード選択・使用がこの戦闘の battle_event では空なので、召喚獣戦は切り札召喚ではなく味方卵への応答だったと読むのが自然である。少なくとも「卵戦に入らなかった」戦闘ではない。

## 退却選択 False と owner 方針

退却選択 False は本件の欠陥証拠ではない。現行 main では退却は設計上禁止されている。

- `battle_menus._hero_retreat_menu()` は owner 2026-11-23 の方針として「退却は停滞の原因になるため一律禁止し、切り札・卵を使い切った後もそのまま戦い切る」と記録し、退却を選ばない。
- さらに `flows/battle._hero_retreat_open()` は chapter 9 の非主人公将軍について、退却確認メニューを開かない経路を持つ。
- issue の決定内訳にも `battle_retreat_banned: 2` があり、実際にこの禁止経路が記録されている。

したがって「退却を選ばなかった」こと自体は設計どおり。owner の「卵・切り札を使い切ってから退却する」方針は、現行ではむしろ「退却せず、使える資源は先に使って戦い切る」方向へ強められている。

## 卵・切り札の使用機会

### 卵

卵は無使用ではなかった可能性が高い。issue の決定列は味方召喚獣の技メニューを示しており、少なくとも卵戦/召喚戦へ入っている。`egg_uses` は複数回使える卵の残回数であり、「残 1」は使用前の ledger 残数または古い観測値でありうる。現行コードは選択だけでは残数を減算しない（`_own_egg_empty()` のコメント: selection alone is not a use receipt; `_egg_recheck` も消費証明ではなく再確認フラグ）ため、使用後に実残数がいくつだったかは本 issue から確定できない。

通常パネル復帰後は `battle_command_open_requested` で救済メニューを開こうとし、その後 `battle_command_open_wait` で表示を待っている。つまり残資源を使う方向の動作は始まっているが、メニュー表示確認前に ally HP 0 になった。ここだけを見ると入力遅延のように見えるが、直前まで召喚獣戦だったため「もっと早く同じ卵行を使えた」とは言えない。

召喚獣を戻した判断自体は、現行 main では設計・テスト済みの経路である。`battle_menus._monster_stall_switch()` は両技が敵 HP を動かせない場合、`return_risk` が無ければ `MONSTER_STALL_RETURN_TURNS` で召喚獣を戻す。負傷将軍の盾になる場合は戻さないテストもあるが、本 issue では bound な将軍 HP reading がなかった可能性があり、ログだけでは return_risk の評価値を確定できない。戻した直後に敗北した点は戦術的に危険だが、これは audit の「卵未使用」判定を直接立証するものではない。

### 切り札

本件の battle_event は `cards_selected: []` / `cards_used: []` / `cards_remaining: []` / `card_pending: null` で、監査が問題にしている resource は `egg` のみ。`cards_left_unused` も空だったため、現行 audit も切り札残存は主張していない。

issue 全体の決定内訳には `battle_card*` が複数あるが、これらは集計窓内の複数戦闘を含む counts であり、直前 30 行の当該戦闘終盤には切り札選択がない。また `I:c9:ハヤク>タオ:3` の launched snapshot には cards が写っておらず、当該戦闘で実携行が確認された切り札があったとは issue 本文から言えない。したがって切り札未使用は本件の根拠にならない。

## #5760 / PR #5763 との異同

#5760 は step-less の連戦で、選択済み・消費未確認の切り札が sortie ledger (`kit_spent`) に記録されず、次戦で同じ札を再計画して空の切り札一覧を開く経路だった。PR #5763 は `_card_use_unclassified()` が `cur['step'] or flow['kit_step']`、なければ同一 actor の newest launched snapshot を使って `kit_spent` へ記録するよう修正済み。

本件は異なる。当該戦闘の切り札選択・使用が `[]` で pending も Noneであり、step-less な carried card の ledger 漏れとして説明する材料がない。少なくとも #5763 が対象にした「選んだ切り札の消費帳簿漏れ」は本件の卵検出経路ではない。

## 判定の根拠まとめ

誤検知と判定する直接の根拠は、検出器が必要な区別をしていないことにある:

1. 本件は卵戦/召喚獣戦へ入った戦闘である。
2. 現行 audit の owner 契約（test で明示）でも、ledger 値だけでは「卵戦に入っていれば」起票しない。
3. しかし通常パネル復帰で `egg_battle` が False に戻ると、終了時には「卵戦へ入った履歴」が残らない。
4. `egg_white_seen` は使用後の再可用性ではなく、使用前に一度見えただけでも立つ。
5. そのため use→return→loss で `egg_available_unused` が True になり得、`egg_uses: 1` が敗北時の実残数であるかのように high confidence 起票される。

これはゲーム行動が最適だったと証明するものではないが、issue の auto-file 内容が主張する「使える卵を残したまま敗北した」は現証拠では成立しない。したがって本件は **(c) 誤検知**。

## 未決の点

- SHA-256 の実計算は `python3` 実行承認が却下されたため未実施。上記 raw 文字列から検品ゲートが再計算する必要がある。
- 本番 decision log はローテーション済みで存在しないため、`monster_stall` 時の `return_risk`、卵使用直前の正確な `egg_uses`、使用後の実残数は確認できない。
- 召喚獣を戻す戦術そのものが望ましかったかは別途検討余地があるが、本 issue の audit 判定 `(battle_loss_with_resources)` の正誤とは切り分けるべきである。

## handoff / 実行制約メモ

- 明示された正本参照 `DOCICH_HANDOFF_PATH` を確認する shell 実行が承認却下されたため、handoff は未読扱い。通常の read-only 検証を GitHub issue 本文と worktree の現行コードで実施した。
- git の書き込み操作と `gh` コマンドは実行していない。issue 本文と #5760/#5763 は公開 GitHub ページの read-only 取得で確認した。

## 検品ゲート確認（Muse、2026-11-28 10:18 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("9|battle_loss_with_resources|battle_loss_with_resources")[:16] = f601c5186ce59c70、SHA-256("g1190-ch1v9|1190|battle_loss_with_resources|egg|マフィン|I:c9:ハヤク>タオ:3|1795808993")[:20] = b3913d3ad03024e59363。いずれも issue の値と一致する。
- 結論 (c) 誤検知は、卵戦へ入った履歴が通常パネル復帰で消える構造と、`egg_white_seen` が使用前後を区別しない検出器の不足で裏付けられている。退却 False は設計どおり（退却禁止）、切り札は本件の主張外。
