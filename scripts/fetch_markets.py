#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
急落タイプ判定・NISAで拾う候補・ドル円/BTC 用の市場データ取得(第1段階, 2026-10追加)

使い方:
  python3 fetch_markets.py      # markets.json を生成
  (fetch_signals.py の main() からも自動で呼ばれる。失敗してもVIX側の更新は止めない)

取得元: Yahoo Finance の chart API(無料・認証不要)
  ^GSPC S&P500 / ^NDX ナスダック100 / ^SOX 半導体(SOX) / ^NYFANG FANG+ /
  200A.T 日経半導体株(NEXT FUNDS 日経半導体ETF、指数の代わり) / BTC-USD / JPY=X ドル円
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKETS_PATH = os.path.join(BASE_DIR, "markets.json")

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d"

SYMBOLS = {
    "spx": ("^GSPC", "S&P500"),
    "ndx": ("^NDX", "ナスダック100"),
    "sox": ("^SOX", "半導体(SOX)"),
    "fang": ("^NYFANG", "FANG+"),
    "nsemi": ("200A.T", "日経半導体"),
    "btc": ("BTC-USD", "ビットコイン"),
    "usdjpy": ("JPY=X", "ドル円"),
}


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 vix-dashboard"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def load_series(sym, fetch=http_get):
    """[(date_iso, close)] を古い順で返す。"""
    data = json.loads(fetch(CHART_URL.format(sym=urllib.request.quote(sym))))
    res = data["chart"]["result"][0]
    ts = res.get("timestamp") or []
    closes = res["indicators"]["quote"][0].get("close") or []
    out = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
        if out and out[-1][0] == d:
            out[-1] = (d, c)
        else:
            out.append((d, c))
    return out


def pct(a, b):
    return None if (a is None or b in (None, 0)) else round((a / b - 1) * 100, 2)


def summarize(series):
    if len(series) < 22:
        return None
    closes = [c for _, c in series]
    last = closes[-1]
    hi = max(closes[-252:])
    return {
        "date": series[-1][0],
        "close": round(last, 2),
        "chg_1d": pct(last, closes[-2]),
        "chg_5d": pct(last, closes[-6]),
        "chg_20d": pct(last, closes[-21]),
        "dd_52w": pct(last, hi),  # 52週高値からの下落率(マイナス)
        "high_52w": round(hi, 2),
    }


def classify(m):
    """急落の種類を判定する。VIXは signals 側で見るので、ここは株価の動きだけで判定。"""
    spx, ndx, sox = m.get("spx"), m.get("ndx"), m.get("sox")
    if not (spx and sox):
        return {"type": "unknown", "label": "判定不可(データ取得失敗)", "class": "warn"}
    s5, s_dd = spx["chg_5d"], spx["dd_52w"]
    x5, x_dd = sox["chg_5d"], sox["dd_52w"]
    n5 = ndx["chg_5d"] if ndx else None
    if s5 <= -4 or s_dd <= -8:
        return {"type": "broad", "label": "全体の急落", "class": "danger"}
    if (x5 <= -8 or x_dd <= -15) and s5 > -3:
        return {"type": "semis", "label": "半導体だけの急落", "class": "danger"}
    if n5 is not None and n5 <= -5 and s5 > -3:
        return {"type": "tech", "label": "ハイテクの急落", "class": "warn"}
    if x5 <= -5 or s5 <= -2.5:
        return {"type": "watch", "label": "下げ始め(要観察)", "class": "warn"}
    return {"type": "calm", "label": "平常", "class": "normal"}


def build_markets(fetch=http_get):
    m, errors = {}, {}
    for key, (sym, name) in SYMBOLS.items():
        try:
            s = summarize(load_series(sym, fetch))
            if s:
                s["name"] = name
                s["symbol"] = sym
                m[key] = s
        except Exception as e:  # 1銘柄の失敗で全体を止めない
            errors[key] = str(e)[:120]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "markets": m,
        "crash_type": classify(m),
        "errors": errors,
    }


def main():
    out = build_markets()
    with open(MARKETS_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    ct = out["crash_type"]["label"]
    print(f"markets.json を更新しました(急落判定: {ct})")
    if out["errors"]:
        print("取得失敗: " + ", ".join(out["errors"]))


if __name__ == "__main__":
    main()
