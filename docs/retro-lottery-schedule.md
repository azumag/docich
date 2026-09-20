# レトロ枠: 毎時抽選モード（互換モード） (`[retro_corner] mode = "lottery"`)

> 現在の本番設定は、毎時の確率抽選ではなく [`retro-rolling-rotation.md`](retro-rolling-rotation.md)
> の rolling rotation を使用する。この文書は既存の `lottery` モードを維持・切り戻しするための仕様である。

固定枠 (各ゲームを日1回・19時台) をやめ、**毎時1回「発火するか」を確率で抽選**する。
当たれば遊べるゲームを無作為に選んで数試合遊び、元のゲームへ戻る。他のコーナーが進行中の
ときは抽選そのものを取りやめる。`mode = "daily"` (既定) は従来どおりで、設定を戻せばロールバックできる。

## 動き

1. コーナー用 timer が毎分 tick する (既存)。抽選は **毎時 `lottery_minute` 分 (既定 5) から
   10分の窓**に届いた最初の tick が1回だけ行う。tick を取りこぼしても同じ時のうちなら引く。
   窓を過ぎたらその時は抽選しない。
2. **他コーナーが進行中/待機中、またはゲーム切替中なら取りやめ** (待たない・引かない)。判定は
   `corner_boundary.other_corner_busy()`:
   - 全コーナー共通の program lock (`docich_program.lock`) を非ブロックで試す (実際に保持中なら busy)。
   - registry の所有者コーナーが `starting`/`active` (busy)。
   - 他コーナーが待機中 (`waiting`/`waiting_turn`/`waiting_boundary`)。先に待っているものを追い越さない。
     強制終了で残った古い待機は 3時間で無視する。自コーナーのエントリは無視する。
   - lock が試せない・registry が壊れている場合は fail-closed で busy。
3. busy でなければ `lottery_probability` で抽選する。**抽選結果は発火の前に state へ記録**するので、
   tick の重複・再起動があっても同じ時に引き直さない (`state["lottery"] = {slot, result, roll, reason, game}`)。
4. 当たり: `games` のうち「遊べる」ゲームから無作為に1つ選ぶ。
   - 遊べる = ゲーム設定が正しい CLI ゲームで、ゲーム側 `[retro_corner] requires` に書かれた実行ファイルが実在する。
   - 前回と同じゲームは、他に遊べるゲームがある限り選ばない (`lottery_avoid_repeat`)。
   - 遊べるゲームが無ければ取りやめ (`no-playable-game`)。
5. 発火: 既存の corner 機構 (transactional switch・program slot・開始アナウンス) を使う。
   `target_matches` 試合を scorelog で検知したら早期終了、来なければ `duration_minutes` を上限に終了
   (入力停止で試合を終わらせない)。終了時に元のゲームへ戻し、改善ジョブ (`improve_agents` 非空なら) を起動する。
   本番 (`require_program_boundary = true`) は program slot 経由で、予測の境界を最大
   `lottery_wait_minutes` (既定 10) だけ待つ。超えたら発火せず取りやめ (`wait-expired`)。

## 設定 (`[retro_corner]`)

| キー | 既定 | 意味 |
|---|---|---|
| `mode` | `"daily"` | `"lottery"` で毎時抽選 |
| `lottery_probability` | 0.3 | 1回の抽選が当たる確率 (0 < p ≤ 1)。期待回数 ≒ 24 × p 回/日 |
| `lottery_minute` | 5 | 抽選する分 (0–50) |
| `lottery_wait_minutes` | 10 | 予測境界を待つ上限 (0–30) |
| `lottery_avoid_repeat` | true | 直前と同じゲームを避ける |
| `duration_minutes` | — | 1回のコーナーの上限時間 |
| `target_matches` | 3 | この試合数で早期終了 |

**読込時の検証**: `lottery_minute + lottery_wait_minutes + duration_minutes ≤ 55`。抽選コーナーは最悪でも
次の正時の前に終わり、**毎正時に始まる固定枠のコーナー (soren91 18:00・PAPER 22:00 など) を塞がない**。
抽選を正時でなく :05 にしているのも、同じ分に起動する固定枠コーナーが先に枠を取れるようにするため。

切り戻し時の例: `lottery_probability = 0.3`、`lottery_minute = 5`、
`lottery_wait_minutes = 10`、`duration_minutes = 20`、`target_matches = 3`。確率は運用で調整する値
(0.3 なら約 7 回/日・1回 5〜20 分)。日次用のキー (`start_hour` など) は `mode = "daily"` へ戻すために残してある。

## 検証と限界

- `tests/test_retro_lottery.py` (27件): 設定検証、抽選窓、時ごとに1回・再起動耐性、当たり/はずれ、
  他コーナー busy 5種 (lock 保持・所有者・待機中・古い待機無視・切替中) の取りやめ、ゲーム選択
  (実行ファイル/種別/連続回避)、本番の program slot 経由と境界待ち。各ガードを外すと対応するテストが落ちる
  ことを変異で確認した (11/11)。日次モードの既存テストは無変更で通る。
- Linux + 実バイナリ + 実 flock (Docker) で本番設定のまま実行: 改善対応の5ゲームが「遊べる」と判定され、当たりは
  `bastet → sorengame` と切替えて戻り、別プロセスが program lock を保持中は `program-locked` で取りやめ、
  同じ時の再 tick は引き直さない。
- **未検証**: VM 上の実運用 (実際の PAPER/soren91 との共存、実プレイを含む1サイクル)。
- nsnake は死なないため 3試合検知が成立せず、当たると上限 (20分) まで遊ぶ。ただし終了後の
  改善は bounded headless 評価へ進み、固定手数時点のスコアを候補比較に使う。
- 改善対応の5ゲームは同じ改善経路 (`improve_agents` → baseline/candidate headless 評価 → margin gate)
  を使う。rolling rotationに追加されたNetHackは専用brainで動き、改善評価は未対応として明示的に抑止する。
  systemd の oneshot から起動する場合は独立 transient user service に分離される。
  1日あたりの LLM 呼び出しが気になる場合は `improve_agents` を空にする (改善ジョブは起動されなくなる)。
