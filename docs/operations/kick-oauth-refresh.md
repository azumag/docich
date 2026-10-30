# Kick 配信者トークンの自動更新

## なぜあるか

Soren の Kick タイトル同期（soviet_now `lib/stream_title_sync.py`）は
`KICK_ACCESS_TOKEN` と `KICK_BROADCASTER_USER_ID` を `.env` から読み、
配信中の自チャンネルへ `PATCH /public/v1/channels` で `stream_title` を書き込む。
Kick の user access token は短命（約1日）で、静的に貼ったトークンはすぐ失効して
`update_failed` に戻る。`docich.kick_oauth` は Authorization Code Grant + PKCE
（Kick は `code_challenge` 必須）で一度だけ同意を取り、refresh token で
access token を自動更新する。

## 仕組み

- `docich-kick-oauth-refresh.timer`（user unit、15分ごと）が
  `bin/docich-kick-oauth --config config/docich.soren-live.toml refresh` を実行する。
- 現在の access token を introspect し、残り1時間を切った、inactive、
  または `KICK_ACCESS_TOKEN` が無い場合だけ更新し、Soren の `.env` の
  `KICK_ACCESS_TOKEN` と `KICK_OAUTH_REFRESH_TOKEN` を原子的に置き換える。
  他の行は変えない。
- 新トークンは「user token（`token_type=user`）」「`channel:read` + `channel:write`
  を持つ」「認証主体自身のチャンネル（`GET /public/v1/channels`）の
  `broadcaster_user_id` が `KICK_BROADCASTER_USER_ID` と一致」することを確認してから
  書く。満たさなければ `.env` を変えない。
- `KICK_BROADCASTER_USER_ID` が未設定の場合は、初回の exchange / refresh で
  認証主体のチャンネルから学習して `.env` に保存する。
- `KICK_CLIENT_ID` / `KICK_CLIENT_SECRET` / `KICK_OAUTH_REFRESH_TOKEN` が
  揃っていない間は `not_configured` で何もしない。
- 状態は `run-soren-live/kick_oauth/status.json`（秘密値なし: status/action/
  broadcaster_user_id/missing_scopes/expires_at）。`reauth_required` で
  unit は exit 3（failed）になる。diagnostics には固定enumだけが投影される。
- Soren 側の利用箇所は呼び出しごとに `.env` を読み直す。起動時の値を保持し続ける
  処理を新しく作らない。

必要 scope: `channel:read channel:write`。Kick 側が refresh token を rotate する
場合は新しい refresh token が `.env` に書き戻される。

## 初回設定・再認可の手順

トークン・client secret・認可コードは、チャット・Issue・ログ・リポジトリに貼らない。

1. （初回のみ・owner）Kick の Developer 設定でアプリを登録し、redirect URI に
   `http://localhost` を設定して client id / client secret を取得する。
   VM の `/home/ubuntu/soren/.env` に `KICK_CLIENT_ID=...` と
   `KICK_CLIENT_SECRET=...` を追記する（`KICK_BROADCASTER_USER_ID` は後で自動学習される）。
2. VM で同意 URL を作る（state と PKCE verifier は15分有効）:
   ```
   cd /home/ubuntu/docich && ./bin/docich-kick-oauth --config config/docich.soren-live.toml authorize-url
   ```
3. その URL を **配信者アカウントでログインしたブラウザ**で開き、要求 scope が
   `channel:read` と `channel:write` だけであることを確認して「許可」する。
   `http://localhost/?code=...&state=...` に移って「接続できません」になるのが正常。
   アドレスバーの URL をコピーする。
4. 手元のマシンで、コピーした URL を VM の exchange に標準入力で渡す:
   ```
   pbpaste | ssh ubuntu@<VM> 'cd /home/ubuntu/docich && ./bin/docich-kick-oauth --config config/docich.soren-live.toml exchange'
   ```
   出力は `{"action": "authorized", "broadcaster_user_id": "...", "missing_scopes": [], ...}`
   のような秘密値なしの JSON。
5. 確認: `./bin/docich-kick-oauth --config config/docich.soren-live.toml status` と
   `systemctl --user list-timers docich-kick-oauth-refresh.timer`。
   その後クリップボードを別の文字列で上書きし、認可したタブを閉じる。

`status.json` が `reauth_required` になったら（refresh token 失効・scope 不足・
別アカウントのトークンなど）手順2〜5をやり直す。アカウントを間違えて認可した場合も
`new_token_not_broadcaster` として `.env` は変わらない。
