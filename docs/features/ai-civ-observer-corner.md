# AI文明観察日記 — MoYoGarden定点観察（企画・bounded入力契約段階）

## この作業の範囲

本カードは Issue #1705 の **企画・入力契約整理** に限る。
runtime実装、LLM日記生成、overlay表示、corner開始/終了、配備/有効化は未実施であり、後続カードで分離して進める。

- 実装開始/全体完了としては扱わない。
- 既存completed / 明示保留は維持。
- 観測経路は MoYoGarden に対して **read-only / passive**。simulation activity を一切発生させない。
- 実世界書き込み、simulation起動、配備/有効化を行わない。

対象リポジトリ HEAD at planning: `azumag/docich` `origin/main` `a4eeecfc45131a437bbd67c812d5129ab6f6d1dc`。
参照した MoYoGarden 上流 HEAD: `azumag/MoYoGarden` `main` `4a4481764b458bd2e0f146954fa54420fc937370`。

## ねらい

MoYoGarden の AI エージェント社会を定点観測し、前回との差分から重要な変化を数件選び、
「AI文明の観測番組」として紹介する継続コーナーの **bounded な入力契約** を固定する。

## 観測ソースの安全境界

- 許可する通信は MoYoGarden の **読み取り GET のみ**。
- 許可するエンドポイント:
  - `GET /api/meta`
  - `GET /api/health`
  - `GET /api/world/snapshot`
  - `GET /api/events?afterTick=<lastTick>&limit=<n>`
- 将来拡張（MVP では未対象だが read-only / passive を維持する場合のみ追加可）:
  - `GET /api/world/halo`（近隣 region の人口・占有を read-only 取得。mutation しない）
- 禁止:
  - `POST /api/admin/*` を含む全 mutation
  - `/api/agents/:id/commands`
  - WebSocket による tick 購読（MVP では polling GET のみ; 接続を持続しない）
  - 観測を契機とした simulation tick / Alarm 起動
- 観測ターゲットは **1 region / 1 observation** を上限とし、複数 region の fans-out は将来拡張。
- region は `AI_CIV_REGION_ID`（未設定時は `/api/meta` の `defaultRegion`）を使用。
- Base URL は設定値のみ使用。defaults で本番 URL を埋め込まない。

## 入力契約（bounded）

### 取得対象

1. `/api/meta`: 利用可能領域・既定領域・version の確認のみ。
2. `/api/health`: tick / paused / tickMode / catch-up 観測（`virtualTicksDue` / `simulationLagMs` / `lastSimulatedAt`）の観測。mutation しない。`alarm` フィールドは上流に存在しない。
3. `/api/world/snapshot`: 完全 WorldState。観測・差分計算に使う。
4. `/api/events?afterTick=<lastObservedTick>&limit=<cap>`: 前回観測以降の公開イベント。

### 上限

- `OBSERVATION_MAX_BYTES = 262144`（1 応答 256 KiB、weather 側 precedent に合わせる）
- `OBSERVATION_MAX_EVENTS_PER_PAGE = 200`（上流 contract は既定 100 / 上限 200: `src/worker.ts` の `parseBoundedInteger(limit, 100, 1, 200)`。MVP は上流上限 200 を使う）
- `OBSERVATION_MAX_EVENTS_PER_RUN = 1000`（1 回の観測で fold する公開イベントの総上限）
- `OBSERVATION_MAX_REGIONS = 1`（MVP）
- `OBSERVATION_HTTP_TIMEOUT_S = 4`（socket timeout、weather precedent; wall-clock 厳密上限ではないことを明記）
- `OBSERVATION_TOTAL_FETCH_BUDGET_S = 45`（全 GET の合計上限）
- `OBSERVATION_SNAPSHOT_STALE_AFTER_S = 900` 以降の snapshot は再取得が必要
- redirect は拒否、固定 host/region allowlist を使う。

### 入力の正規化（MVP が依存する上流フィールド）

MoYoGarden の `WorldState`（`src/protocol.ts:202`）には living/births/deaths 等の人口カウンタは **存在しない**。
WorldState から抽出するのは以下のみで、それ以外は保存しない。

- `tick`: int, `>= 0`（上流 `WorldState.tick`）
- `regionId`: bounded string（上流 `WorldState.regionId`）
- `population.living`: **観測側で導出**。`agents.filter(a => a.hp > 0).length`。上流 precedent: `src/settlement-migration.ts:646` が同式を使用。
- `births`: **観測側で導出**。前回観測との agent-id 集合差分で、今回に新規出現した agent id の数（migration による流入も含まれる window 増分）。上流に累積カウンタはないため「観測窓ごとの増分」であり、前回 stored 値との累積解釈はしない。
- `deaths`: **観測側で導出**。前回観測の agent-id 集合に含まれ今回欠落、または `hp <= 0` になった agent の数（window 増分）。
- `maxGeneration` / `pairCount` / `familyCount`: **上流に存在しない**（`Agent` に generation / pair / family フィールドはない）。MVP 対象外とし、将来派生規則（lineage depth / reproductiveRole / pregnancy などから定義）を追加する場合のみ導入する。
- `regionPopulation`: MVP では `{<observed regionId>: living}` の **単一 entry のみ**。複数 region 横断マップは上流 snapshot 1 回からは取得できない。将来 `/api/world/halo`（`occupants` / `occupantsByFaction`）を read-only bounded 対象に追加した上で、`OBSERVATION_MAX_REGIONS` と上限を再定義して拡張する。
- `events`: **上流 `WorldState.events`（`WorldEvent[]`）を正規化**。上流の実フィールドは `id` / `tick` / `kind` / `message` / `agentId?` / `factionId?` / `position?` / `data?`。ローカル storage にも上流名をそのまま使う（`type`/`summary`/`actorId`/`location` という別名は使わない）。`recentEvents` は WorldState のフィールドではなく、`/api/agents/:id/perception` 応答のフィールドであるため MVP 入力には使わない（perception 全 agent fan-out は bounded 範囲外）。
- `EventKind`（`src/protocol.ts:174-189`）に **birth/death 種別は存在しない**。出生・死亡トピックは agent 集合差分から導出する旨を明記する（kind による検出を実装しない）。

取得に失敗または validation 失敗した observation は **部分公開しない**。前回公開 snapshot は再表示しない（weather と同様の fail-closed）。

## 前回差分の保存契約

保存先: `<state_dir>/ai_civ_observer_last.json`（atomically write）。
1 region につき 1 本。schema:

```json
{
  "schemaVersion": 1,
  "observedAt": "<ISO8601 JST>",
  "sourceBaseUrl": "<configured base URL>",
  "regionId": "<region>",
  "tick": 123,
  "metrics": {
    "populationLiving": 100,
    "births": 4,
    "deaths": 1,
    "regionPopulation": {"garden-1": 100}
  },
  "livingAgentIds": ["agent-..."],
  "topicSeeds": [
    {"eventId": "evt-...", "kind": "agent_conversation", "title": "食料不足を起点に西部から大規模な移住", "firstSeenTick": 123}
  ],
  "seenEventIds": ["evt-..."]
}
```

- 前回 diff は `observedAt` / `tick` / `metrics` / `livingAgentIds` / `seenEventIds` を使う。`births` / `deaths` はこの保存レコードの `livingAgentIds` と今回 snapshot の `agents[]` から観測側で導出する（前回 id 集合を持てない場合は `births`/`deaths` を計算できない旨を明記して MVP 対象外とする）。LLM 文言は保存しない。
- 保存契約では `livingAgentIds`（前回観測時の living agent id 集合）を保持する。上限 `OBSERVER_MAX_TRACKED_AGENT_IDS`（MVP では 10000）を超えたら最古から削除（FIFO）。これにより `births = |currentAlive \\ previousLivingAgentIds|`、`deaths = |previousLivingAgentIds \\ currentAlive|` を same run で再計算できる。
- 保存失敗時は observation を `draft` にとどめ、公開しない。
- 同一 tick の再観測でも `observedAt` を更新しない（idempotent）。差分が 0 なら「変化なし」として topic 0 件で終端。

## 重複イベント識別契約

- イベント識別子は `(regionId, tick, kind, agentId?, position?)`（上流 `WorldEvent` のフィールド名に合わせる）。上流が `id` を返す場合はそれを優先。
- `seenEventIds` に含まれるイベントは「紹介済み」とし、再度 topic に選出しない。
- `seenEventIds` の上限 `OBSERVER_SEEN_EVENT_CAP = 500`。上限超過時は最古から削除（FIFO）。
- topic 選定は importance score 降順、同点は tick 昇順 → eventId 昇順で deterministic。
- topic は `OBSERVER_TOPIC_COUNT = 3` を上限とし、最低 0 件（変化なし）を許す。
- topic 化したイベントは same run で `seenEventIds` に追加。

## 非本番受入条件（固定 fixture）

固定 fixture を `tests/fixtures/ai_civilization_observer/observation_v1.json` に置き、
fixture の `snapshot` は上流 `WorldState` の **bounded サンプル（raw upstream layer）** であり、
`population` / `regionPopulation` など観測側で導出する値は含めない。
観測側の正規化済み記録は `previous`（保存スキーマ doc:89-94）が担う層であることを
`source` / `scenario` と合わせて識別できるようにする。
オフラインの schema / bounds テスト（`tests/test_ai_civilization_observer_contract.py`）で
以下を検証する（CI/本番接続なし）。

1. schema validation: 必須キー・型・上限が満たされる。
2. 入力 bound: `events` 配列長 <= 1000、snapshot の JSON bytes <= 256 KiB。
3. 重複排除契約: fixture の event id が unique（same run での topic 再選出を防ぐ前提）。
4. MoYoGarden activity: administer tick counter は変化しない（fixture で N/A、実測は後続）。

read-only 性（HTTP mutation が発生しない）は、このテストファイルおよび fixture 経路に HTTP 呼び出し/
POST が存在しないことをコードレビューで確認する（静的検査テストは任意）。

fixture には個人を特定しうる raw BOT データを含めない。集計値と合成 id のみ。
`births` / `deaths` は window 増分として扱うため、fixture の living/births/deaths が feeding 相当に見えても
reconciliation を要求しない（migration により living 変化と births/deaths が分離し得る）。

## 後続カード（今回実施しない）

- LLM による短い観察日記/解説生成
- Twica / overlay 最低限表示
- corner 開始/終了と GameSwitch 復帰
- 公開イベント sorting / topic text generation
- 週次総集編・系譜・地図等の将来拡張

これらは follow-up tasks として分離する。

## 検証記録（計画段階）

- 対象 repo clone は kanban scratch に作成。
- 分岐元 `origin/main` (SHA `a4eeecfc...`)。`git log --oneline origin/main..HEAD` は空。
- `gh issue view 1705` で本文・comments=0 を確認。
- MoYoGarden 上流 `src/protocol.ts` / `src/worker.ts` / `src/halo-region.ts` を読み、入力契約のフィールド名・上限を照合した。
- schema/bounds のオフラインテストを `tests/test_ai_civilization_observer_contract.py` に追加し、`pytest` で green を確認。
- 本番接続・deploy・activation は未実施。