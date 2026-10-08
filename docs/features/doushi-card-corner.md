# 同志カードコーナー — ランキング仕様（集計指標の定義と発表形式）（設計案）

- Status: **Proposal（起案）**。実装・配備は未着手。production の corner catalog と TwiCa 側 API は変更しない。
- 関連 Issue: [azumag/docich#934](https://github.com/azumag/docich/issues/934)（同志カードコーナーを追加。issue 本文は下書きであり実装しない指示つき）
- 関連リポジトリ: [azumag/twica](https://github.com/azumag/twica)（同志カードの正本。本ドキュメントの集計はすべて TwiCa 側の定義に従う）
- 関連実装（docich 側）: `src/docich/corner_catalog.py` / `corner_rotation.py` / `corner_adapters.py`（24時間ローテーション）、
  `src/docich/twica_renderer.py` / `twica_browser_health.py` / `twica_service.py`（TwiCa 前景オーバーレイの既存接続）、
  `docs/features/yosou-oh-corner.md`（同じ「コーナー追加の起案」の先行例）

## 0. 結論（本カードの質問への回答）

> 「同志カードコーナーのランキング仕様（集計指標の定義と発表形式）を決めてから着手する方針でよいですか」

**その方針でよい。** 理由は、必要な集計がすべて TwiCa の正本 DB 側にあり、
docich 側の判断だけでは指標の意味（N連の数え方・除外アカウント・期間境界）を決められないため。
本ドキュメントがその「決めた仕様」であり、これ以上の実装着手は本ドキュメントの承認後に行う。

本ドキュメントで **決めたこと** と **オーナー判断が必要なこと** を分けて書く（§3・§7）。

## 1. 何を作るか

Twitch のチャネルポイントで同志カードを引いた視聴者の成績を、番組ローテーションの
1コーナーとして発表する。コーナー名（仮）: **「同志カード大賞」**。

- 対象は配信者（Soren のチャンネル）1つ。TwiCa の `streamer_id` に紐づく同志カードのガチャ履歴。
- 発表する部門は 4 つ: **① 排出枚数 ② 使用チャネルポイント ③ 蒐集カード数（ユニーク）④ 人気カード**。
- 表示は 960×540 の読み取り専用合成 view（既存 weather / tsuitate / 予想王と同じ方式）＋読み上げ。
- docich は **表示のみ**。集計・正本・除外判定は TwiCa に置き、docich に TwiCa の DB 資格情報を持ち込まない。

## 2. データ源の調査（一次情報）

確認日 2026-10-08。参照した TwiCa `main` = `d11540818ad5f6748b2f9a84640de692579fb628`（2026-10-07）。

### 2.1 正本

TwiCa は Next.js on Cloudflare Workers、永続データの正本は PlanetScale PostgreSQL。
同志カードの引き換えは `gacha_history` に 1 カード 1 行で確定する。

```
gacha_history(
  id uuid, user_twitch_id text, user_twitch_username text,
  card_id uuid, streamer_id uuid, redeemed_at timestamptz,
  event_id text, reward_cost integer, reward_id text
)
```

### 2.2 既存の集計（そのまま使える正本）

| 用途 | 関数 / テーブル | 返すもの | 並び順（正本） |
| --- | --- | --- | --- |
| 使用ポイント・枚数（全期間） | `channel_point_usage_stats` を読む `get_channel_point_usage_stats(p_streamer_id, p_from_date, p_limit)` | `{total_points, ranking[{user_twitch_id, username, total_points, redemption_count, last_redeemed_at}]}` | `total_points DESC, redemption_count DESC, last_redeemed_at DESC` |
| 使用ポイント・枚数（期間指定） | 同上（`p_from_date` あり） | 同上 | 同上 |
| 枚数・蒐集カード | `get_gacha_users_for_streamer(p_streamer_id, p_limit, p_offset)` | `{users[{user_twitch_id, username, draw_count, last_draw_at, unique_card_ids[]}], total}` | `draw_count DESC` |
| 人気カード | `get_gacha_drop_stats(p_streamer_id, p_from_date, p_limit_per_card)` | `card_stats[{card_id, card_name, rarity, image_url, actual_count, actual_rate, drawer_count, drawers[{user_twitch_id, username, draw_count, last_drawn_at}]}]` | `rarity_order ASC, created_at DESC`（人気順ではない） |
| 除外判定 | `is_redemption_ranking_excluded(p_streamer_id, p_user_twitch_id)` | 配信者本人 / `twitch_bot_accounts(owner_type='streamer')` / `twitch_bot_accounts(owner_type='system')` を真 | — |

`is_redemption_ranking_excluded` は `refresh_channel_point_usage_stat` から呼ばれ、
除外対象が引き換えると `channel_point_usage_stats` の該当行が DELETE される（自己修復）。
したがって全期間の `channel_point_usage_stats` は「除外済み」の値を返す。

### 2.3 集計述語（数え方の正本）

配信者向けランキングの対象行は **`(reward_cost > 0 OR reward_id IS NOT NULL)`**。

- `executeGachaDraws` は N連ガチャでも `reward_cost` を先頭行にだけ載せる（Twitch EventSub が
  引き換え1回の合計コストしか通知しないため、二重計上を避ける意図的な設計）。`reward_id` は全行へ引き継ぐ。
- この述語により **N連は N 件**として数えられる。`SUM(reward_cost)` は NULL を無視するので
  ポイント合計は N 倍にならない（= 実際に払ったポイント）。
- 対象外: レイドガチャ、手動 QA ドロー（`event_id = 'manual:<uuid>'`。drop stats 側は `event_id NOT LIKE 'manual:%'` で除外）、
  配信者本人、登録済み BOT、共有 system BOT。
- **既知の境界**: `reward_id` は 2026-07-04 に導入された。それ以前の N連2枚目以降は履歴から復元できない。
  したがって全期間の「排出枚数」は 2026-07-04 以前で過少になり得る。この境界を推定で埋めない
  （TwiCa 側の既存方針と同じ）。

### 2.4 現行の公開 API で足りるか → **足りない**

| 経路 | 認証 | 取得できるもの | 判定 |
| --- | --- | --- | --- |
| `GET /api/overlay/{streamerId}/events?since=&afterId=` | 不要（公開・OBS 用の gap-recovery） | `id, event_id, redeemed_at, user_twitch_username, reward_id` + カード `{id,name,description,image_url,rarity}` | **不足**: `reward_cost`（チャネポ）と `user_twitch_id` が無い。期間・枚数の集計も不可 |
| `GET /api/gacha-history` / `/api/gacha-stats` / `/api/user-cards` | 要ログイン（配信者 or 本人） | 集計つき | **不適**: docich から使うには TwiCa のセッション資格情報が必要 |
| `GET /api/overlay/{streamerId}/realtime-config` | 不要 | トランスポート設定のみ | 集計なし |

docich 側の既存接続は `SOREN_DIRECT_TWICA_OVERLAY_URL`（`/overlay/...`）を
**ブラウザで開く**だけで、TwiCa の API を読む HTTP クライアントは存在しない
（`twica_browser_health.py` は `/api<overlay path>/events` の成否カテゴリだけを数える）。
`validate_url()` が URL をログ・argv に出さない契約なので、streamer id は既存 URL から
導出し、URL 本文・token をログ・state・Git へ残さない。

→ **結論: 配信者単位の読み取り専用ランキング API を TwiCa 側に新設する**（§6 PR-2）。
docich は集計済み JSON を表示するだけにする。これが「指標の定義を TwiCa に一本化する」
という本ドキュメントの中心的な決定である。

## 3. 集計指標の定義（確定）

集計は `user_twitch_id` 単位（表示名が変わっても同一人物として合算）。

| 部門 | 表示名 | 定義 | 出典 | 単位・注記 |
| --- | --- | --- | --- | --- |
| ① | 排出枚数 | 対象述語を満たす `gacha_history` 行数（`COUNT(*)`） | `channel_point_usage_stats.redemption_count`（全期間）/ 期間指定時の `COUNT(*)` | 枚。**N連は N 件**。2026-07-04 以前の N連2枚目以降は含まれない |
| ② | 使用チャネルポイント | `SUM(reward_cost)` | `channel_point_usage_stats.total_points` / 期間指定時の `SUM(reward_cost)` | ポイント。N連の cost は先頭行のみなので二重計上しない |
| ③ | 蒐集カード数 | 当該配信者の `is_active` なカードのうち所持している種類数（`COUNT(DISTINCT card_id)`） | `get_gacha_users_for_streamer.unique_card_ids` | 種類。**所持ベース（全期間）**であり期間集計ではない |
| ④ | 人気カード | カード別の期間内 `COUNT(*)`（引かれた枚数）と `drawer_count`（引いた人数） | `get_gacha_drop_stats.card_stats.actual_count` / `.drawer_count` | 枚・人。`drawers[]` は上位のみ |

- ①②④ は §2.3 の対象述語と除外判定を共通に使う。③ は所持テーブル由来のため述語が異なる（下記の差異に注意）。
- **人気カードの並び替えは TwiCa 側で行う**（現行 RPC はレアリティ順で返すため、人気順ソートを docich に押し付けない）。
- **既知の差異（要対応）**: `get_gacha_users_for_streamer` は述語も除外判定も適用しておらず、
  手動 QA ドローと BOT 行も `draw_count` に数える。このため ① に `draw_count` を使うと
  `redemption_count` と一致しない。**① は `channel_point_usage_stats` 側の値を正本とする**こととし、
  ③ のユニーク集計も新設 API 内で同じ除外判定を適用する（§7-8）。

### 3.1 順位の決定則（同率の決定的順序）

TwiCa の既存並びを正本とし、docich 側で独自に並べ替えない。同率の最終決定順序は:

| 部門 | 第1キー | 第2キー | 第3キー |
| --- | --- | --- | --- |
| ① 排出枚数 | `redemption_count` DESC | `last_redeemed_at` DESC | `user_twitch_id` ASC |
| ② 使用チャネルポイント | `total_points` DESC | `redemption_count` DESC | `last_redeemed_at` DESC → `user_twitch_id` ASC |
| ③ 蒐集カード数 | `unique_count` DESC | `draw_count` DESC | `user_twitch_id` ASC |
| ④ 人気カード | `actual_count` DESC | `drawer_count` DESC | `card_id` ASC |

- `last_redeemed_at` も同値のときは `user_twitch_id` 昇順で確定させる（表示が run ごとに揺れないようにする）。
- 取得点数は `limit`（既定 10）+ 同率判定用の余裕分。表示は上位 `limit` 件。

## 4. 発表形式（確定）

1コーナー = 4ページ、program slot を 1 つだけ使う読み取り専用 view。

| ページ | 内容 | 表示 |
| --- | --- | --- |
| 1 | 排出枚数 TOP10 | 順位 / 「同志◯◯」 / ◯枚 |
| 2 | 使用チャネルポイント TOP10 | 順位 / 「同志◯◯」 / ◯pt |
| 3 | 蒐集カード数 TOP10 | 順位 / 「同志◯◯」 / ◯種類 |
| 4 | 人気カード TOP10 | 順位 / カード名 / ◯枚（◯人が獲得） |

- **ページ送り**: 1ページ 15 秒 × 4 = 60 秒。コーナー上限は 2 分（既存 weather と同じ上限方式）。
  上限に達したら自動終了し、既存の GameSwitch 復帰経路へ戻す。ページ送りは view の描画内で行い、
  コーナーは program slot を追加で消費しない。
- **呼称**: 視聴者の呼称は既存契約に合わせ「同志◯◯」（`src/docich/comment/prompts/comment_reply_policy_contract.md`）。
- **注記を画面に出す**（誤解を招かないため、各ページ下部に常時 1 行）:
  - ページ1: 「排出カード枚数（N連はN件）。2026-07-04 以前の N連2枚目以降は含みません」
  - ページ2: 「使用チャネルポイント合計」
  - ページ3: 「現在蒐集しているカードの種類数（全期間）」
  - ページ4: 「期間内に引かれた枚数と人数」
  - 全ページ共通: 「配信者本人と BOT を除く」/ 集計期間
- **読み上げ**: 各ページ上位 3 件を短く実況（既存の実況経路を使い、コーナー専用の TTS 経路は新設しない）。
- **更新**: コーナー開始時に 1 回だけ取得する。コーナー表示中のライブ更新はしない。
- **失敗時**: 取得失敗・schema 不一致・stale のときは前回値を出さず当該コーナーを skip して
  通常スロットへ戻す（fail-closed。既存コーナーの失敗時挙動に合わせる）。
- **表示件数**: 上位 10 件（`limit`）。ページ3 は「◯/全カード種類数」も併記する（母数が分かるように）。
- **期間**: 既定は全期間（`p_from_date = NULL` の累積値）。将来 7日 / 30日 窓を追加できる形にする（§7-1）。

## 5. コーナー追加案（番組ローテーションへの接続）

- 新 adapter **`doushi`**（game id `doushi-view`）。`WeatherCornerAdapter` / `TsuitateCornerAdapter` /
  （予想王案の）`yoso` と同列の**合成 view**（program slot に乗る読み取り専用ビュー）。
  `corner_adapters.ADAPTERS` への追加は本PRでは行わない。
- ビューは TwiCa の新設 API を **read-only** で読み、4ページのランキング表を 960×540 へ描画する。
  既存ゲーム本体・配信 encoder・Soren・TwiCa 本体は変更しない。
- catalog 例（**既定無効・手動予約可**で提案）:

  ```toml
  # 同志カード大賞: 同志カードのランキング。既定は無効。opt-in と privacy 確認が前提。
  { id = "doushi-card", adapter = "doushi", game = "doushi-view", enabled = false, manual_only = true }
  ```

- 実行は他コーナーと同じ program slot 排他。表示時間は上限つき（§4）で自動終了する。
- 有効化・catalog への追加は **オーナー承認後**（本PR では行わない）。

## 6. TwiCa 側に必要な公開契約（提案）

docich が読むのは次の 1 本だけにする（案。TwiCa 側の設計は TwiCa で決める）。

```
GET /api/overlay/{streamerId}/card-ranking?period=all|7d|30d&limit=10
→ 200 {
    "generated_at": "<ISO8601>",
    "period": "all",
    "metric_labels": { ... },        # 表示用の固定文言（docich 側で文言を二重管理しない）
    "note": "N連はN件 / 配信者本人とBOTを除く / ...",
    "by_count":   [{"rank":1,"user_twitch_id":"...","username":"...","redemption_count":12}],
    "by_points":  [{"rank":1,"user_twitch_id":"...","username":"...","total_points":25000,"redemption_count":12}],
    "by_unique":  [{"rank":1,"user_twitch_id":"...","username":"...","unique_count":40,"total_cards":120}],
    "popular_cards": [{"rank":1,"card_id":"...","card_name":"...","actual_count":30,"drawer_count":18}]
  }
```

- 個人情報は Twitch の公開表示名と集計値のみ。token・IP・メール・内部 id 以外は返さない。
- 返すのは集計済みの上位 `limit` 件のみ（生の履歴行・`drawers[]` 全文は返さない）。
- 並び順は §3.1 を API 側で確定させる（クライアントにソートさせない）。
- 認証は既存 `/api/overlay/{streamerId}/events` と同じ「streamerId を知っているだけ」の公開水準とし、
  既存と同じレート制限・キャッシュ無効化に従う（設計は TwiCa 側で確定）。
- 既存の認証必須 API を service トークンで叩く案は、docich に資格情報を増やし、
  視聴者向け公開情報としては過剰権限になるため **採らない**（§7-8 で再確認）。

## 7. 未確定・オーナー判断が要る点

1. **集計期間**: 全期間（既定）か、直近 7日 / 30日 か。全期間は 2026-07-04 境界の影響を受ける。
2. **最低ライン**: 1回だけ引いた人を TOP10 に載せるか（`min_redemptions` を入れるか。既定は導入しない）。
3. **表示時間**: 1ページ 15 秒 × 4（=60 秒）と上限 2 分でよいか。
4. **「数」の見せ方**: ページ1・2 は TwiCa 正本の「排出カード枚数（N連はN件）」で確定とした。
   「引き換え回数」で見せたい場合は別定義が必要。
5. **人気カードの主指標**: 枚数順（既定）か人数順か。両方出すか。
6. **opt-out / 不適切名**: 表示名を配信画面へ出すための周知・非表示申請・不適切名の扱い（未確定）。
7. **対象チャンネル**: Soren の 1 チャンネルのみでよいか（複数にする場合の合算方針）。
8. **③ の除外適用**: `get_gacha_users_for_streamer` に除外判定が入っていない（§3 の既知の差異）。
   新設 API で「除外済みの蒐集数」に揃えるか、現行の素の集計を出すか。
   （推奨: ①②④ と揃えて除外を適用する）

## 8. MVP / 実装ステップ（PR 分割案）

1. **PR-1（本PR）**: 本ドキュメント（仕様・起案）のみ。コード変更なし。
2. **PR-2（azumag/twica 側）**: §6 の公開読み取り専用ランキング API。
   除外判定・対象述語・並び順は既存関数に合わせ、追加の集計は DB 内で行う。
3. **PR-3（docich 側）**: API クライアントのオフライン中核（HTTP は注入・ネットワーク無し）＋テスト。
   JSON 検証は fail-closed。URL は既存 `SOREN_DIRECT_TWICA_OVERLAY_URL` の origin から導出し、ログに出さない。
4. **PR-4**: `doushi` adapter ＋ view ＋ catalog 登録（**既定無効・manual_only**）。
5. **PR-5**: 読み上げ・実況文面、オーナー承認後の有効化と VM 反映。

## 9. 検証

本PRはドキュメントのみ。

```sh
git diff --stat origin/main..HEAD      # docs/features/doushi-card-corner.md の 1 ファイルのみ
```

- `docs/**` は `.github/workflows/ci.yml` の `paths:` に含まれないため CI は **0 チェック**になる。
  これは「成功」の証拠ではない（ドキュメントのみのため実行コードの検証対象が無い、というだけ）。
- §2 の table/関数・述語・並び順・除外条件は、上記 TwiCa `main` の
  migration と SQL 定義（`get_channel_point_usage_stats` / `get_gacha_users_for_streamer` /
  `get_gacha_drop_stats` / `is_redemption_ranking_excluded`）から引用した。docich の既存資産
  （`corner_adapters.ADAPTERS`、`twica_renderer.validate_url`、`comment_reply_policy_contract.md`）も main と照合済み。
- TwiCa・本番 VM・ゲームへの接続、および production catalog の変更は行っていない。
