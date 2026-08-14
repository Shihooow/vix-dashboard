#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIX/SKEW 日次ダッシュボード更新スクリプト

使い方:
  python3 update_dashboard.py <VIX現在値> <VIX3M現在値> <SKEW現在値> [YYYY-MM-DD]

処理内容:
  1. log.csv に本日分の行を追記(同日が既にあれば上書き)
  2. コンタンゴ/バックワーデーション判定 (VIX3M/VIX比率)
  3. SKEW アラート判定 (140超=注意, 144超=警戒)
  4. 毎月第2水曜(価格調整日)までのカウントダウン計算
  5. dashboard.html を最新状態に再生成(静的・オフラインで開ける単一HTML)
     - 1週間/1ヶ月/3ヶ月/6ヶ月/1年の期間タブ付きグラフ
     - 米国の重要経済指標カレンダー(FOMC/CPI/雇用統計/PCE/GDP/PPI)

このスクリプトはネットワークに一切アクセスしない。
数値の取得(Cboeの delayed_quotes JSON)は呼び出し元(Claude)が
web_fetch で行い、その結果をこのスクリプトの引数として渡す設計。

ECON_EVENTS(経済指標カレンダー)は Federal Reserve / BLS / BEA の公式発表予定表
(2026-08-08 時点)を基に手入力したもの。新しい年のスケジュールが発表されたら
このリストを更新すること(目安: FOMCは前年8月頃、CPI/雇用統計/PPIは前年末、
GDP/PCEはBEAが四半期ごとに更新)。
"""

import sys
import csv
import json
import os
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(BASE_DIR, "log.csv")
DASHBOARD_PATH = os.path.join(BASE_DIR, "dashboard.html")
OUTLOOK_PATH = os.path.join(BASE_DIR, "outlook.json")
OUTLOOK_HISTORY_PATH = os.path.join(BASE_DIR, "outlook_history.jsonl")

LOG_HEADER = [
    "date", "vix", "vix3m", "ratio", "contango_status",
    "skew", "skew_alert", "next_adjustment_date", "days_to_adjustment",
]

# 重要度: "high" = 最重要(FOMC/CPI/雇用統計), "mid" = 重要(PCE/GDP/PPI)
ECON_EVENTS = [
    {"date": "2026-08-12", "name": "CPI(消費者物価指数)", "importance": "high"},
    {"date": "2026-08-13", "name": "PPI(卸売物価指数)", "importance": "mid"},
    {"date": "2026-08-26", "name": "GDP改定値・PCE(個人消費支出)", "importance": "mid"},
    {"date": "2026-09-04", "name": "雇用統計(非農業部門雇用者数)", "importance": "high"},
    {"date": "2026-09-10", "name": "PPI(卸売物価指数)", "importance": "mid"},
    {"date": "2026-09-11", "name": "CPI(消費者物価指数)", "importance": "high"},
    {"date": "2026-09-16", "name": "FOMC政策金利発表", "importance": "high"},
    {"date": "2026-09-30", "name": "GDP確定値・PCE(個人消費支出)", "importance": "mid"},
    {"date": "2026-10-02", "name": "雇用統計(非農業部門雇用者数)", "importance": "high"},
    {"date": "2026-10-14", "name": "CPI(消費者物価指数)", "importance": "high"},
    {"date": "2026-10-15", "name": "PPI(卸売物価指数)", "importance": "mid"},
    {"date": "2026-10-28", "name": "FOMC政策金利発表", "importance": "high"},
    {"date": "2026-10-29", "name": "GDP速報値・PCE(個人消費支出)", "importance": "mid"},
    {"date": "2026-11-06", "name": "雇用統計(非農業部門雇用者数)", "importance": "high"},
    {"date": "2026-11-10", "name": "CPI(消費者物価指数)", "importance": "high"},
    {"date": "2026-11-13", "name": "PPI(卸売物価指数)", "importance": "mid"},
    {"date": "2026-11-25", "name": "GDP改定値・PCE(個人消費支出)", "importance": "mid"},
    {"date": "2026-12-04", "name": "雇用統計(非農業部門雇用者数)", "importance": "high"},
    {"date": "2026-12-09", "name": "FOMC政策金利発表", "importance": "high"},
    {"date": "2026-12-10", "name": "CPI(消費者物価指数)", "importance": "high"},
    {"date": "2026-12-15", "name": "PPI(卸売物価指数)", "importance": "mid"},
    {"date": "2026-12-23", "name": "GDP確定値・PCE(個人消費支出)", "importance": "mid"},
]


def second_wednesday(year: int, month: int) -> date:
    d = date(year, month, 1)
    wednesdays = []
    while len(wednesdays) < 2:
        if d.weekday() == 2:
            wednesdays.append(d)
        try:
            d = d.replace(day=d.day + 1)
        except ValueError:
            break
    return wednesdays[1]


def next_adjustment_day(today: date) -> date:
    this_month_2nd_wed = second_wednesday(today.year, today.month)
    if today <= this_month_2nd_wed:
        return this_month_2nd_wed
    year, month = today.year, today.month + 1
    if month > 12:
        month = 1
        year += 1
    return second_wednesday(year, month)


def contango_status(ratio: float) -> str:
    if ratio > 1.0:
        return "コンタンゴ(順鞘)"
    elif ratio < 1.0:
        return "バックワーデーション(逆鞘・警戒)"
    return "フラット"


def skew_alert(skew: float) -> str:
    if skew >= 144:
        return "警戒(144超)"
    elif skew >= 140:
        return "注意(140超)"
    return "通常"


def load_log():
    rows = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
    return rows


def save_log(rows):
    with open(LOG_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADER)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def load_outlook():
    """指標予想・相場解説(週次更新分)を読み込む。ファイルが無ければNone。"""
    if os.path.exists(OUTLOOK_PATH):
        with open(OUTLOOK_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_outlook(data):
    with open(OUTLOOK_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def archive_outlook_to_history(old_outlook):
    """上書きされる直前のoutlookを outlook_history.jsonl に1行追記する(監査・差分確認用)。"""
    if not old_outlook:
        return
    with open(OUTLOOK_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(old_outlook, ensure_ascii=False) + "\n")


def upsert_today(rows, today_row):
    today_str = today_row["date"]
    replaced = False
    for i, r in enumerate(rows):
        if r["date"] == today_str:
            rows[i] = today_row
            replaced = True
            break
    if not replaced:
        rows.append(today_row)
    rows.sort(key=lambda r: r["date"])
    return rows


def render_outlook_section(outlook):
    """指標予想・相場解説セクションのHTMLを組み立てる。outlookがNoneなら未設定の案内を出す。"""
    if not outlook:
        return """
  <section>
    <h2>指標予想・相場解説(週次更新)</h2>
    <div class="card">
      <div class="sub" style="color:var(--muted)">
        まだ設定されていません。outlook.json を作成すると、次回FOMC/CPI/雇用統計の市場予想と
        株式・VIXの見通し解説がここに表示されます。
      </div>
    </div>
  </section>
"""

    def esc(v):
        return str(v) if v is not None else "—"

    fomc = outlook.get("next_fomc", {})
    cpi = outlook.get("next_cpi", {})
    jobs = outlook.get("next_jobs", {})

    sources = outlook.get("sources", [])
    sources_html = ""
    if sources:
        links = "".join(
            f'<a href="{esc(s.get("url"))}" target="_blank" rel="noopener" '
            f'style="color:var(--accent);text-decoration:none;margin-right:14px">{esc(s.get("label", s.get("url")))}</a>'
            for s in sources
        )
        sources_html = f'<div class="outlook-sources">{links}</div>'

    changes = outlook.get("changes_since_last")
    changes_html = ""
    if changes:
        if isinstance(changes, list):
            items = "".join(f"<li>{esc(c)}</li>" for c in changes)
            changes_body = f'<ul style="margin:4px 0 0;padding-left:18px">{items}</ul>'
        else:
            changes_body = f"<div>{esc(changes)}</div>"
        changes_html = f"""
    <div class="card changes-card" style="margin-bottom:16px">
      <div class="label">前回更新からの変更点</div>
      <div class="outlook-text">{changes_body}</div>
    </div>
"""

    return f"""
  <section>
    <h2>指標予想・相場解説(週次更新)</h2>
    <div class="updated" style="margin-bottom:14px">最終更新: {esc(outlook.get('updated'))}</div>
    {changes_html}
    <div class="grid" style="margin-bottom:16px">
      <div class="card">
        <div class="label">FF金利(現在)</div>
        <div class="value" style="font-size:22px">{esc(outlook.get('fed_funds_rate'))}</div>
      </div>
      <div class="card">
        <div class="label">次回FOMC {esc(fomc.get('date'))}</div>
        <div class="sub" style="line-height:1.6">{esc(fomc.get('view'))}</div>
      </div>
      <div class="card">
        <div class="label">次回CPI {esc(cpi.get('date'))}</div>
        <div class="sub" style="line-height:1.6">市場予想: {esc(cpi.get('consensus'))}</div>
      </div>
      <div class="card">
        <div class="label">次回雇用統計 {esc(jobs.get('date'))}</div>
        <div class="sub" style="line-height:1.6">市場予想: {esc(jobs.get('consensus'))}</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:12px">
      <div class="label">株式(S&P500など)の見通し</div>
      <div class="outlook-text">{esc(outlook.get('market_view'))}</div>
    </div>
    <div class="card">
      <div class="label">VIX・ショートポジションへの見立て</div>
      <div class="outlook-text">{esc(outlook.get('vix_view'))}</div>
    </div>
    {sources_html}
  </section>
"""


def render_dashboard(rows, latest):
    recent = rows[-30:]
    recent_rev = list(reversed(recent))
    outlook = load_outlook()
    outlook_html = render_outlook_section(outlook)

    def esc(v):
        return str(v)

    table_rows = "\n".join(
        f"<tr><td>{esc(r['date'])}</td><td>{esc(r['vix'])}</td>"
        f"<td>{esc(r['vix3m'])}</td><td>{esc(r['ratio'])}</td>"
        f"<td>{esc(r['contango_status'])}</td><td>{esc(r['skew'])}</td>"
        f"<td>{esc(r['skew_alert'])}</td></tr>"
        for r in recent_rev
    )

    skew_class = "normal"
    if "警戒" in latest["skew_alert"]:
        skew_class = "danger"
    elif "注意" in latest["skew_alert"]:
        skew_class = "warn"

    contango_class = "normal" if "コンタンゴ" in latest["contango_status"] else "danger"

    # 全期間データをJSONとして埋め込み、期間タブはブラウザ側JSで描画する
    chart_data = [
        {
            "date": r["date"],
            "vix": float(r["vix"]),
            "vix3m": float(r["vix3m"]),
            "skew": float(r["skew"]),
        }
        for r in rows
    ]
    chart_data_json = json.dumps(chart_data, ensure_ascii=False)
    econ_events_json = json.dumps(ECON_EVENTS, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VIXショート運用ダッシュボード</title>
<style>
  :root {{
    --bg: #eef2f7;
    --card: #ffffff;
    --text: #1e2a3a;
    --muted: #6b7c93;
    --normal: #15803d;
    --warn: #b45309;
    --danger: #b91c1c;
    --border: #d7dfe9;
    --accent: #0f766e;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: -apple-system, "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .updated {{ color: var(--muted); font-size: 13px; margin-bottom: 20px; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px 20px;
  }}
  .card .label {{ color: var(--muted); font-size: 12px; margin-bottom: 6px; }}
  .card .value {{ font-size: 30px; font-weight: 700; }}
  .card .sub {{ font-size: 13px; margin-top: 6px; }}
  .badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
  }}
  .badge.normal {{ background: #dcfce7; color: var(--normal); }}
  .badge.warn {{ background: #fef3c7; color: var(--warn); }}
  .badge.danger {{ background: #fee2e2; color: var(--danger); }}
  .badge.imp-high {{ background: #fee2e2; color: var(--danger); }}
  .badge.imp-mid {{ background: #fef3c7; color: var(--warn); }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    font-size: 13px;
  }}
  th, td {{
    padding: 8px 10px;
    text-align: right;
    border-bottom: 1px solid var(--border);
  }}
  th:first-child, td:first-child {{ text-align: left; }}
  th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
  th {{ color: var(--muted); font-weight: 600; background: var(--bg); }}
  tr.today-row td {{ background: rgba(15,118,110,0.10); }}
  section {{ margin-bottom: 28px; }}
  section h2 {{ font-size: 15px; color: var(--muted); margin: 0 0 10px; font-weight: 600; }}
  .tabs {{
    display: flex;
    gap: 6px;
    margin-bottom: 14px;
    flex-wrap: wrap;
  }}
  .tab-btn {{
    background: var(--card);
    border: 1px solid var(--border);
    color: var(--muted);
    padding: 6px 14px;
    border-radius: 999px;
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }}
  .tab-btn.active {{
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    font-weight: 600;
  }}
  .data-note {{
    color: var(--warn);
    font-size: 12px;
    margin-top: 6px;
  }}
  .chart-wrap {{
    position: relative;
    padding-left: 28px;
  }}
  .chart-svg {{
    display: block;
    width: 100%;
  }}
  .y-labels {{
    position: absolute;
    left: 0;
    top: 0;
    width: 26px;
    height: 100%;
  }}
  .y-labels span {{
    position: absolute;
    left: 0;
    font-size: 11px;
    color: #6b7c93;
    white-space: nowrap;
    font-family: -apple-system, "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
  }}
  .axis-labels {{
    position: relative;
    height: 18px;
    margin-top: 6px;
    margin-left: 28px;
  }}
  .axis-labels span {{
    position: absolute;
    top: 0;
    font-size: 12px;
    color: #6b7c93;
    white-space: nowrap;
    font-family: -apple-system, "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
  }}
  .next-event-banner {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: linear-gradient(90deg, rgba(185,28,28,0.08), rgba(255,255,255,0));
    border: 1px solid var(--border);
    border-left: 4px solid var(--danger);
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 14px;
  }}
  .next-event-banner .ne-name {{ font-size: 15px; font-weight: 700; }}
  .next-event-banner .ne-date {{ color: var(--muted); font-size: 13px; margin-top: 2px; }}
  .next-event-banner .ne-days {{ font-size: 22px; font-weight: 700; color: var(--danger); text-align: right; }}
  .next-event-banner .ne-days-label {{ font-size: 11px; color: var(--muted); text-align: right; }}
  .legend {{ display: flex; gap: 16px; font-size: 12px; color: var(--muted); margin-bottom: 12px; flex-wrap: wrap; }}
  .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .legend i {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .outlook-text {{ font-size: 14px; line-height: 1.8; color: var(--text); }}
  .outlook-sources {{ font-size: 12px; margin-top: 10px; }}
  .changes-card {{ border-left: 4px solid var(--accent); background: rgba(15,118,110,0.08); }}
</style>
</head>
<body>
  <h1>VIXショート運用ダッシュボード</h1>
  <div class="updated">最終更新: {latest['date']}</div>

  <div class="grid">
    <div class="card">
      <div class="label">VIX (Spot)</div>
      <div class="value">{latest['vix']}</div>
      <div class="sub">VIX3M: {latest['vix3m']} / 比率: {latest['ratio']}</div>
    </div>
    <div class="card">
      <div class="label">コンタンゴ/バックワーデーション判定</div>
      <div class="value" style="font-size:20px">
        <span class="badge {contango_class}">{latest['contango_status']}</span>
      </div>
      <div class="sub">VIX3M ÷ VIX の比率で判定(1超=コンタンゴ)</div>
    </div>
    <div class="card">
      <div class="label">SKEW指数</div>
      <div class="value">{latest['skew']}</div>
      <div class="sub"><span class="badge {skew_class}">{latest['skew_alert']}</span></div>
    </div>
    <div class="card">
      <div class="label">次回価格調整日(毎月第2水曜)</div>
      <div class="value">{latest['days_to_adjustment']}日</div>
      <div class="sub">{latest['next_adjustment_date']}</div>
    </div>
  </div>

  <section>
    <h2>推移グラフ</h2>
    <div class="tabs" id="period-tabs">
      <button class="tab-btn" data-days="7">1週間</button>
      <button class="tab-btn" data-days="30">1ヶ月</button>
      <button class="tab-btn" data-days="90">3ヶ月</button>
      <button class="tab-btn" data-days="180">6ヶ月</button>
      <button class="tab-btn" data-days="365">1年</button>
    </div>

    <div class="card" style="margin-bottom:12px">
      <div class="label">VIX ・ 目安ライン15 / 20 / 25</div>
      <div id="vix-chart"></div>
    </div>
    <div class="card">
      <div class="label">SKEW指数 ・ 注意ライン140 / 警戒ライン144</div>
      <div id="skew-chart"></div>
    </div>
    <div class="data-note" id="data-note"></div>
  </section>

  {outlook_html}

  <section>
    <h2>経済指標カレンダー(米国)</h2>
    <div id="next-event-banner"></div>
    <div class="legend">
      <span><i style="background:var(--danger)"></i>最重要(FOMC・CPI・雇用統計)</span>
      <span><i style="background:var(--warn)"></i>重要(PCE・GDP・PPI)</span>
    </div>
    <table>
      <thead>
        <tr><th>日付</th><th>指標</th><th>残り</th><th>重要度</th></tr>
      </thead>
      <tbody id="econ-table-body"></tbody>
    </table>
  </section>

  <section>
    <h2>日次ログ(直近{len(recent)}日)</h2>
    <table>
      <thead>
        <tr><th>日付</th><th>VIX</th><th>VIX3M</th><th>比率</th><th>判定</th><th>SKEW</th><th>アラート</th></tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </section>

  <script id="log-data" type="application/json">{chart_data_json}</script>
  <script id="econ-data" type="application/json">{econ_events_json}</script>
  <script>
    const allData = JSON.parse(document.getElementById('log-data').textContent);
    const econEvents = JSON.parse(document.getElementById('econ-data').textContent);
    const CHART_W = 900, CHART_H = 90;

    function pickTickIndices(n, maxTicks) {{
      if (n <= 1) return [0];
      maxTicks = Math.min(maxTicks, n);
      const idx = [];
      for (let i = 0; i < maxTicks; i++) {{
        idx.push(Math.round(i * (n - 1) / (maxTicks - 1)));
      }}
      return Array.from(new Set(idx));
    }}

    function formatTickDate(dateStr, longRange) {{
      const parts = dateStr.split('-');
      const y = parts[0], m = parts[1], d = parts[2];
      return longRange ? (y + '/' + m) : (m + '/' + d);
    }}

    // グラフ本体(折れ線・しきい値線・目盛ガイド線)はSVGで描画。
    // 非等方スケーリング(preserveAspectRatio="none")で文字が潰れないよう、
    // 目盛ラベルの文字は通常のHTML(絶対位置指定)で別レイヤーに重ねる。
    function chartSvg(points, vmin, vmax, opts) {{
      const color = opts.color || '#0f766e';
      const vrange = (vmax - vmin) || 1;
      const step = CHART_W / (points.length - 1);
      let linePoints = [];
      for (let i = 0; i < points.length; i++) {{
        const x = i * step;
        const y = CHART_H - ((points[i].v - vmin) / vrange) * CHART_H;
        linePoints.push(x.toFixed(1) + ',' + y.toFixed(1));
      }}
      let extraLines = '';
      (opts.thresholds || []).forEach(function(t) {{
        if (t.value >= vmin && t.value <= vmax) {{
          const y = CHART_H - ((t.value - vmin) / vrange) * CHART_H;
          extraLines += '<line x1="0" y1="' + y.toFixed(1) + '" x2="' + CHART_W + '" y2="' + y.toFixed(1) +
            '" stroke="' + t.color + '" stroke-width="1" stroke-dasharray="4,4" opacity="0.6" />';
        }}
      }});
      const tickIdx = pickTickIndices(points.length, 6);
      let gridLines = '';
      tickIdx.forEach(function(i) {{
        const x = i * step;
        gridLines += '<line x1="' + x.toFixed(1) + '" y1="0" x2="' + x.toFixed(1) + '" y2="' + CHART_H +
          '" stroke="#c7d2e0" stroke-width="1" opacity="0.7" />';
      }});
      return '<svg class="chart-svg" viewBox="0 0 ' + CHART_W + ' ' + CHART_H +
        '" height="' + CHART_H + '" preserveAspectRatio="none">' + gridLines + extraLines +
        '<polyline fill="none" stroke="' + color + '" stroke-width="2" points="' + linePoints.join(' ') + '" />' +
        '</svg>';
    }}

    function yLabelsHtml(vmin, vmax, thresholds) {{
      const vrange = (vmax - vmin) || 1;
      let spans = '';
      (thresholds || []).forEach(function(t) {{
        if (t.value >= vmin && t.value <= vmax) {{
          const yPct = ((CHART_H - ((t.value - vmin) / vrange) * CHART_H) / CHART_H) * 100;
          spans += '<span style="top:' + yPct.toFixed(2) + '%;transform:translateY(-50%)">' + t.value + '</span>';
        }}
      }});
      return '<div class="y-labels">' + spans + '</div>';
    }}

    function xAxisLabelsHtml(points) {{
      const step = CHART_W / (points.length - 1);
      const firstDate = points[0].date, lastDate = points[points.length - 1].date;
      const spanDays = (new Date(lastDate) - new Date(firstDate)) / 86400000;
      const longRange = spanDays >= 150;
      const tickIdx = pickTickIndices(points.length, 6);
      let spans = '';
      tickIdx.forEach(function(i) {{
        const xPct = (i * step / CHART_W) * 100;
        const label = formatTickDate(points[i].date, longRange);
        let translate;
        if (i === 0) translate = '0';
        else if (i === points.length - 1) translate = '-100%';
        else translate = '-50%';
        spans += '<span style="left:' + xPct.toFixed(2) + '%;transform:translateX(' + translate + ')">' + label + '</span>';
      }});
      return '<div class="axis-labels">' + spans + '</div>';
    }}

    function renderChart(elId, points, opts) {{
      if (points.length < 2) {{
        document.getElementById(elId).innerHTML =
          '<div style="color:#6b7c93;font-size:13px;padding:20px 0">データがまだ十分にありません</div>';
        return;
      }}
      const values = points.map(function(p) {{ return p.v; }});
      let vmin = Math.min(...values), vmax = Math.max(...values);
      if (vmax === vmin) {{ vmax += 1; vmin -= 1; }}
      const pad = (vmax - vmin) * 0.08;
      vmin -= pad; vmax += pad;

      document.getElementById(elId).innerHTML =
        '<div class="chart-wrap">' + yLabelsHtml(vmin, vmax, opts.thresholds) +
        chartSvg(points, vmin, vmax, opts) + '</div>' + xAxisLabelsHtml(points);
    }}

    function render(days) {{
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - days);
      const cutoffStr = cutoff.toISOString().slice(0, 10);
      const filtered = allData.filter(function(r) {{ return r.date >= cutoffStr; }});

      renderChart('vix-chart', filtered.map(function(r) {{ return {{ date: r.date, v: r.vix }}; }}), {{
        color: '#0f766e',
        thresholds: [
          {{ value: 15, color: '#94a3b8' }},
          {{ value: 20, color: '#d97706' }},
          {{ value: 25, color: '#dc2626' }}
        ]
      }});
      renderChart('skew-chart', filtered.map(function(r) {{ return {{ date: r.date, v: r.skew }}; }}), {{
        color: '#c2410c',
        thresholds: [
          {{ value: 140, color: '#d97706' }},
          {{ value: 144, color: '#dc2626' }}
        ]
      }});

      const note = document.getElementById('data-note');
      if (filtered.length > 0 && filtered.length < days * 0.5) {{
        note.textContent = '※ 記録開始からまだ日が浅いため、この期間の全データは揃っていません(現在 ' + filtered.length + ' 日分)';
      }} else {{
        note.textContent = '';
      }}
    }}

    document.querySelectorAll('.tab-btn').forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
        btn.classList.add('active');
        render(parseInt(btn.dataset.days, 10));
      }});
    }});

    // 初期表示は1ヶ月
    document.querySelector('.tab-btn[data-days="30"]').classList.add('active');
    render(30);

    // ---- 経済指標カレンダー ----
    function daysUntil(dateStr) {{
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      const target = new Date(dateStr + 'T00:00:00');
      return Math.round((target - today) / 86400000);
    }}

    function renderEconCalendar() {{
      const upcoming = econEvents
        .map(function(e) {{ return Object.assign({{}}, e, {{ days: daysUntil(e.date) }}); }})
        .filter(function(e) {{ return e.days >= 0; }})
        .sort(function(a, b) {{ return a.date < b.date ? -1 : 1; }});

      const bannerEl = document.getElementById('next-event-banner');
      if (upcoming.length > 0) {{
        const next = upcoming[0];
        const daysLabel = next.days === 0 ? '本日' : (next.days + '日後');
        bannerEl.innerHTML =
          '<div><div class="ne-name">次: ' + next.name + '</div><div class="ne-date">' + next.date + '</div></div>' +
          '<div><div class="ne-days">' + daysLabel + '</div><div class="ne-days-label">まで</div></div>';
      }} else {{
        bannerEl.innerHTML = '';
      }}

      const tbody = document.getElementById('econ-table-body');
      tbody.innerHTML = upcoming.slice(0, 15).map(function(e, i) {{
        const badgeClass = e.importance === 'high' ? 'imp-high' : 'imp-mid';
        const badgeLabel = e.importance === 'high' ? '最重要' : '重要';
        const daysLabel = e.days === 0 ? '本日' : ('あと' + e.days + '日');
        const rowClass = i === 0 ? 'today-row' : '';
        return '<tr class="' + rowClass + '"><td>' + e.date + '</td><td>' + e.name + '</td><td>' + daysLabel +
          '</td><td><span class="badge ' + badgeClass + '">' + badgeLabel + '</span></td></tr>';
      }}).join('');
    }}

    renderEconCalendar();
  </script>

</body>
</html>
"""
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    # --outlook <json_path>: 指標予想・相場解説(週次分)だけを更新してダッシュボード再生成
    if len(sys.argv) >= 2 and sys.argv[1] == "--outlook":
        if len(sys.argv) < 3:
            print("usage: update_dashboard.py --outlook <path-to-json>")
            sys.exit(1)
        with open(sys.argv[2], encoding="utf-8") as f:
            outlook_data = json.load(f)
        archive_outlook_to_history(load_outlook())
        save_outlook(outlook_data)

        rows = load_log()
        if not rows:
            print("log.csv にVIX/SKEWのデータがまだありません。先に通常更新を実行してください。")
            sys.exit(1)
        render_dashboard(rows, rows[-1])
        print("見通しセクションを更新しました:", outlook_data.get("updated"))
        return

    if len(sys.argv) < 4:
        print("usage: update_dashboard.py <VIX> <VIX3M> <SKEW> [YYYY-MM-DD]")
        print("       update_dashboard.py --outlook <path-to-json>")
        sys.exit(1)

    vix = float(sys.argv[1])
    vix3m = float(sys.argv[2])
    skew = float(sys.argv[3])
    if len(sys.argv) >= 5:
        today = datetime.strptime(sys.argv[4], "%Y-%m-%d").date()
    else:
        today = date.today()

    ratio = round(vix3m / vix, 3) if vix else 0.0
    status = contango_status(ratio)
    alert = skew_alert(skew)
    adj_day = next_adjustment_day(today)
    days_remaining = (adj_day - today).days

    row = {
        "date": today.isoformat(),
        "vix": f"{vix:.2f}",
        "vix3m": f"{vix3m:.2f}",
        "ratio": f"{ratio:.3f}",
        "contango_status": status,
        "skew": f"{skew:.2f}",
        "skew_alert": alert,
        "next_adjustment_date": adj_day.isoformat(),
        "days_to_adjustment": str(days_remaining),
    }

    rows = load_log()
    rows = upsert_today(rows, row)
    save_log(rows)
    render_dashboard(rows, row)

    print("更新完了:")
    for k, v in row.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
