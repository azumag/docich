# VERIFY-5784 — [半熟英雄] 卵を残して敗北: ch10 キッシュ

- 対象: azumag/docich issue #5784 / fingerprint `99978f677cd94c6b` / occurrence `23fe241e2b9b5f1fd1a8`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1588-corridor-probe-count`
- 戦闘: キッシュ vs リディア（城マカリア、side: attack）、loss、HP ally 0 / enemy 72、卵残数表示 1 / 切り札 選択 []・使用 []・残り []・pending None
- 検証範囲: read-only（本ファイルの作成以外の編集なし）。git 書き込み操作・gh コマンドなし。
- 現行コード: worktree `verify/5784` / `src/docich/hanjuku/version.py` は `hanjuku-chart-v1597-chikujou-defer-reason`

## 結論: (c) 誤検知

ただし検出器の構造的な不足に起因する誤検知であり、現行 v1597 でも同じ構造が残っている。

本件の敗北は、キッシュが同じ城マカリアでの直前の守備将軍戦で卵召喚へ入り、その勝利後に次の守備将軍リディアへ引き継がれた連戦の第2戦である。issue の決定列は、第1戦で `battle_egg_before_retreat` → `battle_survival_select` → 敵変身（`battle_enemy_transform_observed`）→ 味方所有者の召喚獣技メニュー（`monster_menu_choice`）へ進んだことを示す。`monster_menu_choice` の理由「攻撃データ上、将軍『マスカット』への実効ダメージが10対60」は、現行コードでは `_monster_attack_policy_default` が `owner == 'ally'` を必須とする枝でだけ生成される。したがって味方召喚獣が出ていたことは確実で、卵は少なくともこの連戦内で使用済みである。

その直後に `attack_defender_successor`（守備将軍を倒し、同じ城の次の守備将軍が出た）があり、新しい `battle_start` / `battle_egg_plan` を経て約2秒でリディア戦の `battle_result`（loss）が記録されている。監査が拾った loss はこの第2戦である。卵残数 1 は第2戦開始前に再観測された実残数ではなく、第1戦の卵使用を差し引かない古い台帳値なので、「敗北時に使える卵が1個残っていた」とは断定できない。台帳値 1 が使用前の観測なら、1回の召喚消費後の実残数は0であった可能性が高い。

## ハッシュ式と再計算

`python3` / shell 実行は承認却下され、外部ハッシュサービス利用も却下されたため、sha256 の再計算は **未実施**である。検品ゲート再計算用に、現行 `src/docich/hanjuku/month_audit.py` の式と入力値を残す。

### fingerprint

現行式（`_fingerprint`）:

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
fingerprint = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

battle resource incident では `record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])` なので、issue の signals 配列全体ではなく `[event['kind']]` だけが入る。本件の入力:

```text
10|battle_loss_with_resources|battle_loss_with_resources
```

期待値（issue 記載）: `99978f677cd94c6b`

### occurrence / incident_id

現行式（`_build_battle_resource_incident`）:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
incident_id = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue JSON の値:

- runtime_id: `g1190-ch1v9`
- generation: `1190`
- kind: `battle_loss_with_resources`
- resources join: `egg`
- general: `キッシュ`
- step: `A:600cbbd7:Mbaffde`
- created_at / now: `1795814363.928973` → int `1795814363`

したがって raw は:

```text
g1190-ch1v9|1190|battle_loss_with_resources|egg|キッシュ|A:600cbbd7:Mbaffde|1795814363
```

期待値（issue 記載）: `23fe241e2b9b5f1fd1a8`

## 根拠の詳細

### 1. 監査が「卵残数 1」とした機構

`flows/battle.py` の `battle_result` は `egg_uses` を mem の台帳から書き、`egg_uses_source: 'ledger'`・`egg_available_unused`・`egg_battle` を添える。`month_audit.py` の `_battle_resources` は台帳ソースの正数について、実測の unused も卵戦入場も無い場合に `egg_ledger_only` を立てて卵資源として起票する。本件 JSON は `egg_ledger_only: true` であり、これは実測残数ではなく「台帳だけの証拠」である。

現行コードでも通常戦闘パネルの再観測は `_card_battle_reading` が `cur['egg_battle'] = False` と `mem['egg_battle'] = False` に戻す。さらに新しい戦闘レコードは連戦ごとに新規作成される。このため「直前戦で卵を使ったが、最終戦では台帳だけが残る」経路では、監査の `egg_battle` ガードが効かない。

テスト `tests/test_hanjuku_month_audit.py` には owner 2026-11-26 option B として「卵戦に入っていないなら台帳の正数を使わなかった証拠にする」契約があるが、卵戦入場の判定が戦闘終了時点の `egg_battle` フラグ依存なので、連戦・通常パネル復帰を横断した使用履歴は捕捉できない。

### 2. 本件の卵使用証拠

issue の決定列:

1. `battle_egg_before_retreat` — 「本人の卵残数が正数で未試行のため、退却より先に実メニューの召喚可否を確認」。general はキッシュ、step は `A:600cbbd7:Mbaffde`。
2. `battle_survival_select` — 救済行動の選択。decision row には label が出ないが、少なくとも救済メニューで資源選択へ進んだ。
3. `battle_enemy_transform_observed` — 敵が卵を使用して変身し、開幕の卵温存判断を無効化。
4. `monster_menu_choice` — 「ウイング」を選択。理由は attack-data policy 枝であり、`_monster_attack_policy_default` は `owner != 'ally'` なら即 None を返す。よって owner は味方であり、味方召喚獣の技メニューだった。
5. `battle_result` → `sortie_outcome` → `attack_defender_successor` → 新しい `battle_start` / `battle_egg_plan` → 約2秒後の第2の `battle_result`。

`attack_defender_successor` の実装条件は、攻撃側の現戦で `enemy_hp == 0` かつ `ally_hp > 0`、同じ攻撃将軍に対して別の守備将軍パネルが出ることである。つまり最初の `battle_result` は勝利であり、監査対象の loss はその後の次守備将軍戦である。`battle_end(..., attack_continues=True)` で前戦を閉じ、`reset_battle_controls` と新規 `mem['battle']` によって戦闘ごとの卵戦フラグ・survival 状態は引き継がれない。

### 3. 敵変身後の卵使用機会

`battle_enemy_transform_observed` は開幕の卵温存ラッチ（`conserving_melee` 等）を消すだけで、卵使用済みの事実を消すものではない。敵召喚時の独立判断は `battle_menus.py` の summon メニュー側で `egg_action` / `egg_battle` を立てるが、本件では決定内訳に `egg_battle` が無く、技メニュー直接の観測だけが残っている。それでも味方 owner の `monster_menu_choice` があるため、卵使用の証拠は残る。選択技「ウイング」は reference 上レッドドラゴンの skill1 で、attack-data policy は2技の一致も要求するため、味方側にレッドドラゴン相当の召喚獣が出ていたと読める。

反対に、監査が参照する `egg_battle` は通常パネル再観測で false に戻り、次戦では新規 battle record に置き換わる。したがって「敵変身後に卵を使った」経路ほど、現行監査は卵を残して敗北したように見せやすい。

### 4. 退却選択 False と battle_retreat_banned

`retreat_selected: false` は `hero_retreat_selected` が true でなかっただけであり、本件では欠陥証拠ではない。現行 `_hero_retreat_menu` は owner 2026-11-23 の方針で退却を一律禁止し、`battle_retreat_banned` を1戦闘1回記録して `None` を返す。つまり退却しないのは設計どおりである。

issue の決定内訳に `battle_retreat_banned: 1` はあるが、内訳は直前120秒の月内集計であり、それだけでは最終リディア戦のどの観測で発生したかは特定できない。いずれにしても、選択されていないこと自体は禁止設計と一致する。

owner 方針（docich#4999 系）は退却前に卵・切り札を使うことだが、本件では卵は連戦内で使用済みであり、切り札は最終戦の `cards_selected/cards_used/cards_remaining` がすべて空である。したがって「使い切る前に退却した/戦死した」ことをこの issue の卵残数だけからは立証できない。

### 5. #5760 / #5779 との異同

- #5760: 実欠陥 (a)。中間城・連戦で step を持たない戦闘へ carried card の unclassified 証拠が残らず、次戦で stale snapshot から開幕札が再計画された。修正 PR #5763 済み。本件は切り札の選択・使用・残りがすべて空で、主資源は卵のみなので、#5760 の step-less carried card 台帳漏れとは別経路である。
- #5779: 誤検知 (c)。敵の卵変身に対して味方召喚獣戦へ入り、技選択後に通常戦闘へ戻って敗北したのに、卵戦履歴が消えて台帳 `egg_uses` が残った。検証コメントも v1593 時点で同構造が残ると指摘している。本件は「同一戦で use→return→loss」ではなく、「前の守備将軍戦で卵使用 → successor で次守備将軍戦 → 約2秒で loss」という連戦横断型だが、台帳残数を実残数と誤認する点では同じクラスである。
- VERIFY-5300（同じ fingerprint `99978f677cd94c6b` の ch10 リディア/マカリア系）でも、味方召喚獣が出ているのに監査の卵残数が召喚消費を反映しない stale な直前帳簿であることが確認されている。

### 6. 再発3件

コメントの再発:

- ch10 14-6 ヘスティア / occurrence `0b45a33bfab723859515`
- ch10 14-8 チキータ / occurrence `d938a02cb12db1ea5748`
- ch10 14-12 ブリオッシュ / occurrence `e245b2c202c708a88f15`

いずれも同じ fingerprint で、資源は `['egg']`、confidence high、切り札情報のない短文コメントである。しかし再発コメントには直前の決定列や JSON が無く、本番 decision log はローテーション済みなので、味方召喚・連戦 successor の有無まで同一経路だったかは **確認できない**。fingerprint は章・kind・主 signal だけから作られるため、同一 fingerprint は同一 root cause の証拠にならない。現時点では「卵のみ・同章・同時期の高 confidence 再発」までは一致だが、詳細経路の同一性は未決である。

## 未決・限界

- sha256 の fingerprint / occurrence 再計算: 実行承認が無く未計算。上記の式と入力値から検品ゲートで再計算すること。
- 再発3件の詳細経路: issue コメントだけでは判定不能。主 occurrence の経路とは限らない。
- 卵残数 1 が「使用前 1」だったことは強く示唆されるが、本番 decision log が無いため、ゲーム内部の実残数を直接観測した証拠ではない。それでも監査の high confidence「卵を残して敗北」を支えるには、味方召喚済みの証拠に反するため誤検知と判定した。

## 手順上の注記

- `DOCICH_HANDOFF_PATH` の確認に必要な shell 実行が拒否されたため、非公開正本 handoff は未読。worktree `./handoff.md` への fallback はしていない。
- GitHub issue 本文・再発コメントは gh ではなく公開 issue ページの read-only 取得で確認した。

## 検品ゲート確認（Muse、2026-11-28 12:48 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("10|battle_loss_with_resources|battle_loss_with_resources")[:16] = 99978f677cd94c6b、SHA-256("g1190-ch1v9|1190|battle_loss_with_resources|egg|キッシュ|A:600cbbd7:Mbaffde|1795814363")[:20] = 23fe241e2b9b5f1fd1a8。いずれも issue の値と一致する。
- 結論 (c) 誤検知は、味方召喚獣が出ていたこと（`monster_menu_choice` が `owner == 'ally'` の枝でのみ生成される理由文）と、監査が参照した卵残数が `egg_ledger_only: true` の台帳値であることで裏付けられている。再発3件は同一 fingerprint だが決定列がなく、詳細経路の同一性は未決である。
