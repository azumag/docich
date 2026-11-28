# VERIFY-5793

対象: azumag/docich#5793「[半熟英雄] 卵を残して敗北: ch11 レモン」
検証モード: read-only。コード修正・git 書き込み・gh 操作は未実施。
検証コード: worktree `/home/hatch/work/docich-5793` / branch `verify/5793` / HEAD `50580f17fbabf1fe3024841761273a32e6352990`。
`refs/remotes/origin/main` も同じ `50580f17fbabf1fe3024841761273a32e6352990` を指すことを `.git` 参照から確認した。ボット版はこの tree では `hanjuku-chart-v1597` で、事案発生版 `hanjuku-chart-v1591-clear-sortie-launch-latch` より新しい。
handoff 正本は `DOCICH_HANDOFF_PATH` の明示参照がなく未読。VERIFY-5318 と同じく、GitHub issue 本文と現行コードを根拠にした。

## 結論

**(c) 誤検知**。

ただし誤検知の構造は VERIFY-5318 の単純な「台帳残数だけを見た」型より一歩進んでいる。現行 main では、戦闘中に一度でも白い「たまごをつかう」行を見ると `egg_available_unused` 証拠が残り、その後に実際に卵召喚へ入っても、通常戦闘パネルへ戻った時点で `egg_battle` 証拠が消える。そのため監査には「使える卵があったのに卵戦へ入らず負けた」ように見える。issue #5793 の決定列には召喚獣の技選択そのものが残っており、実際には卵を使った戦闘だった。

## 根拠

### 1. issue 本文の決定列が卵使用を示している

レモン戦の直前決定は、最初は `battle_survival_unavailable`（読める未試行の救済手段なし）と救済メニュー観測予算の消費・再クローズだったが、その後 20.32 秒に `battle_survival_select`（「カーソルと有効な項目を確認して救済行動を選択」）が記録され、続けて `summon_dialogue_paced`、さらに `monster_menu_choice` が 3 回記録されている。`monster_menu_choice` の理由は、召喚獣の技メニューで敵将軍マフィンへの実効ダメージを比較して技を選んだというもので、単なる通常戦闘メニューの開閉ではない。

`battle_enemy_transform_observed` は敵側が卵で変身した記録であり、自軍が卵を使わなかった証拠にはならない。反対に `enemy_general_menu_wait`（敵将軍コマンド表示）と自軍側の `monster_menu_choice` が並んでいることは、自軍召喚に敵が応じた展開と整合する。

`battle_survival_unavailable` / `battle_menu_rescue_exhausted` はメニュー観測や入力の bounded budget を使い切った記録であり、「卵が存在しなかった」「卵を使えなかった」ことを直接証明する終局証拠ではない。実際、その後に `battle_survival_select` と召喚戦が発生している。

### 2. 退却選択 False は設計どおりで、資源未使用の証拠ではない

現行コードでは退却は一律禁止である。`_hero_retreat_menu` は退却可否の確認後に `battle_retreat_banned` を記録して退却せず戦い切る方針で、issue の月間内訳にも `battle_retreat_banned: 2` がある。`battle_hero_retreat_open` は退却実行ではなく救済/退却メニュー確認の入口、`battle_command_open_requested` は B 送信計画であって選択成功ではない。したがって `retreat_selected: false` は異常ではなく、卵を残したまま退却しなかったという意味にもならない。

### 3. 現行 main は卵使用後に「卵戦に入った」証拠を失う

コード上の流れは以下のとおり。

- `src/docich/hanjuku/flows/battle_menus.py:1342-1350`: 白い「たまごをつかう」行を見ると `cur['egg_white_seen'] = True` が立つ。これはその時点では実測証拠として妥当だが、このフラグを消す箇所は見つからない。
- `src/docich/hanjuku/flows/battle.py:1746-1758`: `_egg_available_unused` は `cur['egg_battle']` / `mem['egg_battle']` / `cur['egg_used']` が無ければ、`own_egg_positive_observed` または `egg_white_seen` を「使える卵を残した」証拠として返す。
- 同じ `egg_used` は `src/docich/hanjuku` 内で参照されるだけで、代入・設定箇所がない。つまり使用済みを直接示す永続フラグは実質存在しない。
- `src/docich/hanjuku/flows/battle_menus.py:3155-3181`: 召喚戦へ入ると `mem['egg_battle'] = True` / `battle['egg_battle'] = True` が一時的に立つ。
- しかし `src/docich/hanjuku/flows/battle.py:398-405`: 通常戦闘パネルを再観測すると `cur['card_summon_observed'] = False`、`cur['egg_battle'] = False`、`mem['egg_battle'] = False` に戻る。敗北時の最終 HP パネルは通常戦闘パネルなので、召喚戦を経た後でも戦闘終了時点では `egg_battle` が False になる。
- `src/docich/hanjuku/flows/battle.py:2131-2151`: `battle_result` には `egg_uses`（台帳値）、`egg_uses_source='ledger'`、`_egg_available_unused(mem, cur)`、`egg_battle=bool(...)` が書かれる。コメント自体も `egg_uses` は「最後に観測した台帳値で、戦闘時点の残数ではない」と明記している。
- `src/docich/hanjuku/month_audit.py:1387-1395`: 台帳ソースでも `egg_available_unused is True` なら `egg=1` とし、`egg_battle is not True` の ledger-only も卵証拠にする owner 2026-11-26 option B が入っている。

この組合せにより、レモン戦では次の誤った状態が作られる。

1. 白い卵行を開いて確認した時点の `egg_white_seen=True` が残る。
2. 卵を選んで召喚戦へ入る。
3. 通常戦闘パネルへ戻った時点で `egg_battle` が False にリセットされる。
4. `egg_used` はどこでも設定されない。
5. `battle_result` では `egg_available_unused=True` かつ `egg_battle=False` に見える。
6. 監査は台帳 `egg_uses=1` と合わせて「卵を残して敗北」と起票する。

したがって issue の「卵残数 1」は実残数ではなく、使用可否を一度観測した古いラッチ＋台帳値から作られた検知側の artifact である。`battle_egg_before_retreat: 1` / `battle_egg_plan: 1` が記録されているのに卵残数 1 のまま敗北したように見えるのも、実際には卵を使った後に使用証拠が消えるためで、owner 方針を無視して卵使用を回避した経路ではない。

## 関連 issue / 再発との比較

- #5760（ch5 カシュー）: 結論 (a) で修正 PR #5763 がマージ済み。主問題は step-less の carried card 台帳漏れで、`_card_use_unclassified` と `_carried_opening_tactics` のカード台帳経路だった。#5793 は `step` があり、切り札の選択・使用・残りがすべて `[]` なので、少なくとも主経路は #5760 のカード台帳漏れとは別である。
- #5779（ch9 マフィン）: issue JSONでも `egg_ledger_only: false` / `egg_uses: 1` / resources `['egg']` で、決定列には召喚会話と `monster_menu_choice` が 7 回あり、「たまごに もどれ」選択の後に敗北している。卵使用後に同じ検知 artifact が残る同型の誤検知と判断できる。
- #5784（ch10 キッシュ）: `egg_ledger_only: true` だが、決定列は `battle_survival_select` → `summon_dialogue_paced` → `monster_menu_choice` → 敗北で同じ方向である。`egg_ledger_only` は起票抑制ではなく注記なので、実際に卵戦へ入った戦闘でも high のまま残り得る。
- #5793 の再発コメント 2件は詳細な決定列を持たない要約のみ確認できた。ヴィーナスは resources `['egg','card']` で切り札側の経路も混ざるため本件と完全同一とは断定できない。フェットチーネは resources `['egg']` で同種だが、要約だけでは召喚戦へ入ったかを直接確認できない。ただし現行検出器には上記の構造的欠落があるため、同一 fingerprint の再発が同じ誤検知機序で増えている可能性は高い。
- VERIFY-5318 と fingerprint が同じなのは、fingerprint が将軍名や卵残数を含まず chapter/kind/signals だけから作られるためであり、別将軍・別戦闘でも一致すること自体は矛盾ではない。

## fingerprint / occurrence の再計算

python3 の実行承認が却下されたため、ハッシュは再計算していない。検品ゲートで再計算すること。

現行 main の式:

```python
# src/docich/hanjuku/month_audit.py:3543-3545
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:16]
```

呼び出し側は `month_audit.py:1645` で `_fingerprint(mem.get('chapter'), event['kind'], [event['kind']])` なので、#5793 の入力行は次のとおり。

```text
chapter = 11
kind = battle_loss_with_resources
signals = ["battle_loss_with_resources"]
raw = "11|battle_loss_with_resources|battle_loss_with_resources"
expected fingerprint = sha256(raw.encode()).hexdigest()[:16]
issue 記載値 = dc40d3a96d6e0629
```

occurrence / incident_id の現行式:

```python
# src/docich/hanjuku/month_audit.py:1640-1643
incident_id = sha256(
    f"{runtime_id}|{generation}|{kind}|{','.join(resources)}|{general}|{step}|{int(now)}"
).hexdigest()[:20]
```

#5793 の `occurrence = 9b5cd35e9fe6db029feb` は wall-clock の `int(now)` を含むため、issue 本文だけからは厳密再現できない。入力として分かる値は `runtime_id='g1190-ch1v9'`、`generation=1190`、`kind='battle_loss_with_resources'`、`resources=['egg']`、`general='レモン'`、`step='A:e6c9732e:M21006b'`。`now` は封緘時刻で、issue の production decision log はローテーション済みのため取得不能。

## 未決事項

- 再発コメント 2件には決定列がなく、同じ召喚使用経路だったかは issue コメントだけでは厳密には判定不能。特にヴィーナスは card 資源を含むため、卵 artifact に加えて別のカード経路がある可能性がある。
- 本検証ではコード修正しない。修正する場合の論点は、少なくとも `egg_white_seen` を使用・召喚確定時に消す/使用済みフラグを永続化する、または通常パネルへ戻っても「この戦闘では卵戦に入った」ことを最終 `battle_result` まで保持することである。現行の `egg_battle` は進行中状態であり、終了後の「入ったことがある」証拠としては使えない。

## 検品ゲート確認（Muse、2026-11-28 14:47 JST）

- fingerprint を検品ゲートで再計算した。SHA-256("11|battle_loss_with_resources|battle_loss_with_resources")[:16] = dc40d3a96d6e0629。issue の値と一致する。
- occurrence も再計算した。issue 本文の `created_at` は 1795819392.056565 で、`int(now)` = 1795819392 とすると SHA-256("g1190-ch1v9|1190|battle_loss_with_resources|egg|レモン|A:e6c9732e:M21006b|1795819392")[:20] = 9b5cd35e9fe6db029feb。issue の値と一致する。
- 結論 (c) 誤検知は、決定列に召喚戦の証拠（`battle_survival_select` → `summon_dialogue_paced` → `monster_menu_choice` 3回）がある一方で、現行 main では召喚戦へ入った後に通常戦闘パネルへ戻ると `egg_battle` が False へリセットされ、`egg_used` を設定する箇所がどこにもないため、監査に「卵を残して敗北」と見える構造で裏付けられている。#5779・#5784 と同型の誤検知である。
