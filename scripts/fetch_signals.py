#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIX先物カーブ・地合いシグナル取得スクリプト

使い方:
  python3 fetch_signals.py            # signals.json を生成し、終値を1行で出力
  python3 fetch_signals.py --closes   # 「VIX VIX3M SKEW 日付」だけを出力
                                      # (update_dashboard.py の引数にそのまま渡せる)

取得元(すべてCboe公式の無料CSV):
  - 指数の日次終値: VIX / VIX3M / VIX9D / SKEW / SPX
    https://cdn.cboe.com/api/global/us_indices/daily_prices/<SYMBOL>_History.csv
  - VIX先物の限月別清算値
    https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_<満期日>.csv

計算する指標(2013〜2026年の検証結果に基づく):
  - 1〜2限月コンタンゴ率: IGの調整金との相関0.92。満期の前日に次の限月へ切り替える
  - 推定調整金: 比率 ≒ 0.53 + 3.48 × コンタンゴ率(%)、1lot/日の円 ≒ 比率 × 0.22
  - S&P500の50日線乖離、VIX − 実現ボラ(20日)、VIX9D÷VIX
  - ①地合いシグナル、②利確ゾーン判定、100lotあたりのストレステスト
"""

import csv
import io
import json
import os
import sys
import math
import urllib.request
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNALS_PATH = os.path.join(BASE_DIR, "signals.json")

IDX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv"
VX_URL = "https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_{d}.csv"

FX_YEN = 155.0           # 1pt=1ドルの円換算(ストレステスト用の概算)
ADMIN_YEN_PER_LOT = 0.22  # Admin Fee 1lot/日(2025/7〜2026/9の実績でほぼ一定)

# ②利確判定: VIX水準ごとの今後20営業日の平均(2013〜2026年)
#   down = さらに下がった幅の平均(残りの利幅), up = 上に振れた幅の平均(リスク)
EXIT_TABLE = [
    (0, 13, 0.7, 4.5),
    (13, 15, 1.2, 4.7),
    (15, 17, 1.9, 4.7),
    (17, 20, 2.9, 4.8),
    (20, 25, 4.3, 4.6),
    (25, 99, 6.5, 4.4),
]


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 vix-dashboard"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8-sig")


def load_index(sym, fetch=http_get):
    """{date: close} を返す。CSVの最終列を終値とみなす。"""
    out = {}
    for row in csv.reader(io.StringIO(fetch(IDX_URL.format(sym=sym)))):
        if not row or row[0] == "DATE":
            continue
        try:
            d = datetime.strptime(row[0], "%m/%d/%Y").date()
            out[d] = float(row[-1])
        except ValueError:
            continue
    return out


def vix_expiry_candidate(year, month):
    """月次VIX満期の目安: 翌月の第3金曜の30日前(祝日は前後にずれるので取得時に探す)。"""
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    d = date(ny, nm, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d + timedelta(days=14) - timedelta(days=30)


def load_vx_contracts(today, n_months=4, fetch=http_get):
    """今日以降の満期を持つ限月を最大n_months本読み込み、[(満期日, {date: settle})] を返す。"""
    contracts = []
    y, m = today.year, today.month
    tries = 0
    while len(contracts) < n_months and tries < n_months + 3:
        tries += 1
        cand = vix_expiry_candidate(y, m)
        for off in (0, -1, 1, -2):
            d = cand + timedelta(days=off)
            if d < today - timedelta(days=1):
                break
            try:
                txt = fetch(VX_URL.format(d=d.isoformat()))
            except Exception:
                continue
            if not txt.startswith("Trade Date"):
                continue
            settles = {}
            for row in csv.DictReader(io.StringIO(txt)):
                try:
                    s = float(row["Settle"])
                    if s > 0:
                        settles[datetime.strptime(row["Trade Date"], "%Y-%m-%d").date()] = s
                except (ValueError, KeyError):
                    continue
            contracts.append((d, settles))
            break
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return sorted(contracts, key=lambda c: c[0])


def front_two(contracts, t):
    """満期の前日に次の限月へ切り替える(IGの調整金と最もよく一致した設定)。"""
    live = [c for c in contracts if (c[0] - t).days > 1]
    if len(live) < 2:
        return None
    (e1, s1), (e2, s2) = live[0], live[1]
    if t in s1 and t in s2:
        return {"exp1": e1, "f1": s1[t], "exp2": e2, "f2": s2[t]}
    return None


def realized_vol(closes, n=20):
    if len(closes) < n + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))]
    mu = sum(rets) / n
    var = sum((r - mu) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var * 252) * 100


def build_signals(fetch=http_get, today=None):
    today = today or date.today()
    vix = load_index("VIX", fetch)
    vix3m = load_index("VIX3M", fetch)
    vix9d = load_index("VIX9D", fetch)
    skew = load_index("SKEW", fetch)
    spx = load_index("SPX", fetch)

    common = sorted(set(vix) & set(vix3m) & set(skew) & set(spx))
    t = common[-1]
    spx_dates = [d for d in sorted(spx) if d <= t]
    spx_closes = [spx[d] for d in spx_dates]

    ma50 = sum(spx_closes[-50:]) / 50
    rv20 = realized_vol(spx_closes)
    v = vix[t]

    contracts = load_vx_contracts(t, fetch=fetch)
    ft = front_two(contracts, t)
    c12 = (ft["f2"] / ft["f1"] - 1) * 100 if ft else None

    est_ratio = 0.53 + 3.48 * c12 if c12 is not None else None
    est_yen = est_ratio * ADMIN_YEN_PER_LOT if est_ratio is not None else None

    x50 = (spx[t] / ma50 - 1) * 100
    vrp = v - rv20 if rv20 is not None else None
    n9 = vix9d[t] / v if t in vix9d else None

    # 調整金ゾーン
    if c12 is None:
        carry_zone = ("データなし", "warn")
    elif c12 >= 8.5:
        carry_zone = ("保有OK(コンタンゴ8.5%以上)", "normal")
    elif c12 >= 5.6:
        carry_zone = ("様子見(5.6〜8.5%)", "warn")
    elif c12 > 0:
        carry_zone = ("新規・積み増し見送り(5.6%未満)", "danger")
    else:
        carry_zone = ("ノーポジ推奨(バックワーデーション)", "danger")

    # ①地合いシグナル
    regime = [
        {
            "name": "株は好調なのにカーブが平ら",
            "rule": "S&P500が50日線+2%超、コンタンゴ5%未満、VIX18未満",
            "hit": bool(c12 is not None and x50 > 2 and c12 < 5 and v < 18),
            "stat": "20営業日以内にVIX20超え 48%(平時32%)",
        },
        {
            "name": "VIXが低いのにオプションが割高",
            "rule": "VIX15未満、VIX − 実現ボラ20日 > 6pt",
            "hit": bool(vrp is not None and v < 15 and vrp > 6),
            "stat": "2週間以内に+30% 30%(平時18%)",
        },
        {
            "name": "静けさが極端(参考・該当日少)",
            "rule": "VIX15未満、SKEW145超、VIX9D÷VIX 0.86未満",
            "hit": bool(n9 is not None and v < 15 and skew[t] > 145 and n9 < 0.86),
            "stat": "20営業日以内にVIX20超え 41%",
        },
        {
            "name": "カーブが平らでVIXも低め",
            "rule": "コンタンゴ5%未満、VIX17未満",
            "hit": bool(c12 is not None and c12 < 5 and v < 17),
            "stat": "20営業日以内にVIX20超え 35%(平時28%)",
        },
    ]

    # ②利確ゾーン
    row = next(r for r in EXIT_TABLE if r[0] <= v < r[1])
    carry_pt_20d = (est_yen * 28 / FX_YEN) if est_yen is not None else 0.0
    remaining = row[2] + max(carry_pt_20d, 0)
    exit_zone = bool(15 <= v < 17 and c12 is not None and c12 < 5) or v < 15
    exit_info = {
        "vix_band": f"{row[0]}〜{row[1] if row[1] < 99 else ''}",
        "down_pt": row[2],
        "up_pt": row[3],
        "carry_pt_20d": round(carry_pt_20d, 2),
        "remaining_pt": round(remaining, 2),
        "take_profit": exit_zone,
    }

    # 100lotあたりのストレステスト(現在値で売った場合の含み損)
    stress = [
        {"to": lvl, "loss_yen": round(-(lvl - v) * 100 * FX_YEN)}
        for lvl in (20, 25, 30, 35, 40)
        if lvl > v
    ]

    def r(x, n=2):
        return None if x is None else round(x, n)

    return {
        "date": t.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "closes": {
            "vix": v,
            "vix3m": vix3m[t],
            "vix9d": vix9d.get(t),
            "skew": skew[t],
            "spx": spx[t],
        },
        "futures": None if not ft else {
            "exp1": ft["exp1"].isoformat(),
            "f1": ft["f1"],
            "exp2": ft["exp2"].isoformat(),
            "f2": ft["f2"],
            "roll_date": (ft["exp1"] - timedelta(days=1)).isoformat(),
        },
        "contango_12_pct": r(c12),
        "est_ratio": r(est_ratio, 1),
        "est_yen_per_lot_day": r(est_yen),
        "carry_zone": {"label": carry_zone[0], "class": carry_zone[1]},
        "spx_vs_ma50_pct": r(x50),
        "rv20": r(rv20),
        "vix_minus_rv": r(vrp),
        "vix9d_over_vix": r(n9, 3),
        "regime_signals": regime,
        "exit": exit_info,
        "stress_per_100lot": stress,
    }


def main():
    sig = build_signals()
    with open(SIGNALS_PATH, "w", encoding="utf-8") as f:
        json.dump(sig, f, ensure_ascii=False, indent=2)
    c = sig["closes"]
    line = f"{c['vix']} {c['vix3m']} {c['skew']} {sig['date']}"
    if len(sys.argv) >= 2 and sys.argv[1] == "--closes":
        print(line)
    else:
        print(f"signals.json を更新しました({sig['date']})")
        print(f"コンタンゴ1-2限月: {sig['contango_12_pct']}% / 推定調整金: {sig['est_yen_per_lot_day']}円/lot/日")
        print(f"update_dashboard.py 用の終値: {line}")


if __name__ == "__main__":
    main()
