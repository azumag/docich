# RuntimeBackend: worker/status 縦切り (issue #43)

`src/docich/webui.py` は Soren (soviet_now) 固有の pid ファイルパス・worker 名・
game_state.json レイアウトと、UI/HTTP route の配線を同居させて巨大化していた
(監査時点で約6,800行)。本ドキュメントは、その最初の縦切りとして **read-only な
worker/status 取得だけ** を `RuntimeBackend` capability interface へ抽出した際の
contract snapshot と Soren dependency inventory を記録する。

対象外 (本PRでは触らない): command route (`POST /api/workers` 等の書き込み系) と
frontend (SPA JS) の分割。

## 1. スコープ: 「status」の線引き

webui.py には状態を返す GET エンドポイントが複数ある
(`/api/health`, `/api/config`, `/api/game_state`, `/api/improve_state`,
`/api/workers`, `/api/stream`, `/api/predictions`, `/api/peak_status` 等)。
本PRで RuntimeBackend に抽出したのは次の 2 つのみ:

| capability | route | 理由 |
|---|---|---|
| `list_workers` | `GET /api/workers` | issue #43 が名指しした対象 route。worker 名・pid ファイル path が完全に Soren 固有 |
| `get_status` | `GET /api/game_state` | soviet_now の `game_state.json` (盤面/得点) を読むだけの薄い route で、worker と対になる「今の runtime 状態」の代表として選んだ |

**含めなかったもの** (out of scope, 理由付き):

- `GET /api/health` — docich webui 自体の生存確認 (soren_root・uptime・bind/port)。
  Soren 固有の worker/game 状態ではなく webui プロセス自身の meta 情報なので対象外。
- `GET /api/improve_state` — improve daemon 専用の状態 (pid・lock・進捗)。
  `/api/workers` の `improve_daemon` 行と重複する概念だが、詳細フィールド
  (`is_locked` 等) が improve 機能固有で、抽出すると capability が肥大化するため
  今回は見送り。将来 improve 機能を切り出す縦切りで別途扱う想定。
- `GET /api/predictions`, `GET /api/peak_status`, `GET /api/stream` —
  それぞれ Twitch predictions・ピーク帯・配信という個別機能の状態で、
  「worker 一覧」「ゲーム状態」より一段具体的な機能固有 API のため対象外。

## 2. Soren dependency inventory (抽出前の webui.py 直書きロジック)

`_handle_get_workers` (旧: webui.py 5708行付近、実測ではリファクタ前で1786行付近)
と `_handle_get_game_state` が直接依存していた Soren 固有の path/名前:

- worker 名リスト (`_get_workers_status` 内に直書き):
  `radio_worker`, `chat_worker`, `improve_daemon`, `audio_worker`,
  `prediction_worker`, `youtube_worker`
- pid ファイル path 規約: `<soren_root>/tmp/state/<worker>.pid`
  (`_find_worker_pid`)。未知の追加 worker も `tmp/state/*.pid` を走査して
  拾う (`_get_workers_status` 後半)。
- pause マーカー path 規約: `<soren_root>/tmp/state/<worker>.paused`
  (`_worker_pause_marker_path`)、対象は `TOGGLEABLE_WORKERS` =
  `("direct_stream", "chat_worker", "prediction_worker", "improve_daemon")`。
- 生死判定: `os.kill(pid, 0)` + `/proc/<pid>/stat` (無ければ `ps -o stat=`)
  によるゾンビ判定 (`_process_is_zombie` / `_pid_is_active`)。
- worker 誤殺防止ガード: `/proc/<pid>/cmdline` を `start_all.sh` の
  `_pattern_for_worker` と同じ正規表現で照合 (`_pid_matches_worker_process`)。
  こちらは書き込み系 (`POST /api/workers`) 専用のため webui.py に残置。
- game 状態ファイル: `<soren_root>/game_state.json`
  (`_game_state_path`)。中身は Soren 側スクリプトが書く `{"state", "score", ...}`。
- 「soviet_now が配置されているか」の目印: `<soren_root>/eloop_lib.sh` の存在
  (`run_webui` の起動時警告で既に使われていた基準をそのまま流用)。

`src/docich/procs.py` (汎用 subprocess 実行ヘルパ) と `src/docich/supervise.py`
(`docich run` の再起動ループ) は **調査の結果、webui.py の worker/status ロジック
から一切参照されていない** (webui.py はこれらを import すらしていない)。
webui.py の worker 生死判定は上記の通り pid ファイル + `os.kill`/`/proc` を
直接読む自前実装であり、`procs.py`/`supervise.py` の subprocess 実行系とは
独立している。よって本縦切りでは両モジュールへの変更は不要だった。

## 3. RuntimeBackend interface

新規モジュール `src/docich/runtime_backend.py`:

```python
class RuntimeBackend(abc.ABC):
    def capabilities(self) -> dict[str, bool]: ...   # {"list_workers": bool, "get_status": bool}
    def list_workers(self) -> list[dict[str, Any]]: ...
    def get_status(self) -> dict[str, Any]: ...
```

- `CAPABILITY_LIST_WORKERS = "list_workers"`, `CAPABILITY_GET_STATUS = "get_status"`
  (`ALL_CAPABILITIES` はこの2つ)。
- `CapabilityUnsupportedError`: capability 未対応なのに呼んだ場合に送出する
  実装ミス検出用の安全網 (route 側は本来 `capabilities()` を先に見て分岐する)。
- `DocichBackend`: soviet_now に一切依存しない mock 実装。コンストラクタに
  渡した固定データをそのまま返すだけで、実プロセス/実ファイルに触れない。
  interface が Soren 専用でないことを示す + 単体テスト用。
- `SorenBackend(soren_root)`: 上記 §2 の Soren 固有ロジックを移設した adapter。
  `soren_deployed(soren_root)` (= `soren_root` が dir かつ `eloop_lib.sh` あり)
  が False の間、`capabilities()` は両方 False を返し、`list_workers()` /
  `get_status()` を呼ぶと `CapabilityUnsupportedError` を送出する。

`webui.py` 側は `_Handler._runtime_backend()` が `SorenBackend(self.soren_root)`
を返し、`_handle_get_workers` / `_handle_get_game_state` は
`backend.capabilities()` を見てから `list_workers()`/`get_status()` を呼ぶだけ
になった。worker 名・pid ファイル path の文字列リテラルは対象 route から消えた
(§5 の grep 結果を参照)。

## 4. HTTP contract (変更しないこと)

### `GET /api/workers` (soviet_now 配置済み)

```json
{"workers": [{"worker": "radio_worker", "pid": 123, "alive": true, "paused": false, "status": "ok"}, ...], "now": 1710000000}
```

### `GET /api/workers` (soviet_now 未配置: 新規)

```json
{"workers": [], "now": 1710000000, "unsupported": true, "capability": "list_workers", "reason": "soviet_now is not deployed at this soren_root"}
```

### `GET /api/game_state` (soviet_now 配置済み)

```json
{"exists": true, "path": "/.../game_state.json", "mtime": 1710000000, "data": {...}, "state": "revolution", "score": 3}
```

### `GET /api/game_state` (soviet_now 未配置: 新規)

```json
{"exists": false, "path": null, "mtime": 0, "data": null, "state": "", "score": null, "unsupported": true, "capability": "get_status", "reason": "soviet_now is not deployed at this soren_root"}
```

配置済みの場合の JSON 形状 (キー集合・値の意味) はリファクタ前と同一。
`tests/test_webui.py` の `test_workers_snapshot_matches_legacy_read_path` /
`test_game_state_snapshot_matches_legacy_read_path` が、同一 fixture に対する
新経路 (RuntimeBackend) と旧経路 (下記 §5 の legacy flag) の出力が
`assertEqual` で完全一致することを検証する。

## 5. ロールバック flag

環境変数 `DOCICH_WEBUI_LEGACY_RUNTIME_READS` を `1`/`true`/`yes`/`on` にすると、
`_handle_get_workers` / `_handle_get_game_state` は capability 判定を挟まず
リファクタ前と同一の関数呼び出し (`_get_workers_status` / `_read_game_status`
を無条件に呼ぶ) に戻る。soviet_now 未配置でも `unsupported` を返さず、
リファクタ前と同じ「pid ファイルが無いので not_running/exists=false」という
表示になる。API contract (JSON の形自体) は変更しない。
