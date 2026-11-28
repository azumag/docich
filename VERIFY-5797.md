# VERIFY-5797 — [半熟英雄] 卵を残して敗北: ch12 アテナ

## 結論

**(c) 誤検知**

本件は「アテナが卵を一度も使わずに敗北した」経路ではない。issue の直前決定列だけでも、直前の守備将軍戦で味方側の召喚獣メニューへ入り、アマゾンの技「ガラハドのけん」を複数回選んだことが読み取れる。使用後の残数 0 を直接読んだ証拠はないが、前戦で卵を消費した強い証拠があり、その後の回復・再取得証拠はない。少なくとも、敗北時に使用可能な卵が 1 個残っていたという監査側の主張は証明できない。

監査が `卵残数: 1` とした値は、現行コードでも戦闘時点の実残数ではなく最終観測台帳値である。守備将軍連戦で前戦の卵使用が次戦の `egg_battle` フラグへ引き継がれないため、台帳の stale な正数だけを根拠に `battle_loss_with_resources` / high confidence へ分類した検出器側の誤検知である。

## 検証範囲

- 本番 decision log はローテーション済みのため参照していない。issue 本文の JSON / 直前決定列と、現在の worktree のコードだけを使った。
- `gh` コマンドと git 書き込み操作は実行していない。issue 本文は公開 GitHub ページの表示内容で確認した。
- コード修正・commit・push・PR 化はしていない。

## 1. fingerprint / occurrence の式と入力値

現行 `src/docich/hanjuku/month_audit.py` の battle resource incident builder は、fingerprint を次のように計算する。

```python
record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])

def _fingerprint(chapter, kind, signals):
    raw = f"{chapter}|{kind}|{'+'.join(signals)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

該当箇所: `src/docich/hanjuku/month_audit.py:1645`, `:3543-3545`。
`signals` 配列全体ではなく `[event['kind']]` だけを渡す。issue の値を代入した raw は:

```text
12|battle_loss_with_resources|battle_loss_with_resources
```

期待される fingerprint は issue 記載の `17db88f7ee083127`。

occurrence / incident_id は月次一般式ではなく builder 固有の次式である（`src/docich/hanjuku/month_audit.py:1640-1643`）。

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue JSONの値を代入した raw は:

```text
g1190-ch1v9|1190|battle_loss_with_resources|egg|アテナ|A:94d33d73:M9e607f|1795823452
```

`now` は issue JSONの `created_at = 1795823452.2728162` の整数部。期待される occurrence / incident_id は `97235b9c0b3f128c401f`。

**再計算は未実施**: この検証レーンでは `python3` 実行が承認却下されたため、sha256 の実計算はここでは行っていない。検品ゲートで上記 raw から再計算されたい。

## 2. 時間順に見た実経路

issue JSONの `decisions.before` から、少なくとも次の順序が分かる。

1. `1795823433.372` ごろ、前の守備将軍戦で `battle_survival`（HP低下のため温存を中止して救済メニューを確認）が出る。
2. `1795823433.603` に `battle_retreat_banned`、その後 `battle_survival_select` が2回続く。
3. `1795823439` ごろ召喚戦会話を経て、`battle_enemy_transform_observed` と同時刻に `monster_menu_choice` が出る。
4. `1795823439` / `1795823444` の2回とも、理由文は「召喚獣の技メニューで『ガラハドのけん』を選択: 攻撃データ上、将軍『アンディーブ』への実効ダメージが18対24のため…」である。
5. `1795823450.214813` に一度 `battle_result` が出て、同時刻に `attack_defender_successor` → `battle_start` → `battle_egg_plan` が続く。`attack_defender_successor` の成立条件は、前戦敵HPが0・味方HPが正・同じ攻撃側将軍に次の守備将軍が出たことである（`src/docich/hanjuku/flows/battle.py:186-219`）。したがってここで前戦終了・次の守備将軍戦開始である。
6. 最後に `1795823452.268773` の `battle_result` があり、incident の敵はマルガリータ、HP ally 0 / enemy 72 である。次戦開始から敗北まで約2.05秒しかない。この間に `battle_survival`、`battle_survival_select`、卵使用開始、白文字卵行の観測は出ていない。

### 味方が卵を使った証拠

`monster_menu_choice` の理由文は重要である。現行コードでは、攻撃データに基づく選択理由 `攻撃データ上、…への実効ダメージが…のため…を選ぶ` は `_monster_attack_policy_default` の evidence を使う分岐でだけ生成される（`src/docich/hanjuku/flows/battle_menus.py:4335-4341`, `:4359-4379`）。ところが `_monster_attack_policy_default` は先頭で `owner != 'ally'` なら即 `None` を返す（`src/docich/hanjuku/judgment/events.py:295-298`）。

したがって、issue の「実効ダメージが18対24」という理由文が存在する以上、そのメニューは敵側待機ではなく **ally-owned な召喚獣メニュー**だったと判定できる。また「ガラハドのけん」はアマゾンの skill2 である（`src/docich/hanjuku_reference.py:1094-1097`）。前戦の攻撃側将軍はアテナなので、アテナ側が卵でアマゾンへ変身し、技選択を行ったと読むのが自然である。

なお `battle_enemy_transform_observed` は、この結論を左右しない。現行コードは敵名前/HPの変化だけでなく、メニュー行に `たまごにもどれ` があるだけでも `transformed = True` にする（`src/docich/hanjuku/flows/battle_menus.py:3989-4001`）。召喚獣メニューには通常この戻る行があるため、この decision 単体は敵が卵を使った確定的な証拠ではない。仮に敵も卵を使っていたとしても、上記の ally-owned メニュー証拠は残る。

## 3. なぜ監査が `卵残数: 1` にしたか

`battle_result` へ入る `egg_uses` は `(mem.get('egg_uses') or {}).get(cur.get('ally'))` であり、コメントにも「最終観測台帳値で、戦闘時点の残数ではない」と明記されている（`src/docich/hanjuku/flows/battle.py:2131-2147`）。

監査側 `_battle_resources` は、`egg_uses_source == 'ledger'` のとき、

- `egg_available_unused` の実測がなく、
- 台帳値が正で、
- `metric['egg_battle'] is not True`

なら `egg = 1`、`egg_ledger_only = True` にする（`src/docich/hanjuku/month_audit.py:1379-1395`）。issue JSONでも `egg_ledger_only: true` であり、実測残数ではないことが明示されている。

この `egg_battle` は「現在の戦闘で卵戦に入ったか」のフラグにすぎない。前戦で卵を使っても、

- 通常戦闘パネルを読むと `_card_battle_reading` が `cur['egg_battle']` / `mem['egg_battle']` を false に戻し（`src/docich/hanjuku/flows/battle.py:398-405`）、
- 守備将軍連戦では `_attack_defender_successor` が前戦を `battle_end` し、`reset_battle_controls` へ進む（`src/docich/hanjuku/flows/battle.py:212-219`）、
- `reset_battle_controls` も `mem['egg_battle'] = False` にする（`src/docich/hanjuku/judgment/battle.py:518-527`）

ため、前戦での卵使用は次戦の `metric['egg_battle']` を true にしない。さらに卵使用回数は選択や召喚から推測減算せず、実観測された整数だけを使う設計である（例: `src/docich/hanjuku/judgment/battle.py:3382-3400`）。そのため、前戦で1回消費した後でも、更新済み残数を画面から読めていなければ台帳は stale な `1` のまま残る。

本件はこの構造そのものである。最終敗北したマルガリータ戦では卵戦に入っていないので、監査は「この戦闘で卵を使っていない」ことを正しく検出したにすぎず、「敗北時にまだ使える卵が残っていた」ことは証明していない。

## 4. 退却方針・切り札との関係

- 現在の実装では退却は owner 方針により一律禁止である。`_hero_retreat_menu` は `battle_retreat_banned` を1戦闘1回記録し、切り札・卵を使い切った後も戦い切る（`src/docich/hanjuku/flows/battle_menus.py:704-714`）。したがって `退却選択: False` は欠陥証拠ではなく、この方針と矛盾しない。
- issue の決定内訳にも `battle_retreat_banned: 2` があり、少なくとも前戦では明示的に記録されている。これは退却し損ねたのではなく、退却を選ばない設計である。
- `battle_event` の切り札は 選択 `[]` / 使用 `[]` / 残り `[]` / pending `null` であり、本 incident の資源は `['egg']` だけである。120秒集計には前戦や他戦闘の card 系 decision が残るが、最終敗北の `battle_event` に未使用切り札証拠はない。切り札台帳は本件の主因ではない。

## 5. #5760 / #5779 との異同

### #5760 との違い

#5760 は実欠陥と判定され、修正 PR #5763 がマージ済みである。あちらは step-less 連戦で、選択済み・消費未確認の切り札が `kit_spent` へ記録されず、stale snapshot から存在しない切り札を再計画したことが主因だった。卵を実際に使った証拠はなく、メニュー遠回りが卵使用機会を狭めたという経路だった。

本件は違う。切り札の選択・使用・残り・pending は全て空で、むしろ ally-owned な召喚獣メニューにより前戦で卵を使った証拠がある。#5763 の step-less card ledger 修正の対象外である。

### #5779 との類似と差異

#5779 は既に「誤検知」とコメントされている。あちらは同一戦闘内で卵使用・通常パネル復帰・敗北が起き、通常パネル復帰時に卵戦履歴が消える構造が指摘された。

本件は同じ検出器上の欠陥群だが、時間構造は少し違う。前の守備将軍戦でアテナ側が卵召喚し、その前戦に勝って次の守備将軍マルガリータへ移行し、約2秒で敗北した。つまり #5779 の「同一戦闘内 use→return→loss」ではなく、「前戦 use → attack_defender_successor → 次戦 loss」である。`egg_battle` を現在戦闘だけで見る監査は、この連戦跨ぎでも stale 台帳を誤って残存卵とみなす。

## 6. 補足: confidence について

`docs/operations/hanjuku-month-audit.md:147-150` には、`battle_result` の `egg_uses` は最終観測台帳値であり、卵だけが残る敗北は当該戦闘で使用可能だった証拠ではないため medium とする趣旨が残っている。一方、現行 builder は `cards_unconfirmed_only` の場合だけ medium に下げ、`egg_ledger_only` 単独では high を下げない（`src/docich/hanjuku/month_audit.py:1590-1597`）。本件は、この high 扱いが少なくとも連戦跨ぎでは成立しない実例である。

## 未決の点

- ハッシュは式と入力値を特定したが、`python3` 実行却下のため sha256 の実計算は未実施。検品ゲートでの再計算が必要。
- `battle_survival_select` の選択 label は issue の決定表 projection に出ていない。本番 decision log はローテーション済みなので確認できない。ただし ally-owned 召喚獣メニューの証拠が独立にあるため、結論には影響しない。
- 敵が実際に卵変身したかどうかは、現行の `battle_enemy_transform_observed` だけでは確定できない。ただしこれも本件の結論には必要ない。
- 最終マルガリータ戦の約2秒間に仮に入力機会があったかは判断しない。前戦で卵を消費済みであることが本筋であり、最終戦で卵を選ばなかったこと自体を実欠陥とみなす根拠はない。

## 検品ゲート確認（Muse、2026-11-28 15:56 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("12|battle_loss_with_resources|battle_loss_with_resources")[:16] = 17db88f7ee083127、SHA-256("g1190-ch1v9|1190|battle_loss_with_resources|egg|アテナ|A:94d33d73:M9e607f|1795823452")[:20] = 97235b9c0b3f128c401f。いずれも issue の値と一致する。
- 結論 (c) 誤検知は、前戦でアテナ側が卵でアマゾンへ変身し「ガラハドのけん」を選んだ ally-owned 召喚獣メニューの独立証拠があり、監査の「卵残数 1」が最終観測台帳値（`egg_ledger_only: true`）にすぎないことで裏付けられている。#5779・#5784・#5793 と同じ家族の再発である。
