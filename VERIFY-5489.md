# VERIFY-5489: 月末直前に始まったボスフェーズの月内出撃ゼロ

対象: [docich#5489](https://github.com/azumag/docich/issues/5489)  
fingerprint: `26f15a46998467f6` / occurrence: `8f9cb82208479cad8f48`  
判定: **(b) 監査契約どおり。出撃失敗を示す証拠は不足しており、ゲーム挙動の変更は行わない。**

## 記録の確認

sealed incident `g1190-ch1v9`（generation 1190、BOT_VERSION `hanjuku-chart-v1489-discharge-last-general`）から確認した値:

| 項目 | 値 |
|---|---:|
| 対象月 | ch1 2-9 |
| 月の観測 | 803回 / 179.720647秒 |
| 最大観測gap | 0.735701秒 |
| 月の確定出撃 / 全発射 | 1 / 4 |
| 月の全 `order_launched` | 4 |
| ボス | クイーン |
| 残りボス城 | けっかい |
| 月内ボス向け出撃 / launch event | 0 / 0 |
| ボスフェーズ観測 | 37回 |
| ボスフェーズ開始 | 1795550342.804991 |
| 月クローズ | 1795550350.7762501 |

フェーズ継続時間は `closed_at - started_at = 7.971259秒`。月全体の約4.4%にすぎず、月末の約8秒だけボスフェーズが成立していた。月内には通常の出撃が4件あったが、ボス向けの発射はなかった。

`after` は `complete=false` / `early=true` で、月クローズ後の観測窓は完了していない。窓内に1件の発射（最初の発射はクローズ31.1秒後）が記録されているが、これは閉じた月の集計には含まれず、ボス向け出撃の証拠でもない。したがって「月内ボス出撃ゼロ」は正しい一方、「ボスフェーズ中に十分な時間があったのに出撃しなかった」とは結論できない。

## コード・契約との照合

- `src/docich/hanjuku/month_audit.py::_boss_phase` は、通常城をすべて保有し、ボス城が残っている場合にフェーズを返す。
- `_track_boss` はフェーズごとの `started_at` と `observations` を記録する。
- `_close_month` は月クローズ時にフェーズが成立し、月内ボス向け発射数が0なら `boss_month_zero_sortie` を選ぶ。ここに最低フェーズ継続時間の条件はない。
- `_boss_departures` はボス城targetまたは `purpose='boss'` の発射だけを数える。通常移動や通常城向け出撃はボス出撃に混ぜない。
- `docs/operations/hanjuku-month-audit.md` は、月末時点のボスフェーズと月内ボス出撃ゼロを別kindで記録し、フェーズ開始tick・観測回数を保存すると定義している。
- 既存の `VERIFY-5339.md` も、フェーズが月の一部しか覆わない場合があること、`started_at` / `observations` をトリアージに使うことを説明している。

よってこのissueの `confidence=high` は月全体の観測密度・ログ被覆の高さを示すもので、ボスフェーズの出撃機会が十分だったことを示さない。600秒条件の `boss_phase_no_battle` は別の連続フェーズ監査であり、本件の月次kindに流用しない。

## 結論・次の確認

このoccurrenceは監査仕様どおりの事実記録であり、現時点でコードの欠陥は確認できない。約8秒のフェーズに対してdispatch policyや将軍の最低残留数を変更するのは安全でないため、変更しない。

Issueは自動closeせずOpenを維持する。次の完全観測月でボスフェーズが十分な時間継続してもボス向け発射が0なら、plan / garrison / source eligibility / dispatch gateを新しいincidentと照合して再調査する。

## 検証範囲

- GitHub上のsealed incidentとmainの監査実装・運用契約を静的照合。
- 実機の再生・本番VMへの接続・ゲーム入力は行っていない。
- 本PRは監査の意味を変えず、遅いフェーズ開始の解釈を運用文書に明記する文書変更のみ。
