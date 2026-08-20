# CLAUDE.md

このリポジトリのエージェント向けルールは `AGENTS.md` に一本化している。作業を始める前に
`AGENTS.md` を読み、特に以下を必ず守ること:

1. 作業再開時はまず `handoff.md` を読む（実態と突き合わせてから着手）。
2. 一段落したら `handoff.md` を更新する。
3. 機密情報（ストリームキー・API キー・秘密鍵等）はどのファイルにも書かない。
4. `handoff.md` とメモリは適宜棚卸し（整理・要約・訂正）する。
5. VM への本番反映とリポジトリへのコミット・push は同時に行う。
6. `soviet_now` の `config.sh` 既定値変更は worker 完全再起動で反映する。
7. 作業中は `codex_work_indicator.sh start/stop`（または webui 作業中バナー）で粒度細かく進捗を報告する。

詳細・理由は [`AGENTS.md`](./AGENTS.md) を参照。
