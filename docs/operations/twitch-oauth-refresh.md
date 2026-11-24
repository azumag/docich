# Twitch 配信者トークンの自動更新

## なぜあるか

Soren の Twitch 機能（タイトル・カテゴリ更新、チャット送信、クリップ、投票、予想、
Creator Goals）は dociai の user token を `.env` から読む。以前は implicit grant で発行した
トークンを手で入れていたが、implicit grant には refresh token が無く約60日で必ず失効する。
2026-10-19 に `TWITCH_BOT_TOKEN` が失効し、ゲーム切替時のタイトル・カテゴリ更新が
2日半すべて失敗した（`run-soren-live/logs/stream-game.log` の
`no valid broadcaster credential with channel:manage:broadcast`）。

`docich.twitch_oauth` は Authorization Code Grant で一度だけ同意を取り、refresh token で
短命の access token（約4時間）を自動更新する。Confidential client の refresh token には
有効期限が無い。ただし dociai のパスワード変更、アプリ連携の解除で無効になる。

## 仕組み

- `docich-twitch-oauth-refresh.timer`（user unit、15分ごと）が
  `bin/docich-twitch-oauth --config config/docich.soren-live.toml refresh` を実行する。
- 残り1時間を切った、無効、または管理対象キーの値が揃っていない場合だけ更新し、
  Soren の `.env` の `TWITCH_BOT_TOKEN` / `TWITCH_PREDICTIONS_TOKEN` /
  `TWITCH_POLLS_TOKEN` / `TWITCH_CLIP_TOKEN` を同じ新トークンへ、
  `TWITCH_OAUTH_REFRESH_TOKEN` を新しい refresh token へ原子的に置き換える。
  他の行は変えない。
- 新トークンは validate で「dociai（`TWITCH_BROADCASTER_ID` と一致）」かつ必要 scope を
  全て持つことを確認してから書く。満たさなければ `.env` を変えない。
- `TWITCH_CLIENT_SECRET` か `TWITCH_OAUTH_REFRESH_TOKEN` が無い間は `not_configured` で何もしない。
- 状態は `run-soren-live/twitch_oauth/status.json`（秘密値なし: status/action/login/
  missing_scopes/expires_at）。`reauth_required` で unit は exit 3（failed）になる。
- Soren 側の利用箇所は呼び出しごとに `.env` を読み直す（soviet_now
  `docs/twitch_game_sync.md`）。起動時の値を保持し続ける処理を新しく作らない。

必要 scope: `channel:manage:broadcast chat:edit chat:read user:write:chat clips:edit
channel:manage:polls channel:manage:predictions channel:read:goals`。
広告スヌーズ（`channel:read:ads` / `channel:manage:ads`）は `TWITCH_ADS_ENABLED=0` のため含めていない。
有効化する場合は `REQUIRED_SCOPES` に加えて再認可する。

## 初回設定・再認可の手順

トークン・secret・認可コードは、チャット・Issue・ログ・リポジトリに貼らない。

1. （初回のみ・owner）Twitch Developer Console で `TWITCH_CLIENT_ID` のアプリを開き、
   OAuth Redirect URL に `http://localhost` があることを確認して client secret を発行し、
   VM の `/home/ubuntu/soren/.env` に `TWITCH_CLIENT_SECRET=...` を追記する。
2. VM で同意 URL を作る（state は15分有効）:
   ```
   cd /home/ubuntu/docich && ./bin/docich-twitch-oauth --config config/docich.soren-live.toml authorize-url
   ```
3. その URL を **dociai でログインしたブラウザ**で開き、画面上部が dociai であることを確認して「許可」する。
   `http://localhost/?code=...&state=...` に移って「接続できません」になるのが正常。アドレスバーの URL をコピーする。
4. 手元の Mac で、コピーした URL を VM の exchange に標準入力で渡す:
   ```
   pbpaste | ssh ubuntu@<VM> 'cd /home/ubuntu/docich && ./bin/docich-twitch-oauth --config config/docich.soren-live.toml exchange'
   ```
   出力は `{"action": "authorized", "login": "dociai", "missing_scopes": [], ...}` のような秘密値なしの JSON。
5. 確認: `./bin/docich-twitch-oauth --config config/docich.soren-live.toml status` と
   `systemctl --user list-timers docich-twitch-oauth-refresh.timer`。
   その後クリップボードを別の文字列で上書きし、認可したタブを閉じる。

`status.json` が `reauth_required` になったら手順2〜5をやり直す。
