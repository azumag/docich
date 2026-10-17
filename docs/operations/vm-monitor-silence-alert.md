# VM monitor の沈黙検知（独立アラート）

`scheduled` な `VM storage monitor` は、VM の storage / runtime health / tracked drift /
baseline のアラートを出す唯一の経路である。その run が成功しなくなると、**監視が止まった
こと自体を誰も報告しない**。monitor は自分の沈黙をアラートできないし、外部の Cloudflare
watchdog（`ops/vm_monitor_watchdog`）は GitHub へ到達できない・token が失効している場合に
fail-closed で静かに止まる（2026-10-15〜10-17 の実測では dispatch が 1 件も無く、最後の
アラートは #1867（10-06）だった）。

この検知は、そのどちらとも**別の失敗モード**を持つ経路として動く。

- 専用の workflow（`.github/workflows/vm-monitor-silence-alert.yml`）と専用の
  concurrency group。monitor の queue が詰まっても影響を受けない。
- watchdog の個人 token ではなく、run 単位の `GITHUB_TOKEN`（`issues: write`）を使う。
  watchdog の資格情報が失効しても盲目にならない。

## 動作

- 毎時 `:29`（monitor の `:17` / `:47` と別スロット）に、`vm-storage-monitor.yml` の
  **最新の成功 run** の時刻を読む。
- 成功 run が閾値（6 時間）以内なら何もしない。開いている沈黙アラートがあれば close する。
- 閾値を超えている、または成功 run が 1 件も無い場合は、固定タイトル
  `[VM monitor] silence (no successful run)` の Issue を**1 件だけ**開き、既に開いていれば
  本文を現在の経過時間で更新する（重複させない）。
- 公開するのは固定 field のみ: 最終成功時刻、経過時間、monitor workflow の URL、
  検知 run の URL。path・credential・log・API body は出さない。

`ops/vm_actions/monitor_silence.py` が判定と重複排除を持つ。

## 失敗時の挙動

- 成功 run の照会・Issue 一覧の取得に失敗した場合は **exit 2 で終了し、アラート状態を一切
  変更しない**。判断できないときに既存アラートを閉じて沈黙を隠さないため。
- アラートの作成・更新・close に失敗した場合は exit 3。状態は次回の実行で収束させる。

## 限界

- GitHub の `schedule` 自体が配信されない場合は、この検知も動かない。**monitor の復旧手段
  ではなく「気づき」の経路**であり、watchdog の Cron / token / dispatch の点検（owner 作業）
  は引き続き必要。
- 検知の遅延は最大で「閾値 6 時間 + 次回スロット」程度。

## 検証

```console
python3 -m unittest ops/vm_actions.tests.test_monitor_silence
```

owner は `workflow_dispatch` で即時実行して、沈黙時に Issue が 1 件開き、成功 run が
閾値内に戻ると close されることを確認できる（手動実行は owner に限定）。
