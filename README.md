# VIXショート運用ダッシュボード

## スマホで見る(公開URL)
https://shihooow.github.io/vix-dashboard/

## 仕組み(2026-10-15〜 パソコン不要)
- **このGitHubリポジトリが元データ**。`log.csv`・`signals.json`・`markets.json`・`outlook.json`・画面(`index.html`/`dashboard.html`)はすべてここにある。
- **毎日の数値更新**: GitHub Actions(`.github/workflows/update.yml`)が日本時間 7:50 / 18:50(月〜土)に実行し、mainへコミット → GitHub Pagesで公開。パソコンの電源・スリープに関係なく動く。
  - 手動で動かしたいときは GitHub の「Actions」タブ → update-dashboard → 「Run workflow」。
- **週次の指標予想・相場解説**: Claudeのクラウド上のスケジュールタスク(パソコン不要)がWeb検索して `outlook.json` を更新し、mainへコミットする。
- パソコンの `~/Claude/vix-dashboard/` は2026-10-15時点の古い写し。`取引判断ログ.csv`(個人の判断記録)はパソコンだけに置き、公開リポジトリには入れない。
- 以前の自動公開で使っていた `.github_token`(パソコン内に平文保存)は不要になった。

## ファイル構成
- `dashboard.html` — 自動更新される最新状況(index.html と同じ内容)
  - 1週間/1ヶ月/3ヶ月/6ヶ月/1年の期間タブでVIX・SKEWの推移グラフを切り替え可能(2025-08-08〜現在の実データ収録済み)
  - VIXに15/20/25、SKEWに140/144の目安ラインを表示
  - 米国の経済指標カレンダー(FOMC・CPI・雇用統計・PCE・GDP・PPI)を「次のイベントまで」バナー+一覧表で表示
  - **指標予想・相場解説セクション(週次更新)** — FF金利の現状、次回FOMC/CPI/雇用統計の市場予想、株式・VIXの見通し解説を表示
- `log.csv` — VIX/VIX3M/SKEWの日次記録(Excelで開けます)
- `outlook.json` — 指標予想・相場解説の元データ(スケジュールタスクがWebSearchで調べて上書き。`changes_since_last`に前回からの変更点も記録)
- `outlook_history.jsonl` — 2026-10-15まではパソコン側に自動保存していた見通しの履歴(公開しない)。以降の履歴はこのリポジトリの outlook.json のコミット履歴で見返せる
- `取引判断ログ.csv` — 仕込み・決済などの判断を手動で記録する用(パソコンにのみ保存、公開しない)。
- `scripts/update_dashboard.py` — ログ追記+ダッシュボード再生成を行うスクリプト(GitHub Actionsから自動実行)
  - `python3 update_dashboard.py <VIX> <VIX3M> <SKEW> [日付]` — 通常の日次数値更新
  - `python3 update_dashboard.py --outlook outlook.json` — 見通しセクションのみ更新(outlook.jsonを読んで再生成)

## 判定ロジック
- コンタンゴ/バックワーデーション: VIX3M ÷ VIX が1超なら「コンタンゴ(順鞘)」、1未満なら「バックワーデーション(逆鞘・警戒)」
- SKEW: 画面上は「過去1年(252営業日)の中で何パーセンタイルか」で表示(上位10%=赤、上位25%=黄)。log.csvのskew_alert列は従来どおり140/144で記録(2026-10-12変更)
- 価格調整日: 毎月第2水曜。当日までの残り日数をカウントダウン表示

## 経済指標カレンダーについて(要メンテナンス)
`scripts/update_dashboard.py` 内の `ECON_EVENTS` に2026年12月までの日程を手入力しています。
2027年分のスケジュールが発表され次第、リストへの追記が必要です。

## 自動更新の詳細
- Cboeの日次CSVは引けから数時間遅れて更新されるため、CSVが前日のままでも `fetch_signals.py` が delayed_quotes(`cdn-api.cboe.com`)から引け後の公式終値(close)を取り、その日の数値としてログ・画面に反映する(地合いシグナル・先物欄はCSVの日付のまま、画面上部に注記)。
- `update_dashboard.py --from-signals` は毎回、直近15営業日ぶんをCSVの確定値でlog.csvへ書き戻す。実行し損ねた日の取りこぼしや、delayed_quotes由来の暫定値は次の実行で自動的に正しい値へ置き換わる。
- `fetch_signals.py` はVIXとVIX3Mがそろった最新日を基準日にする。SKEW・S&P500・VIX9D・VIX先物の反映が遅れている場合は直近値で代用し、`signals.json` の `stale` に記録してダッシュボード上部に注記する。
- ダッシュボード上部は「ページ更新(日本時間の生成日時)」と「データ: 米国◯/◯終値」を表示。閲覧時点で取れているはずの終値日より古いと黄色の「データ遅延」バッジが出る(米国の休場日は `US_MARKET_HOLIDAYS` で除外。年末に翌年分を追記)。
- 週次の見通し更新は毎朝起動されるが、実際に更新するのは**月曜日**と、**CPI/雇用統計/FOMC発表の前後(スポット更新)**だけ(ECON_EVENTSの日付と連動)。
