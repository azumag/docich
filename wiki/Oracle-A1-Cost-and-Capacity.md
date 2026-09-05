# OCI Ampere A1: 4 OCPU / 24 GB の料金と 2 OCPU / 12 GB の運用可否

> 最終確認: 2026-08-31  
> 対象: Soren 本番の Oracle Cloud Infrastructure (OCI) `VM.Standard.A1.Flex`  
> 前提: 4 OCPU / 24 GB、Ubuntu 24.04 ARM64、1280×720・約30 fps、FFmpeg 直接配信

## 結論

- **無料トライアルのまま 4 OCPU / 24 GB を18日間動かして請求がないのは正常**。18日を24時間連続としても、A1 の使用量は 1,728 OCPU時間 / 10,368 GB時間で、Oracle がトライアル・有料テナンシを含む各テナンシに示している月3,000 OCPU時間 / 18,000 GB時間の無償量に収まる。
- **無料トライアル終了後も free-only のままなら、A1 の構成上限はテナンシ合計 2 OCPU / 12 GB**。4/24 のままでは既存A1が無効化され、PAYGへ移行しなければ30日後に削除対象になる。
- **PAYGへの移行は「ただちに4/24のCompute料金が発生する」という意味ではない**。他のA1利用がなければ、4/24を31日間連続稼働しても 2,976 OCPU時間 / 17,856 GB時間で月間無償量内に収まり、A1 Compute料金は **$0見込み**。
- **現行品質のまま2/12へ縮小するのは推奨しない**。既存ベンチマークでは4/24が720p30を満たし、2/12はゲーム描画とdrop/duplicate条件を満たしていない。さらに、4/24でホスト全体のCPU使用率が常時80%なら約3.2 OCPU分を使っており、2 OCPUでは同じ処理量を維持できない。

したがって、現時点の推奨は **トライアル終了前にPAYGへアップグレードし、4 OCPU / 24 GBを維持する**。同時に予算アラートを設定し、他のA1インスタンスや有料リソースを増やさない運用にする。

## 「無料アカウントの上限」と「月間無償使用量」は別物

この件で混乱しやすいのは、Oracleの資料に次の2種類の制限が出てくるためである。

| 種類 | 内容 | 4/24への影響 |
|---|---|---|
| free-only アカウントの**プロビジョニング上限** | A1全体で2 OCPU / 12 GB | トライアル終了後、PAYGにしない場合は4/24を保持できない |
| A1 Computeの**月間無償使用量** | 各テナンシで最初の3,000 OCPU時間 / 18,000 GB時間 | PAYGで4/24を保持しても、単独運用なら通常は料金0ドルに収まる |

つまり、**4/24を保持するためにPAYG化は必要だが、4/24そのものが必ず有料になるわけではない**。

OracleのArmドキュメントは、有料アカウントとトライアル・アカウントを含むすべてのテナンシについて、A1の最初の3,000 OCPU時間 / 18,000 GB時間を無料と説明している。一方、Free Tierドキュメントは、トライアル終了後に無料専用アカウントで保持できるA1を合計2 OCPU / 12 GBまでとしている。

## 現在の18日間稼働をどう解釈するか

2026-08-31時点で4/24を18日間、24時間連続稼働したとすると次の使用量になる。

```text
OCPU:   4 × 24時間 × 18日 = 1,728 OCPU時間
Memory: 24 GB × 24時間 × 18日 = 10,368 GB時間
```

これは月3,000 OCPU時間 / 18,000 GB時間以内である。したがって、現時点でCompute料金が発生していないことはOracleの公開条件と整合する。

ただし、**インスタンスの稼働開始日と無料トライアルの開始日は同じとは限らない**。トライアル終了日は「18日動かしたから残り12日」と推定せず、OCIコンソールに表示されるプロモーション終了日を確認すること。無料トライアルは、原則としてサインアップから30日またはクレジットを使い切った時点の早い方で終了する。

また、無料トライアル中は、PAYGへアップグレードしない限り登録カードへ自動請求されない。現在の無請求実績は、**free-only状態で4/24を永続利用できる証拠ではない**点に注意する。

## 4 OCPU / 24 GBを1か月動かした場合

A1の月間無償量と比較すると次の通り。

| 稼働日数 | OCPU使用量 | メモリ使用量 | 無償枠の残り | A1 Compute料金見込み |
|---:|---:|---:|---:|---:|
| 28日 | 2,688 OCPU時間 | 16,128 GB時間 | 312 OCPU時間 / 1,872 GB時間 | $0 |
| 30日 | 2,880 OCPU時間 | 17,280 GB時間 | 120 OCPU時間 / 720 GB時間 | $0 |
| 31日 | 2,976 OCPU時間 | 17,856 GB時間 | 24 OCPU時間 / 144 GB時間 | $0 |

31日月では余裕が小さい。無料量はA1の仮想マシン、ベアメタル、Container Instancesで共有されるため、別のA1を追加起動すると超過しやすい。

無償量を超えた部分の公開単価は次の通り。

| リソース | 単価 |
|---|---:|
| A1 OCPU | $0.01 / OCPU時間 |
| A1メモリ | $0.0015 / GB時間 |

「無償量が一切なかった」と仮定した4/24のリスト価格は30日で `$54.72`、31日で `$56.54` だが、通常のPAYGテナンシでは先に月間無償量が適用されるため、単独の4/24常時稼働はこの全額請求にはならない。

## Compute以外で料金が出る条件

A1 Computeが0ドルでも、テナンシ全体が必ず0ドルとは限らない。少なくとも次を確認する。

- A1を追加作成して、合計3,000 OCPU時間 / 18,000 GB時間を超えていないか
- Boot VolumeとBlock Volumeの合計がAlways Freeの200 GB以内か
- Volumeがhome region外に作られていないか
- 月間アウトバウンド通信が10 TBを超えていないか
- GPU、x86 Compute、追加ストレージなど別の有料リソースを作成していないか

現行の映像4,500 kbps + 音声160 kbpsを24時間送信する場合、単純計算の送出量は月約1.5 TBであり、他に大きな転送がなければ10 TB以内に収まる。

PAYG化後は、Billing & Cost Managementで小額の予算とアラートを設定し、Cost AnalysisでCompute、Block Volume、Networkの実績を確認する。予算アラートは支出検知用として扱い、無料枠内に収める構成管理そのものの代わりにはしない。

## 2 OCPU / 12 GBで現在の配信を維持できるか

### 既存実測

`docs/oracle_arm_setup_guide.md` に残っている比較では、Sorenの短時間ベンチマークで次の結果になっている。

- 4 OCPU / 24 GB: 720p30の条件を満たした
- 2 OCPU / 12 GB: ゲーム描画とdrop/duplicate条件を満たさなかった

現在はOBSではなくFFmpeg直接配信へ移行済みで、1280×720・約30 fpsが本番構成になっている。したがって、「OBSを外せば2/12でも余裕が出る」という最適化はすでに実施済みである。

### CPU 80%の意味

CPU使用率80%が**4 OCPU全体の平均**なら、使用量は概算で次の通り。

```text
4 OCPU × 80% = 約3.2 OCPU相当
```

これを2 OCPUへ縮小すると、必要処理量は容量の約160%になる。

```text
3.2 OCPU相当 ÷ 2 OCPU = 160%
```

同じ負荷のままではCPUが100%に張り付き、次のいずれかが悪化する可能性が高い。

- Chrome / Unity WebGLの描画fps
- FFmpegの送出速度、drop、duplicate frame
- VOICEVOXの合成待ち時間
- 改善処理や補助workerの応答時間
- 音声と映像の同期、再接続時の復旧余力

一方、`top`の**特定プロセス1個が80%**という意味なら、そのプロセスは約0.8コア分であり、ホスト全体80%とは異なる。縮小判断前に全体値を確認する。

```bash
sudo apt update
sudo apt install -y sysstat

nproc
mpstat -P ALL 1
pidstat -u -r 1
free -h
ps -eo pid,ppid,comm,%cpu,%mem,rss --sort=-%cpu | head -25
```

### 2/12の位置付け

2/12でもプロセスを起動すること自体は可能だが、**現行の720p30・ゲーム・TTS・改善処理を同時に維持する本番構成としては不合格の可能性が高い**。

どうしても無料専用アカウントへ戻す場合は、2/12へ縮小するだけでなく、次のような品質変更を前提に再設計する必要がある。

- 出力を720p30から480pまたは20〜24 fpsへ下げる
- VOICEVOXを外部サービスまたは別ホストへ移す
- 改善処理と音声合成を同時実行しない
- ゲーム側の描画負荷を下げる
- 30〜60分のカナリア運転でFFmpeg `speed >= 0.98`、平均送出fps、drop/dup増加、音声遅延を確認する

## 推奨運用

1. OCIコンソールで無料トライアルの正確な終了日を確認する。
2. 終了前にPAYGへアップグレードする。
3. A1は現在の4 OCPU / 24 GB 1台を維持し、追加のA1を常時起動しない。
4. Billing & Cost Managementで予算アラートを設定する。
5. PAYG化後と月替わり後にCost Analysisを確認し、A1 Computeが0ドルで処理されていることを実績で確認する。
6. Boot Volume / Block Volume合計を200 GB以内、アウトバウンドを10 TB以内に保つ。

## 関連する一次情報・内部資料

### Oracle公式

- [OCI Ampere A1 Compute for Free](https://docs.oracle.com/ja-jp/iaas/Content/Compute/References/arm.htm)
- [Oracle Cloud Infrastructure Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [Always Freeリソース](https://docs.oracle.com/ja-jp/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Oracle Cloud価格表](https://www.oracle.com/jp/cloud/price-list/)
- [Ampere A1 Compute](https://www.oracle.com/jp/cloud/compute/arm/)

### docich / soviet_now

- [Oracle Cloud ARMセットアップガイド](https://github.com/azumag/docich/blob/main/docs/oracle_arm_setup_guide.md)
- [Soren Linux / FFmpeg direct-stream status](https://github.com/azumag/docich/blob/main/docs/soren_linux_migration_plan.md)
- [soviet_now Issue #96: Oracle A1で720p30を安定化](https://github.com/azumag/soviet_now/issues/96)
