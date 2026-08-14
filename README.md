# VIXショート運用ダッシュボード

## スマホで見る(公開URL)
https://shihooow.github.io/vix-dashboard/

毎朝の自動更新のたびに、GitHubリポジトリ [Shihooow/vix-dashboard](https://github.com/Shihooow/vix-dashboard)(Public)へ `index.html`/`dashboard.html`/`log.csv` を自動pushし、GitHub Pagesで公開している。`取引判断ログ.csv`(個人の判断記録)は絶対に公開リポジトリへコピーしない設計。

pushに使うfine-grainedトークン(`vix-dashboard`リポジトリのContents:Read and writeのみに限定、有効期限2027-08-08)は `.github_token` にこのフォルダ内で平文保存している。漏洩リスクを避けたい場合は https://github.com/settings/personal-access-tokens で失効できる(その場合は自動公開が止まるので、手動pushに切り替えるか新しいトークンを発行して差し替える)。

## 保存場所
`~/Claude/vix-dashboard/`(以前は `~/Documents/資産形成プロジェクト` にありましたが、
フォルダ名の日本語が原因で実際には保存できていなかったため、ここに移動しました)

## ファイル構成
- `dashboard.html` — 毎朝自動更新される最新状況(ブラウザで開くだけ。オフラインOK)
  - 1週間/1ヶ月/3ヶ月/6ヶ月/1年の期間タブでVIX・SKEWの推移グラフを切り替え可能(2025-08-08〜現在の実データ収録済み)
  - VIXに15/20/25、SKEWに140/144の目安ラインを表示
  - 米国の経済指標カレンダー(FOMC・CPI・雇用統計・PCE・GDP・PPI)を「次のイベントまで」バナー+一覧表で表示
  - **指標予想・相場解説セクション(週次更新)** — FF金利の現状、次回FOMC/CPI/雇用統計の市場予想、株式・VIXの見通し解説を表示
- `log.csv` — VIX/VIX3M/SKEWの日次記録(Excelで開けます)
- `outlook.json` — 指標予想・相場解説の元データ(スケジュールタスクがWebSearchで調べて上書き。`changes_since_last`に前回からの変更点も記録)
- `outlook_history.jsonl` — outlook.jsonの過去バージョンを1行1件で自動保存(上書き前に自動退避、監査・見返し用)
- `取引判断ログ.csv` — 仕込み・決済などの判断を手動で記録する用。都度この行を追記してください。
- `scripts/update_dashboard.py` — ログ追記+ダッシュボード再生成を行うスクリプト(毎朝スケジュールタスクから自動実行)
  - `python3 update_dashboard.py <VIX> <VIX3M> <SKEW> [日付]` — 通常の日次数値更新
  - `python3 update_dashboard.py --outlook outlook.json` — 見通しセクションのみ更新(outlook.jsonを読んで再生成)

## 判定ロジック
- コンタンゴ/バックワーデーション: VIX3M ÷ VIX が1超なら「コンタンゴ(順鞘)」、1未満なら「バックワーデーション(逆鞘・警戒)」
- SKEWアラート: 140以上「注意」、144以上「警戒」
- 価格調整日: 毎月第2水曜。当日までの残り日数をカウントダウン表示

## 経済指標カレンダーについて(要メンテナンス)
`scripts/update_dashboard.py` 内の `ECON_EVENTS` に2026年12月までの日程を手入力しています。
2027年分のスケジュールが発表され次第、リストへの追記が必要です。

## 自動更新
- 毎朝(平日)Cboeの delayed_quotes から VIX・VIX3M・SKEW を取得し、`scripts/update_dashboard.py` に渡してログ追記・ダッシュボード再生成を行うスケジュールタスクを設定済み(パスを新フォルダに更新済み)。
- `vix-dashboard-weekly-outlook` スケジュールタスクは毎朝起動されるが、実際に指標予想・相場解説を更新するのは**月曜日(通常の週次更新)**と、**CPI/雇用統計/FOMC発表の前後1〜2日(スポット更新)**だけ。それ以外の日は何もせず終了する(ECON_EVENTSの日付と連動して自動判定)。
  - 更新するたびに、前回の内容と比較した「変更点」を`changes_since_last`として記録し、ダッシュボード上部に表示する。
  - 完了通知はオフにしている(頻度が読みにくいため)。気になったらいつでもダッシュボードを開けばよい。手動実行は「Scheduled」から「Run now」。
