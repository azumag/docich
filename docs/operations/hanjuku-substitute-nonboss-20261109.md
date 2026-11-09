# 半熟英雄: 通常城の出撃は計画と違う将軍でも代わりに出せる（v609、2026-11-10 オーナー指示）

## 指示（オーナー）

「フォールバックとして、計画と違う将軍でもかわりを出撃OKにしていいですよ」

## 変更（v609 `hanjuku-chart-v609-substitute-nonboss`）

出撃画面の将軍名が**実在の将軍**で、計画の将軍と違っても、次の条件を満たせば
**そのまま出撃**します（`sortie_actor_substituted` を記録し、`sortie_general[step]` を更新）。

- 出撃先が**通常城**（ボス城は対象外）
- ボス切り札のキャンセル予算（`_strict_boss_cards`）の対象外
- **計画の将軍が主人公でない**（主人公の出撃は従来どおり取り消し）
- 実画面の将軍が主人公でない

## 確認

- `tests/test_hanjuku_sortie_evidence.py` に1件追加（アテナが代わりに出撃）。
- 出撃・証跡・ボス切り札・診断 **207件 passed**（v607 で衝突した
  `test_public_decide_capped_owner_yields_to_another_order_like_an_unowned_view` も合格）。
- 本番反映後: `sortie_actor_unconfirmed` の取り消しが減ることを実測する。
