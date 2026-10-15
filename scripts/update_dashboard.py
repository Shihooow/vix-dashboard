#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIX/SKEW 日次ダッシュボード更新スクリプト

使い方:
  python3 update_dashboard.py <VIX現在値> <VIX3M現在値> <SKEW現在値> [YYYY-MM-DD]

処理内容:
  1. log.csv に本日分の行を追記(同日が既にあれば上書き)
  2. コンタンゴ/バックワーデーション判定 (VIX3M/VIX比率)
  3. SKEW アラート判定 (log.csvは従来の140/144のまま記録。画面は過去1年パーセンタイルで表示)
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
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
WEEKDAYS_JA = "月火水木金土日"

# 米国市場(NYSE)の休場日。データ遅延バッジの判定に使う(年末に翌年分を追記)
US_MARKET_HOLIDAYS = [
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
]


def md_wd(d):
    """date -> '10/2(金)'"""
    return f"{d.month}/{d.day}({WEEKDAYS_JA[d.weekday()]})"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(BASE_DIR, "log.csv")
DASHBOARD_PATH = os.path.join(BASE_DIR, "dashboard.html")
OUTLOOK_PATH = os.path.join(BASE_DIR, "outlook.json")
OUTLOOK_HISTORY_PATH = os.path.join(BASE_DIR, "outlook_history.jsonl")
SIGNALS_PATH = os.path.join(BASE_DIR, "signals.json")  # fetch_signals.py が生成

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


# SKEWは長期的に水準が切り上がっており、固定ライン(140/144)だと毎日「警戒」になって情報がない。
# そこで「過去1年(約252営業日)の中で今日が何%目か」で判定する(2026-10-12追加)。
SKEW_WINDOW = 252


def _pctl(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def skew_rank(skews, idx):
    """skews[idx] が、直近SKEW_WINDOW日(当日含む)の中で何パーセンタイルか(0〜100)。"""
    window = skews[max(0, idx - SKEW_WINDOW + 1): idx + 1]
    v = skews[idx]
    below = sum(1 for x in window if x < v)
    equal = sum(1 for x in window if x == v)
    return round((below + 0.5 * equal) / len(window) * 100), len(window)


def skew_rank_label(pct):
    if pct >= 90:
        return "danger", f"1年で上位10%(上位{100 - pct}%)"
    if pct >= 75:
        return "warn", f"1年の中では高め(上位{100 - pct}%)"
    return "normal", "1年の中では普通"


def skew_year_stats(skews):
    window = sorted(skews[-SKEW_WINDOW:])
    return {
        "median": _pctl(window, 0.5),
        "p75": _pctl(window, 0.75),
        "p90": _pctl(window, 0.9),
        "n": len(window),
    }


def make_row(vix, vix3m, skew, day):
    """log.csv の1行分を作る。"""
    ratio = round(vix3m / vix, 3) if vix else 0.0
    adj_day = next_adjustment_day(day)
    return {
        "date": day.isoformat(),
        "vix": f"{vix:.2f}",
        "vix3m": f"{vix3m:.2f}",
        "ratio": f"{ratio:.3f}",
        "contango_status": contango_status(ratio),
        "skew": f"{skew:.2f}",
        "skew_alert": skew_alert(skew),
        "next_adjustment_date": adj_day.isoformat(),
        "days_to_adjustment": str((adj_day - day).days),
    }


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


def load_signals():
    if os.path.exists(SIGNALS_PATH):
        with open(SIGNALS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def render_signals_parts(sig, latest):
    """signals.json から各セクションの部品HTMLを作る。sigが無ければ従来表示にフォールバック。"""

    def esc(v):
        return "—" if v is None else str(v)

    # --- 結論カード: VIX ---
    vix = float(latest["vix"])
    if vix >= 20:
        vix_badge = '<span class="badge danger">仕込みゾーン(20以上)</span>'
    else:
        vix_badge = f'<span class="badge normal">仕込み目安20まで あと +{20 - vix:.2f}</span>'
    card_vix = f"""
    <div class="card">
      <div class="label">VIX</div>
      <div class="value">{latest['vix']}</div>
      <div class="sub">{vix_badge}</div>
      <div class="sub muted">参考: VIX3M {latest['vix3m']} / 比率 {latest['ratio']}</div>
    </div>"""

    if not sig:
        contango_class = "normal" if "コンタンゴ" in latest["contango_status"] else "danger"
        card_carry = f"""
    <div class="card">
      <div class="label">調整金ゾーン</div>
      <div class="value" style="font-size:20px"><span class="badge {contango_class}">{latest['contango_status']}</span></div>
      <div class="sub muted">先物データ未取得のため VIX3M÷VIX で暫定判定</div>
    </div>"""
        return {"card_vix": card_vix, "card_carry": card_carry, "card_regime": "",
                "regime_section": "", "roll_card": "", "holding_section": "", "basis": "", "stale": {}}

    fut = sig.get("futures") or {}
    cz = sig.get("carry_zone", {})
    ex = sig.get("exit", {})
    regs = sig.get("regime_signals", [])
    hits = [r for r in regs if r.get("hit")]

    # --- 結論カード: 調整金 ---
    card_carry = f"""
    <div class="card">
      <div class="label">調整金ゾーン(先物1〜2限月コンタンゴ率)</div>
      <div class="value">{esc(sig.get('contango_12_pct'))}%</div>
      <div class="sub"><span class="badge {esc(cz.get('class'))}">{esc(cz.get('label'))}</span></div>
      <div class="sub muted">推定調整金 {esc(sig.get('est_yen_per_lot_day'))}円/lot/日(Admin Fee比 {esc(sig.get('est_ratio'))}、30以上で保有OK)</div>
    </div>"""

    # --- 結論カード: 地合い ---
    n = len(hits)
    rclass = "normal" if n == 0 else ("warn" if n == 1 else "danger")
    rlabel = "該当なし" if n == 0 else ("急騰に備える地合い" if n >= 2 else "やや警戒")
    hit_names = " / ".join(r["name"] for r in hits) if hits else "4条件とも非該当"
    card_regime = f"""
    <div class="card">
      <div class="label">地合い(急騰が来やすいか)</div>
      <div class="value">{n}<span style="font-size:16px;color:var(--muted)"> / {len(regs)} 該当</span></div>
      <div class="sub"><span class="badge {rclass}">{rlabel}</span></div>
      <div class="sub muted">{esc(hit_names)}</div>
    </div>"""

    # --- 地合いシグナル表(該当を上に) ---
    ordered = hits + [r for r in regs if not r.get("hit")]
    regime_rows = "".join(
        f'<tr class="{"hit-row" if r["hit"] else ""}"><td style="width:64px"><span class="badge {"danger" if r["hit"] else "normal"}">{"該当" if r["hit"] else "—"}</span></td>'
        f'<td>{esc(r["name"])}<div class="sub muted">{esc(r["rule"])}</div></td>'
        f'<td class="muted">{esc(r["stat"])}</td></tr>'
        for r in ordered
    )
    regime_section = f"""
  <section>
    <h2>地合いシグナル(2013〜2026年の検証)</h2>
    <table class="regime"><tbody>{regime_rows}</tbody></table>
    <div class="sub muted" style="margin-top:8px">根拠: S&amp;P500 50日線乖離 {esc(sig.get('spx_vs_ma50_pct'))}% / VIX − 実現ボラ20日 {esc(sig.get('vix_minus_rv'))}pt / VIX9D÷VIX {esc(sig.get('vix9d_over_vix'))} / 期近 {esc(fut.get('f1'))}({esc(fut.get('exp1'))}満期)・次限月 {esc(fut.get('f2'))}</div>
    <div class="sub" style="margin-top:10px"><b>ノーポジ時に該当したら</b>: VIX20前後にアラートを入れて、証拠金の余力を空けて待つ。</div>
    <div class="sub"><b>保有中に該当したら</b>: 積み増しは止める。2つ以上該当したら、下のストレステストの金額とロット数を見比べて縮小を検討する。</div>
    <div class="sub muted">急騰が来やすい地合いかどうかの目安で、タイミングの予測ではない。</div>
  </section>"""

    # --- 日程: 次のロール ---
    roll_days = ""
    try:
        rd = datetime.strptime(fut.get("roll_date"), "%Y-%m-%d").date()
        base = datetime.strptime(latest["date"], "%Y-%m-%d").date()
        roll_days = f"{(rd - base).days}日"
    except Exception:
        pass
    roll_card = f"""
    <div class="card">
      <div class="label">次のロール(満期の前日)</div>
      <div class="value">{roll_days or esc(fut.get('roll_date'))}</div>
      <div class="sub">{esc(fut.get('roll_date'))}</div>
      <div class="sub muted">この前後で調整金の水準が切り替わりやすい</div>
    </div>"""

    # --- 建てる前・保有中 ---
    stress_rows = "".join(
        f'<tr><td>VIX {s["to"]}</td><td>{s["loss_yen"]:,}円</td></tr>'
        for s in sig.get("stress_per_100lot", [])
    )
    tp_badge = (
        '<span class="badge danger">利確ゾーン</span>' if ex.get("take_profit")
        else '<span class="badge normal">保有継続の余地あり</span>'
    )
    holding_section = f"""
  <section>
    <h2>建てる前・保有中に見るもの</h2>
    <div class="grid">
      <div class="card">
        <div class="label">ストレステスト(今の水準で100lot売った場合)</div>
        <table><tbody>{stress_rows}</tbody></table>
        <div class="sub muted">ロット数に比例。建てる前に「ロット数 ÷ 100 × この金額」が証拠金で耐えられるか確認。</div>
      </div>
      <div class="card">
        <div class="label">保有中の利確判定(今後20営業日の平均)</div>
        <div class="value" style="font-size:20px">{tp_badge}</div>
        <div class="sub">VIX {esc(ex.get('vix_band'))}帯: 残りの利幅 {esc(ex.get('down_pt'))}pt + 調整金 {esc(ex.get('carry_pt_20d'))}pt = {esc(ex.get('remaining_pt'))}pt</div>
        <div class="sub muted">上振れリスクの平均: {esc(ex.get('up_pt'))}pt</div>
      </div>
    </div>
  </section>"""

    return {"card_vix": card_vix, "card_carry": card_carry, "card_regime": card_regime,
            "regime_section": regime_section, "roll_card": roll_card,
            "holding_section": holding_section,
            "basis": "", "stale": sig.get("stale") or {}}


# ===== 第1段階(2026-10追加): 急落時の行動ルール =====
MARKETS_PATH = os.path.join(BASE_DIR, "markets.json")  # fetch_markets.py が生成

# VIXの段階ごとの行動(4口座)。下限 <= VIX < 上限
ACTION_TIERS = [
    (0, 20, "待機", "normal", {
        "vix": "待機。VIX20でアラート",
        "nisa": "積立4本だけ(いつも通り)",
        "tokutei": "なし",
        "crypto": "両建てのポイント稼ぎ(いつも通り)",
    }),
    (20, 25, "第1段階", "warn", {
        "vix": "1単位(100lot)。4時間足RSI70超えで建てる",
        "nisa": "積立だけ",
        "tokutei": "なし",
        "crypto": "いつも通り",
    }),
    (25, 30, "第2段階", "warn", {
        "vix": "+1単位(合計2)",
        "nisa": "積立だけ",
        "tokutei": "SOXLに3万円",
        "crypto": "両建てのレバレッジを半分に。新規は建てない",
    }),
    (30, 40, "第3段階", "danger", {
        "vix": "+1単位(合計3)",
        "nisa": "10万円スポット買い(下の「拾う候補」で一番下げているもの)",
        "tokutei": "TQQQに3万円",
        "crypto": "USDCのうち10万円でBTCを現物買い(長期)",
    }),
    (40, 50, "第4段階", "danger", {
        "vix": "+1単位(合計4、ここで上限)",
        "nisa": "追加で10万円",
        "tokutei": "追加しない",
        "crypto": "使っていないUSDCを取引所から引き出す",
    }),
    (50, 999, "第5段階", "danger", {
        "vix": "追加しない",
        "nisa": "最後の10万円(年30万円の上限まで)",
        "tokutei": "なし",
        "crypto": "守り優先",
    }),
]

RULES_TEXT = [
    "VIXは最大4単位(20・25・30・40で1単位ずつ)。<b>VIX全体の含み損が−60万円になったら全部決済</b>",
    "SOXL・TQQQは +30%で半分利益確定 / −30%で損切り / 最長3ヶ月",
    "NISAのスポット買いは年30万円まで(急落専用の現金から)。買ったものは売らない",
    "4口座合計の急落作戦の損失が年−80万円に達したら、その年は新しく入らない",
    "半導体だけの急落(VIX20未満)ではSOXLは使わず、NISAで日経半導体を拾う",
    "VIXの待機資金は円で持つ。円高が進んだら円→USDCで暗号資産側を補充",
]


def load_markets():
    if os.path.exists(MARKETS_PATH):
        try:
            with open(MARKETS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def render_action_section(latest, sig, mk):
    """今日のアクション: VIXの段階 × 4口座の行動を表示する。"""
    vix = float(latest["vix"])
    tier = next(t for t in ACTION_TIERS if t[0] <= vix < t[1])
    lo, hi, name, cls, acts = tier
    acts = dict(acts)
    crash = (mk or {}).get("crash_type") or {}
    notes = []

    # 半導体だけの急落(VIXは低いまま)の上書き
    if crash.get("type") == "semis" and vix < 20:
        acts["nisa"] = "日経半導体を10万円スポット買い(半導体だけの急落)"
        acts["tokutei"] = "SOXLは使わない(半導体だけの急落は勝率50%・最悪−59%)"
        notes.append("半導体だけの急落を検知")
    # 第2段階のSOXLは「半導体だけの急落」では使わない(VIX25以上の検証で有効だったのは全体の急落)
    if lo == 25 and crash.get("type") == "semis":
        acts["tokutei"] = "SOXLは見送り(半導体だけの急落のため)"
    # 調整金ゾーン
    cz = (sig or {}).get("carry_zone") or {}
    if vix >= 20 and cz.get("class") == "danger":
        notes.append("調整金が薄い/マイナス(" + str(cz.get("label")) + ")。建てるなら早めの利確前提")

    # 次の段階まで
    nxt = next((t for t in ACTION_TIERS if t[0] > vix), None)
    to_next = f"次の段階(VIX{nxt[0]}・{nxt[2]})まで あと +{nxt[0] - vix:.2f}" if nxt else "最終段階"
    rng = f"VIX {hi}未満" if lo == 0 else (f"VIX {lo}以上" if hi >= 999 else f"VIX {lo}〜{hi}")

    def cell(title, body):
        return f'<div class="act"><div class="act-h">{title}</div><div class="act-b">{body}</div></div>'

    notes_html = "".join(f'<div class="sub">⚠ {n}</div>' for n in notes)
    rules_html = "".join(f"<li>{r}</li>" for r in RULES_TEXT)
    crash_badge = ""
    if crash:
        crash_badge = f'<span class="badge {crash.get("class", "normal")}">株価: {crash.get("label")}</span>'

    return f"""
  <section>
    <h2>今日のアクション(急落時の追加ルール)</h2>
    <div class="card tier-card tier-{cls}">
      <div class="tier-top">
        <div>
          <div class="label">今の段階({rng})</div>
          <div class="value">{name}<span class="tier-vix">VIX {latest['vix']}</span></div>
        </div>
        <div class="tier-side">{crash_badge}<div class="sub muted">{to_next}</div></div>
      </div>
      {notes_html}
      <div class="acts">
        {cell("VIX(IG証券)", acts["vix"])}
        {cell("NISA成長投資枠", acts["nisa"])}
        {cell("特定口座(レバレッジ)", acts["tokutei"])}
        {cell("暗号資産(Perp・USDC)", acts["crypto"])}
      </div>
      <details class="rules"><summary>運用ルール(2026/10改訂)</summary><ul>{rules_html}</ul></details>
    </div>
  </section>"""


def render_crash_section(mk):
    """急落の種類・NISAで拾う候補・ドル円/BTC。"""
    if not mk or not mk.get("markets"):
        return """
  <section>
    <h2>急落の種類・拾う候補</h2>
    <div class="card"><div class="sub muted">市場データ(markets.json)がまだありません。fetch_signals.py の実行時に自動で作られます。</div></div>
  </section>"""
    m = mk["markets"]
    crash = mk.get("crash_type") or {}

    def f(v, unit="%"):
        if v is None:
            return "—"
        color = "var(--danger)" if v < 0 else "var(--normal)"
        return f'<span style="color:{color}">{v:+.1f}{unit}</span>'

    # NISAで拾う候補 = FANG+・日経半導体・S&P500のうち52週高値からの下落が最大のもの
    cands = [k for k in ("fang", "nsemi", "spx") if k in m and m[k].get("dd_52w") is not None]
    pick = min(cands, key=lambda k: m[k]["dd_52w"]) if cands else None

    order = ["spx", "ndx", "sox", "fang", "nsemi", "btc", "usdjpy"]
    rows = ""
    for k in order:
        if k not in m:
            continue
        x = m[k]
        tag = ' <span class="badge warn">拾う候補</span>' if k == pick else ""
        hl = ' class="hit-row"' if k == pick else ""
        close = f"{x['close']:,.2f}" if x["close"] < 1000 else f"{x['close']:,.0f}"
        rows += (f"<tr{hl}><td>{x['name']}{tag}</td><td>{close}</td><td>{f(x.get('chg_1d'))}</td>"
                 f"<td>{f(x.get('chg_5d'))}</td><td>{f(x.get('dd_52w'))}</td></tr>")

    fx = m.get("usdjpy")
    fx_msg = "—"
    if fx and fx.get("chg_20d") is not None:
        if fx["chg_20d"] <= -3:
            fx_msg = f'<span class="badge warn">円高</span> 20日で{fx["chg_20d"]:+.1f}%。円→USDCで暗号資産側を補充する目安'
        else:
            fx_msg = f'20日で{fx["chg_20d"]:+.1f}%(円高サインなし)'
    btc = m.get("btc")
    btc_msg = f"高値から{btc['dd_52w']:+.1f}%。VIX30以上でUSDC10万円分を現物買い" if btc else "—"
    pick_msg = f"{m[pick]['name']}(52週高値から{m[pick]['dd_52w']:+.1f}%)" if pick else "—"
    err = mk.get("errors") or {}
    err_html = f'<div class="sub muted">取得失敗: {", ".join(err)}</div>' if err else ""

    return f"""
  <section>
    <h2>急落の種類・拾う候補</h2>
    <div class="grid">
      <div class="card">
        <div class="label">急落の種類(株価5日騰落・52週高値からの下落で判定)</div>
        <div class="value" style="font-size:22px"><span class="badge {crash.get('class', 'normal')}" style="font-size:15px">{crash.get('label', '—')}</span></div>
        <div class="sub muted">全体: S&amp;P500が5日で−4%以下 or 高値から−8%以下 / 半導体だけ: SOXが5日で−8%以下 or 高値から−15%以下(S&amp;P500は−3%より上)</div>
      </div>
      <div class="card">
        <div class="label">NISAで拾うなら(一番下げているもの)</div>
        <div class="value" style="font-size:20px">{pick_msg}</div>
        <div class="sub muted">FANG+・日経半導体(200A)・S&amp;P500から自動選択。実行はVIX30以上 or 半導体だけの急落時</div>
      </div>
      <div class="card">
        <div class="label">ドル円・BTC</div>
        <div class="sub">ドル円 {fx['close'] if fx else '—'}: {fx_msg}</div>
        <div class="sub">BTC: {btc_msg}</div>
      </div>
    </div>
    <table>
      <thead><tr><th>指数</th><th>終値</th><th>前日比</th><th>5日</th><th>52週高値から</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    {err_html}
  </section>"""


def render_dashboard(rows, latest):
    recent = rows[-30:]
    recent_rev = list(reversed(recent))
    outlook = load_outlook()
    outlook_html = render_outlook_section(outlook)
    now_jst = datetime.now(JST)
    page_updated = f"{md_wd(now_jst.date())} {now_jst.strftime('%H:%M')}"
    data_date = datetime.strptime(latest["date"], "%Y-%m-%d").date()
    data_label = md_wd(data_date)
    sig_data = load_signals()
    parts = render_signals_parts(sig_data, latest)
    mk = load_markets()
    action_html = render_action_section(latest, sig_data, mk)
    crash_html = render_crash_section(mk)
    stale_items = dict(parts.get("stale") or {})
    # 最新の数値が delayed_quotes 由来(CSV未反映)のときは、シグナル欄が何日の値かを明記する
    if sig_data and sig_data.get("date") and sig_data["date"] < latest["date"]:
        stale_items["地合いシグナル・先物"] = sig_data["date"]
    stale_note = ""
    if stale_items:
        txt = " / ".join(
            f"{k}は{md_wd(datetime.strptime(v, '%Y-%m-%d').date())}の値"
            for k, v in stale_items.items()
        )
        stale_note = f'<span class="upd-note">※Cboeの反映待ちのため一部前日値: {txt}</span>'

    def esc(v):
        return str(v)

    # SKEWは固定ラインではなく「過去1年の中での位置(パーセンタイル)」で判定
    all_skews = [float(r["skew"]) for r in rows]
    n_rows = len(rows)

    def skew_cell(i):
        pct, _ = skew_rank(all_skews, i)
        cls, _lbl = skew_rank_label(pct)
        return f'<span class="badge {cls}">{pct}%</span>'

    table_rows = "\n".join(
        f"<tr><td>{esc(r['date'])}</td><td>{esc(r['vix'])}</td>"
        f"<td>{esc(r['vix3m'])}</td><td>{esc(r['ratio'])}</td>"
        f"<td>{esc(r['contango_status'])}</td><td>{esc(r['skew'])}</td>"
        f"<td>{skew_cell(n_rows - 1 - j)}</td></tr>"
        for j, r in enumerate(recent_rev)
    )

    skew_pct, skew_n = skew_rank(all_skews, n_rows - 1)
    skew_class, skew_label = skew_rank_label(skew_pct)
    sk = skew_year_stats(all_skews)

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
  .updated {{ color: var(--muted); font-size: 13px; margin-bottom: 20px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; }}
  .updated .upd-main {{ color: var(--text); font-size: 15px; font-weight: 700; }}
  .updated .upd-note {{ flex-basis: 100%; font-size: 12px; }}
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
  .muted {{ color: var(--muted); }}
  .badge {{ white-space: nowrap; }}
  section > .sub {{ font-size: 12px; margin-top: 6px; line-height: 1.6; }}
  .card .sub.muted {{ font-size: 12px; }}
  table.regime td {{ text-align: left; vertical-align: top; }}
  table.regime td:last-child {{ text-align: right; font-size: 12px; white-space: nowrap; }}
  tr.hit-row td {{ background: rgba(185,28,28,0.06); }}
  @media (max-width: 640px) {{ body {{ padding: 16px; }} table.regime td:last-child {{ white-space: normal; }} }}
  .tier-card {{ border-left: 6px solid var(--normal); }}
  .tier-card.tier-warn {{ border-left-color: var(--warn); background: #fffbeb; }}
  .tier-card.tier-danger {{ border-left-color: var(--danger); background: #fef2f2; }}
  .tier-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }}
  .tier-vix {{ font-size: 15px; color: var(--muted); font-weight: 600; margin-left: 10px; }}
  .tier-side {{ text-align: right; }}
  .acts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin-top: 14px; }}
  .act {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }}
  .act-h {{ font-size: 12px; color: var(--muted); font-weight: 600; margin-bottom: 4px; }}
  .act-b {{ font-size: 14px; line-height: 1.5; }}
  details.rules {{ margin-top: 12px; font-size: 13px; }}
  details.rules summary {{ cursor: pointer; color: var(--accent); font-weight: 600; }}
  details.rules ul {{ margin: 8px 0 0; padding-left: 18px; line-height: 1.7; }}
  @media (max-width: 640px) {{ .tier-side {{ text-align: left; }} }}
  .changes-card {{ border-left: 4px solid var(--accent); background: rgba(15,118,110,0.08); }}
</style>
</head>
<body>
  <h1>VIXショート運用ダッシュボード</h1>
  <div class="updated">
    <span class="upd-main">ページ更新 {page_updated}</span>
    <span class="upd-data">データ: 米国 {data_label} 終値</span>
    <span id="stale-badge" class="badge warn" style="display:none"></span>
    {stale_note}
  </div>

  {action_html}

  <section>
    <h2>今日の結論</h2>
    <div class="grid">
      {parts['card_vix']}
      {parts['card_carry']}
      {parts['card_regime']}
    <div class="card">
      <div class="label">SKEW指数</div>
      <div class="value">{latest['skew']}</div>
      <div class="sub"><span class="badge {skew_class}">{skew_label}</span></div>
      <div class="sub muted">過去1年で{skew_pct}パーセンタイル(直近{skew_n}営業日)<br>1年の中央値 {sk['median']:.1f} / 上位25% {sk['p75']:.1f} / 上位10% {sk['p90']:.1f}</div>
      <div class="sub muted">※判断の軸にはしない参考値</div>
    </div>
    </div>
  </section>

  {parts['regime_section']}

  {crash_html}

  <section>
    <h2>日程</h2>
    <div class="grid">
    <div class="card">
      <div class="label">次回価格調整日(毎月第2水曜)</div>
      <div class="value">{latest['days_to_adjustment']}日</div>
      <div class="sub">{latest['next_adjustment_date']}</div>
    </div>
      {parts['roll_card']}
    <div class="card" id="next-high-card">
      <div class="label">次の最重要指標(FOMC・CPI・雇用統計)</div>
      <div class="value" id="nh-days">—</div>
      <div class="sub" id="nh-name"></div>
    </div>
    </div>
  </section>

  {parts['holding_section']}

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
      <div class="label">SKEW指数 ・ 過去1年の中央値 {sk['median']:.1f} / 上位10%ライン {sk['p90']:.1f}</div>
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
        <tr><th>日付</th><th>VIX</th><th>VIX3M</th><th>比率</th><th>判定</th><th>SKEW</th><th>1年順位</th></tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </section>

  <script id="log-data" type="application/json">{chart_data_json}</script>
  <script id="econ-data" type="application/json">{econ_events_json}</script>
  <script>
    // データ遅延バッジ: 閲覧時点(日本時間)で取得できているはずの米国終値日と比べる
    (function() {{
      var dataDate = "{latest['date']}";
      var holidays = {json.dumps(US_MARKET_HOLIDAYS)};
      function iso(d) {{ return d.toISOString().slice(0, 10); }}
      var jst = new Date(Date.now() + 9 * 3600 * 1000);  // UTCフィールドで日本時間を表す
      var d = new Date(Date.UTC(jst.getUTCFullYear(), jst.getUTCMonth(), jst.getUTCDate()));
      // 前日の米国終値は日本時間8時までに反映される想定
      d.setUTCDate(d.getUTCDate() - (jst.getUTCHours() >= 8 ? 1 : 2));
      while (d.getUTCDay() === 0 || d.getUTCDay() === 6 || holidays.indexOf(iso(d)) >= 0) {{
        d.setUTCDate(d.getUTCDate() - 1);
      }}
      var expected = iso(d);
      if (dataDate < expected) {{
        var b = document.getElementById("stale-badge");
        var wd = "日月火水木金土";
        b.textContent = "データ遅延: " + (d.getUTCMonth() + 1) + "/" + d.getUTCDate() + "(" + wd[d.getUTCDay()] + ")の終値が未反映";
        b.style.display = "inline-block";
      }}
    }})();
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
          {{ value: {sk['median']:.2f}, color: '#94a3b8' }},
          {{ value: {sk['p90']:.2f}, color: '#dc2626' }}
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

    (function() {{
      const nh = econEvents
        .map(function(e) {{ return Object.assign({{}}, e, {{ days: daysUntil(e.date) }}); }})
        .filter(function(e) {{ return e.days >= 0 && e.importance === 'high'; }})
        .sort(function(a, b) {{ return a.date < b.date ? -1 : 1; }})[0];
      if (nh) {{
        document.getElementById('nh-days').textContent = nh.days === 0 ? '本日' : (nh.days + '日');
        document.getElementById('nh-name').textContent = nh.date + ' ' + nh.name;
        if (nh.days <= 3) document.getElementById('next-high-card').style.borderLeft = '4px solid var(--danger)';
      }}
    }})();
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

    # --from-signals: fetch_signals.py が保存したCboe公式終値で通常更新する
    if len(sys.argv) >= 2 and sys.argv[1] == "--from-signals":
        sig = load_signals()
        if not sig:
            print("signals.json がありません。先に fetch_signals.py を実行してください。")
            sys.exit(1)
        # 直近のCSV確定値で log.csv を書き戻す(取りこぼした日・delayed_quotes由来の暫定値を自動修復)
        tail = sig.get("history_tail") or []
        if tail:
            rows = load_log()
            for h in tail:
                rows = upsert_today(rows, make_row(h["vix"], h["vix3m"], h["skew"],
                                                   datetime.strptime(h["date"], "%Y-%m-%d").date()))
            save_log(rows)
        # CSVが未反映でも、引け後の delayed_quotes があればそちらを最新日として使う
        q = sig.get("latest_quote")
        c, d_str = (q, q["date"]) if q else (sig["closes"], sig["date"])
        sys.argv = [sys.argv[0], str(c["vix"]), str(c["vix3m"]), str(c["skew"]), d_str]

    if len(sys.argv) < 4:
        print("usage: update_dashboard.py <VIX> <VIX3M> <SKEW> [YYYY-MM-DD]")
        print("       update_dashboard.py --from-signals")
        print("       update_dashboard.py --outlook <path-to-json>")
        sys.exit(1)

    vix = float(sys.argv[1])
    vix3m = float(sys.argv[2])
    skew = float(sys.argv[3])
    if len(sys.argv) >= 5:
        today = datetime.strptime(sys.argv[4], "%Y-%m-%d").date()
    else:
        today = date.today()

    row = make_row(vix, vix3m, skew, today)

    rows = load_log()
    rows = upsert_today(rows, row)
    save_log(rows)
    render_dashboard(rows, row)

    print("更新完了:")
    for k, v in row.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
