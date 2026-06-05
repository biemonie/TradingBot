import requests
import json
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
import math

# ============================================================
#  ✏️  YOUR 3 DETAILS
# ============================================================
YOUR_GMAIL           = "ebubeonianwah@gmail.com"
YOUR_APP_PASSWORD    = "twya tjbj hdez jjuk"
YOUR_TWELVEDATA_KEY  = "4ab2a4a5f9844f709a0baa38957292a6"

# ============================================================
#  SETTINGS
# ============================================================
CONFIG = {
    "sender_email":          YOUR_GMAIL,
    "sender_password":       YOUR_APP_PASSWORD,
    "receiver_email":        YOUR_GMAIL,
    "twelvedata_api_key":    YOUR_TWELVEDATA_KEY,
    "risk_percent":          1.0,
    "account_balance":       3000,
    "atr_sl_multiplier":     1.5,
    "rr_ratio":              2.0,
    "scan_interval_minutes": 15,
    "lookback_bars":         100,
}

INSTRUMENTS = {
    "US30":   "DJI",
    "GER40":  "DAX",
    "NAS100": "NDX",
    "GOLD":   "XAU/USD",
}

FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_NAMES  = ["0%", "23.6%", "38.2%", "50%", "61.8% Golden Zone", "78.6%", "100%"]

# ============================================================
#  PURE PYTHON MATH HELPERS (no numpy/pandas needed)
# ============================================================
def mean(values):
    return sum(values) / len(values) if values else 0

def ema(values, period):
    k = 2.0 / (period + 1)
    result = [mean(values[:period])]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def calculate_atr_list(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1])
        )
        trs.append(tr)
    if len(trs) < period:
        return mean(trs) if trs else 0
    return mean(trs[-period:])

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = mean(gains[-period:])
    avg_loss = mean(losses[-period:])
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# ============================================================
#  DATA FETCHING
# ============================================================
def get_ohlcv(symbol, interval="15min", bars=100):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol":     symbol,
        "interval":   interval,
        "outputsize": bars,
        "apikey":     CONFIG["twelvedata_api_key"],
        "format":     "JSON",
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if "values" not in data:
            print(f"[{symbol}] API error: {data.get('message','Unknown')}")
            return None
        values = sorted(data["values"], key=lambda x: x["datetime"])
        opens  = [float(v["open"])  for v in values]
        highs  = [float(v["high"])  for v in values]
        lows   = [float(v["low"])   for v in values]
        closes = [float(v["close"]) for v in values]
        return {"opens": opens, "highs": highs, "lows": lows, "closes": closes}
    except Exception as e:
        print(f"[{symbol}] Fetch error: {e}")
        return None

# ============================================================
#  TECHNICAL ANALYSIS
# ============================================================
def get_trend(closes):
    if len(closes) < 50:
        return {"trend": "UNKNOWN", "strength": "WEAK", "ema50": 0, "ema200": 0}
    ema50_vals  = ema(closes, 50)
    ema200_vals = ema(closes, min(200, len(closes)))
    e50  = ema50_vals[-1]
    e200 = ema200_vals[-1]
    c    = closes[-1]
    if c > e50 > e200:
        return {"trend": "BULLISH", "strength": "STRONG",   "ema50": round(e50,2), "ema200": round(e200,2)}
    elif c > e200:
        return {"trend": "BULLISH", "strength": "MODERATE", "ema50": round(e50,2), "ema200": round(e200,2)}
    elif c < e50 < e200:
        return {"trend": "BEARISH", "strength": "STRONG",   "ema50": round(e50,2), "ema200": round(e200,2)}
    elif c < e200:
        return {"trend": "BEARISH", "strength": "MODERATE", "ema50": round(e50,2), "ema200": round(e200,2)}
    return {"trend": "RANGING", "strength": "WEAK", "ema50": round(e50,2), "ema200": round(e200,2)}

def find_sr(highs, lows, closes, sensitivity=0.002):
    current = closes[-1]
    resistance, support = [], []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            resistance.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            support.append(lows[i])
    def cluster(levels):
        if not levels:
            return []
        levels = sorted(set(levels))
        out = [levels[0]]
        for l in levels[1:]:
            if abs(l - out[-1]) / out[-1] > sensitivity:
                out.append(l)
        return out
    resistance = cluster(resistance)
    support    = cluster(support)
    near_res = sorted([r for r in resistance if r > current])[:3]
    near_sup = sorted([s for s in support if s < current], reverse=True)[:3]
    return {"nearest_resistance": near_res, "nearest_support": near_sup}

def find_sd_zones(opens, closes):
    avg_size = mean([abs(c - o) for c, o in zip(closes, opens)])
    demand, supply = [], []
    current = closes[-1]
    for i in range(len(closes)):
        size = abs(closes[i] - opens[i])
        if size > avg_size * 1.5:
            top = max(opens[i], closes[i])
            bot = min(opens[i], closes[i])
            if closes[i] > opens[i]:
                demand.append({"top": top, "bottom": bot})
            else:
                supply.append({"top": top, "bottom": bot})
    in_demand = any(z["bottom"] < current < z["top"] * 1.02 for z in demand)
    in_supply = any(z["bottom"] * 0.98 < current < z["top"] for z in supply)
    return {"price_in_demand": in_demand, "price_in_supply": in_supply}

def calc_fibonacci(highs, lows, closes):
    lookback = min(50, len(closes))
    recent_h = highs[-lookback:]
    recent_l = lows[-lookback:]
    swing_high = max(recent_h)
    swing_low  = min(recent_l)
    hi_idx     = recent_h.index(swing_high)
    lo_idx     = recent_l.index(swing_low)
    current    = closes[-1]
    price_range = swing_high - swing_low
    bullish = lo_idx < hi_idx
    levels = {}
    for lvl, name in zip(FIB_LEVELS, FIB_NAMES):
        if bullish:
            levels[name] = round(swing_high - price_range * lvl, 4)
        else:
            levels[name] = round(swing_low + price_range * lvl, 4)
    golden = levels.get("61.8% Golden Zone", 0)
    fib382 = levels.get("38.2%", 0)
    fib50  = levels.get("50%", 0)
    in_golden   = abs(current - golden) / max(current, 1) < 0.003
    near_382_50 = (abs(current - fib382) / max(current, 1) < 0.003 or
                   abs(current - fib50)  / max(current, 1) < 0.003)
    return {
        "swing_high":    round(swing_high, 4),
        "swing_low":     round(swing_low,  4),
        "levels":        levels,
        "in_golden_zone": in_golden,
        "near_382_or_50": near_382_50,
        "trend_direction": "BULLISH" if bullish else "BEARISH",
    }

# ============================================================
#  SIGNAL GENERATION
# ============================================================
def generate_signal(name, symbol):
    print(f"\n🔍 Analysing {name}...")
    data = get_ohlcv(symbol)
    if not data or len(data["closes"]) < 50:
        return None

    opens  = data["opens"]
    highs  = data["highs"]
    lows   = data["lows"]
    closes = data["closes"]
    price  = closes[-1]

    trend  = get_trend(closes)
    sr     = find_sr(highs, lows, closes)
    sd     = find_sd_zones(opens, closes)
    fib    = calc_fibonacci(highs, lows, closes)
    atr    = calculate_atr_list(highs, lows, closes)
    rsi    = calculate_rsi(closes)

    signal_type = None
    reasons     = []
    confidence  = 0

    if trend["trend"] == "BULLISH":
        confidence += 25
        reasons.append(f"✅ Bullish trend — EMA50 ({trend['ema50']}) above EMA200 ({trend['ema200']})")
        if trend["strength"] == "STRONG":
            confidence += 10
            reasons.append("✅ Strong trend — price above both EMAs")
        if sr["nearest_support"]:
            sup = sr["nearest_support"][0]
            if abs(price - sup) / price < 0.005:
                confidence += 20
                reasons.append(f"✅ Price near support: {round(sup,2)}")
        if sd["price_in_demand"]:
            confidence += 20
            reasons.append("✅ Price inside demand zone")
        if fib["in_golden_zone"] and fib["trend_direction"] == "BULLISH":
            confidence += 20
            reasons.append("✅ Price at Fibonacci 61.8% Golden Zone!")
        elif fib["near_382_or_50"]:
            confidence += 12
            reasons.append("✅ Price near Fibonacci 38.2%/50%")
        if rsi < 45:
            confidence += 15
            reasons.append(f"✅ RSI at {round(rsi,1)} — room to move up")
        if confidence >= 55:
            signal_type = "BUY"

    elif trend["trend"] == "BEARISH":
        confidence += 25
        reasons.append(f"✅ Bearish trend — EMA50 ({trend['ema50']}) below EMA200 ({trend['ema200']})")
        if trend["strength"] == "STRONG":
            confidence += 10
            reasons.append("✅ Strong trend — price below both EMAs")
        if sr["nearest_resistance"]:
            res = sr["nearest_resistance"][0]
            if abs(price - res) / price < 0.005:
                confidence += 20
                reasons.append(f"✅ Price near resistance: {round(res,2)}")
        if sd["price_in_supply"]:
            confidence += 20
            reasons.append("✅ Price inside supply zone")
        if fib["in_golden_zone"] and fib["trend_direction"] == "BEARISH":
            confidence += 20
            reasons.append("✅ Price at Fibonacci 61.8% Golden Zone!")
        elif fib["near_382_or_50"]:
            confidence += 12
            reasons.append("✅ Price near Fibonacci 38.2%/50%")
        if rsi > 55:
            confidence += 15
            reasons.append(f"✅ RSI at {round(rsi,1)} — room to move down")
        if confidence >= 55:
            signal_type = "SELL"

    if not signal_type:
        print(f"   No signal ({confidence}% confidence)")
        return None

    sl_dist = atr * CONFIG["atr_sl_multiplier"]
    tp_dist = sl_dist * CONFIG["rr_ratio"]
    stop_loss   = round(price - sl_dist, 4) if signal_type == "BUY" else round(price + sl_dist, 4)
    take_profit = round(price + tp_dist, 4) if signal_type == "BUY" else round(price - tp_dist, 4)
    risk_amt    = CONFIG["account_balance"] * CONFIG["risk_percent"] / 100
    lots        = round(risk_amt / max(sl_dist, 0.0001), 2)

    return {
        "instrument":  name,
        "signal":      signal_type,
        "price":       round(price, 4),
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "confidence":  confidence,
        "trend":       trend,
        "rsi":         round(rsi, 1),
        "atr":         round(atr, 4),
        "sr":          sr,
        "sd":          sd,
        "fib":         fib,
        "reasons":     reasons,
        "lots":        lots,
        "risk_amount": round(risk_amt, 2),
        "timestamp":   datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }

# ============================================================
#  EMAIL
# ============================================================
def send_email(signals):
    subject = f"🤖 Trading Bot — {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}"
    if signals:
        subject += f" | {', '.join([s['instrument'] + ' ' + s['signal'] for s in signals])}"

    html = "<html><body style='font-family:Arial,sans-serif;background:#0d1117;color:#e6edf3;padding:20px;'>"
    html += "<h2 style='color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px;'>🤖 Trading Bot Report</h2>"

    if signals:
        for s in signals:
            color = "#3fb950" if s["signal"] == "BUY" else "#f85149"
            bg    = "#0d2818" if s["signal"] == "BUY" else "#2d0f0f"
            emoji = "🟢" if s["signal"] == "BUY" else "🔴"
            golden = " ⭐ GOLDEN ZONE" if s["fib"]["in_golden_zone"] else ""

            fib_rows = ""
            for fname, flevel in s["fib"]["levels"].items():
                is_gold = "61.8" in fname
                style   = "color:#ffd700;font-weight:bold;" if is_gold else "color:#e6edf3;"
                fib_rows += f"<tr><td style='padding:4px 8px;color:#8b949e;'>{fname}</td><td style='padding:4px 8px;{style}'>{flevel}</td></tr>"

            html += f"""
            <div style='background:{bg};border:1px solid {color};border-radius:8px;padding:20px;margin-bottom:20px;'>
                <h3 style='color:{color};margin:0 0 5px 0;'>{emoji} {s['instrument']} — {s['signal']}{golden}</h3>
                <p style='color:#8b949e;margin:0 0 15px 0;font-size:13px;'>{s['timestamp']}</p>
                <table style='width:100%;border-collapse:collapse;margin-bottom:15px;'>
                    <tr><td style='padding:6px;color:#8b949e;'>Entry Price</td><td style='padding:6px;color:#e6edf3;font-weight:bold;'>{s['price']}</td>
                        <td style='padding:6px;color:#8b949e;'>Trend</td><td style='padding:6px;color:{color};'>{s['trend']['trend']} ({s['trend']['strength']})</td></tr>
                    <tr><td style='padding:6px;color:#8b949e;'>Stop Loss 🛑</td><td style='padding:6px;color:#f85149;font-weight:bold;'>{s['stop_loss']}</td>
                        <td style='padding:6px;color:#8b949e;'>RSI</td><td style='padding:6px;color:#e6edf3;'>{s['rsi']}</td></tr>
                    <tr><td style='padding:6px;color:#8b949e;'>Take Profit 🎯</td><td style='padding:6px;color:#3fb950;font-weight:bold;'>{s['take_profit']}</td>
                        <td style='padding:6px;color:#8b949e;'>ATR</td><td style='padding:6px;color:#e6edf3;'>{s['atr']}</td></tr>
                    <tr><td style='padding:6px;color:#8b949e;'>Suggested Lots</td><td style='padding:6px;color:#e6edf3;'>{s['lots']}</td>
                        <td style='padding:6px;color:#8b949e;'>Risk Amount</td><td style='padding:6px;color:#ffa657;'>${s['risk_amount']}</td></tr>
                    <tr><td style='padding:6px;color:#8b949e;'>Confidence</td><td colspan='3' style='padding:6px;color:{color};font-weight:bold;'>{s['confidence']}%</td></tr>
                </table>
                <div style='background:#0d1117;padding:12px;border-radius:6px;margin-bottom:10px;'>
                    <strong style='color:#ffd700;'>📐 Fibonacci (Swing: {s['fib']['swing_low']} → {s['fib']['swing_high']})</strong>
                    <table style='width:100%;margin-top:8px;border-collapse:collapse;font-size:13px;'>
                        <tr><th style='padding:4px 8px;color:#58a6ff;text-align:left;'>Level</th><th style='padding:4px 8px;color:#58a6ff;text-align:left;'>Price</th></tr>
                        {fib_rows}
                    </table>
                </div>
                <div style='background:#0d1117;padding:12px;border-radius:6px;margin-bottom:10px;'>
                    <strong style='color:#58a6ff;'>📊 Key Levels</strong><br><br>
                    <span style='color:#3fb950;'>Support: </span>{', '.join([str(round(x,2)) for x in s['sr']['nearest_support']]) or 'None'}<br>
                    <span style='color:#f85149;'>Resistance: </span>{', '.join([str(round(x,2)) for x in s['sr']['nearest_resistance']]) or 'None'}<br>
                    <span style='color:#ffa657;'>In Demand Zone: {'Yes ✅' if s['sd']['price_in_demand'] else 'No'}</span><br>
                    <span style='color:#ffa657;'>In Supply Zone: {'Yes ✅' if s['sd']['price_in_supply'] else 'No'}</span>
                </div>
                <div style='background:#0d1117;padding:12px;border-radius:6px;margin-bottom:10px;'>
                    <strong style='color:#58a6ff;'>🧠 Why this signal:</strong><br><br>
                    {'<br>'.join(s['reasons'])}
                </div>
                <div style='background:#1a1f00;border-left:3px solid #ffa657;padding:12px;border-radius:4px;'>
                    <strong style='color:#ffa657;'>⚠️ Always confirm on your MT5 chart before entering! Never skip your stop loss.</strong>
                </div>
            </div>"""
    else:
        html += "<div style='background:#1c2128;border-radius:8px;padding:20px;text-align:center;'><p style='color:#8b949e;'>No high-confidence signals this scan. Being patient is part of trading. 🎯</p></div>"

    html += "<p style='color:#30363d;font-size:11px;margin-top:20px;'>Automated analysis only. Not financial advice. Always manage your risk.</p></body></html>"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = CONFIG["sender_email"]
        msg["To"]      = CONFIG["receiver_email"]
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(CONFIG["sender_email"], CONFIG["sender_password"])
            server.sendmail(CONFIG["sender_email"], CONFIG["receiver_email"], msg.as_string())
        print(f"✅ Email sent!")
    except Exception as e:
        print(f"❌ Email error: {e}")

# ============================================================
#  MAIN LOOP
# ============================================================
def run_bot():
    print("=" * 55)
    print("  🤖 TRADING BOT — Started!")
    print(f"  Instruments: {', '.join(INSTRUMENTS.keys())}")
    print(f"  Scanning every {CONFIG['scan_interval_minutes']} minutes")
    print(f"  Alerts → {CONFIG['receiver_email']}")
    print("=" * 55)

    while True:
        print(f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} — Scanning...")
        signals = []
        for name, symbol in INSTRUMENTS.items():
            try:
                sig = generate_signal(name, symbol)
                if sig:
                    signals.append(sig)
                    print(f"   🎯 {name}: {sig['signal']} | {sig['confidence']}% confidence")
            except Exception as e:
                print(f"   ❌ {name} error: {e}")

        if signals:
            print(f"\n📧 Sending email with {len(signals)} signal(s)...")
            send_email(signals)
        else:
            print("💤 No signals. Waiting...")

        next_scan = (datetime.utcnow() + timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%H:%M UTC")
        print(f"⏳ Next scan at {next_scan}")
        time.sleep(CONFIG["scan_interval_minutes"] * 60)

if __name__ == "__main__":
    run_bot()
