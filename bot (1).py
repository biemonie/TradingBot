import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
import os

# ============================================================
#  ✏️  FILL IN THESE 3 THINGS — THEN YOU'RE DONE!
# ============================================================

YOUR_GMAIL           = "ebubeonianwah@gmail.com"
YOUR_APP_PASSWORD    = "twya tjbj hdez jjuk"
YOUR_TWELVEDATA_KEY  = "4ab2a4a5f9844f709a0baa38957292a6"

# ============================================================
#  SETTINGS — You can leave these as they are
# ============================================================
CONFIG = {
    "sender_email":           YOUR_GMAIL,
    "sender_password":        YOUR_APP_PASSWORD,
    "receiver_email":         YOUR_GMAIL,
    "twelvedata_api_key":     YOUR_TWELVEDATA_KEY,
    "risk_percent":           1.0,       # % of account to risk per trade
    "account_balance":        3000,      # Demo account balance
    "atr_sl_multiplier":      1.5,       # Stop loss = 1.5x ATR
    "rr_ratio":               2.0,       # Risk:Reward (2 = TP is 2x your SL)
    "scan_interval_minutes":  15,        # Scan every 15 minutes
    "lookback_bars":          100,       # Candles to analyse
    "sr_sensitivity":         0.002,     # S&R zone sensitivity
}

INSTRUMENTS = {
    "US30":   "DJI",
    "GER40":  "DAX",
    "NAS100": "NDX",
    "GOLD":   "XAU/USD",
}

FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_NAMES  = ["0% (Swing High/Low)", "23.6%", "38.2%", "50%",
              "61.8% (Golden Zone)", "78.6%", "100% (Swing High/Low)"]

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
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        for col in ["open","high","low","close","volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col])
        return df
    except Exception as e:
        print(f"[{symbol}] Fetch error: {e}")
        return None

# ============================================================
#  TECHNICAL INDICATORS
# ============================================================
def calculate_emas(df):
    df["ema50"]  = df["close"].ewm(span=50,  adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    return df

def calculate_atr(df, period=14):
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"]  - df["close"].shift(1))
        )
    )
    df["atr"] = df["tr"].rolling(period).mean()
    return df

def calculate_rsi(df, period=14):
    delta = df["close"].diff()
    gain  = delta.where(delta > 0, 0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs    = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))
    return df

# ============================================================
#  FIBONACCI RETRACEMENT
# ============================================================
def calculate_fibonacci(df):
    """
    Find the most recent significant swing high and swing low,
    then calculate all Fibonacci retracement levels between them.
    Also identifies which Fib zone price is currently in.
    """
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values

    # Find swing high (highest point in last 50 bars)
    lookback = min(50, len(df))
    recent_highs = highs[-lookback:]
    recent_lows  = lows[-lookback:]

    swing_high     = float(np.max(recent_highs))
    swing_low      = float(np.min(recent_lows))
    swing_high_idx = int(np.argmax(recent_highs))
    swing_low_idx  = int(np.argmin(recent_lows))
    current_price  = float(closes[-1])
    price_range    = swing_high - swing_low

    # Bullish retracement: price pulled back FROM high, looking for bounce UP
    # Bearish retracement: price pulled back FROM low, looking for drop DOWN
    trend_is_bullish = swing_low_idx < swing_high_idx  # Low came before high

    fib_levels = {}
    for level, name in zip(FIB_LEVELS, FIB_NAMES):
        if trend_is_bullish:
            # Retracement levels going DOWN from swing high
            price_at_level = swing_high - (price_range * level)
        else:
            # Retracement levels going UP from swing low
            price_at_level = swing_low + (price_range * level)
        fib_levels[name] = round(price_at_level, 4)

    # Find nearest Fib level to current price
    nearest_fib      = None
    nearest_fib_dist = float("inf")
    for name, lvl in fib_levels.items():
        dist = abs(current_price - lvl)
        if dist < nearest_fib_dist:
            nearest_fib_dist = dist
            nearest_fib      = (name, lvl)

    # Check if price is in the "Golden Zone" (61.8% area — strongest fib level)
    golden_zone_level = fib_levels.get("61.8% (Golden Zone)", 0)
    in_golden_zone    = abs(current_price - golden_zone_level) / current_price < 0.003

    # Check if price is near 38.2% or 50% (also strong)
    fib_382 = fib_levels.get("38.2%", 0)
    fib_50  = fib_levels.get("50%",   0)
    near_382_or_50 = (abs(current_price - fib_382) / current_price < 0.003 or
                      abs(current_price - fib_50)  / current_price < 0.003)

    return {
        "swing_high":       round(swing_high, 4),
        "swing_low":        round(swing_low,  4),
        "trend_direction":  "BULLISH" if trend_is_bullish else "BEARISH",
        "levels":           fib_levels,
        "nearest_level":    nearest_fib,
        "in_golden_zone":   in_golden_zone,
        "near_382_or_50":   near_382_or_50,
        "price_range":      round(price_range, 4),
    }

# ============================================================
#  SUPPORT & RESISTANCE
# ============================================================
def find_support_resistance(df, sensitivity=0.002):
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values
    current_price = closes[-1]

    resistance_levels = []
    support_levels    = []

    for i in range(2, len(df) - 2):
        if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and
                highs[i] > highs[i+1] and highs[i] > highs[i+2]):
            resistance_levels.append(highs[i])
        if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and
                lows[i] < lows[i+1] and lows[i] < lows[i+2]):
            support_levels.append(lows[i])

    def cluster(levels, sens):
        if not levels:
            return []
        levels = sorted(set(levels))
        out = [levels[0]]
        for lvl in levels[1:]:
            if abs(lvl - out[-1]) / out[-1] > sens:
                out.append(lvl)
        return out

    resistance_levels = cluster(resistance_levels, sensitivity)
    support_levels    = cluster(support_levels,    sensitivity)

    nearest_res = sorted([r for r in resistance_levels if r > current_price])[:3]
    nearest_sup = sorted([s for s in support_levels    if s < current_price], reverse=True)[:3]

    return {
        "current_price":      current_price,
        "nearest_resistance": nearest_res,
        "nearest_support":    nearest_sup,
    }

# ============================================================
#  SUPPLY & DEMAND ZONES
# ============================================================
def find_supply_demand_zones(df):
    closes = df["close"].values
    opens  = df["open"].values
    highs  = df["high"].values
    lows   = df["low"].values
    avg_size = np.mean(abs(closes - opens))
    supply_zones  = []
    demand_zones  = []
    current_price = closes[-1]

    for i in range(1, len(df) - 1):
        size = abs(closes[i] - opens[i])
        if closes[i] > opens[i] and size > avg_size * 1.5:
            demand_zones.append({"top": max(opens[i], closes[i]),
                                  "bottom": min(opens[i], closes[i])})
        if closes[i] < opens[i] and size > avg_size * 1.5:
            supply_zones.append({"top": max(opens[i], closes[i]),
                                  "bottom": min(opens[i], closes[i])})

    in_demand = any(z["bottom"] < current_price < z["top"] * 1.02 for z in demand_zones)
    in_supply = any(z["bottom"] * 0.98 < current_price < z["top"] for z in supply_zones)

    return {
        "price_in_demand": in_demand,
        "price_in_supply": in_supply,
        "demand_zones":    demand_zones[-5:],
        "supply_zones":    supply_zones[-5:],
    }

# ============================================================
#  TREND
# ============================================================
def determine_trend(df):
    last   = df.iloc[-1]
    ema50  = last["ema50"]
    ema200 = last["ema200"]
    close  = last["close"]

    if close > ema50 > ema200:
        return {"trend": "BULLISH", "strength": "STRONG",   "ema50": round(ema50,4), "ema200": round(ema200,4)}
    elif close > ema200:
        return {"trend": "BULLISH", "strength": "MODERATE", "ema50": round(ema50,4), "ema200": round(ema200,4)}
    elif close < ema50 < ema200:
        return {"trend": "BEARISH", "strength": "STRONG",   "ema50": round(ema50,4), "ema200": round(ema200,4)}
    elif close < ema200:
        return {"trend": "BEARISH", "strength": "MODERATE", "ema50": round(ema50,4), "ema200": round(ema200,4)}
    else:
        return {"trend": "RANGING", "strength": "WEAK",     "ema50": round(ema50,4), "ema200": round(ema200,4)}

# ============================================================
#  POSITION SIZING
# ============================================================
def calculate_position_size(entry, stop_loss):
    risk_amount = CONFIG["account_balance"] * (CONFIG["risk_percent"] / 100)
    sl_distance = abs(entry - stop_loss)
    if sl_distance == 0:
        return {"lots": 0, "risk_amount": 0, "sl_distance": 0}
    lots = round(risk_amount / sl_distance, 2)
    return {"lots": lots, "risk_amount": round(risk_amount, 2), "sl_distance": round(sl_distance, 4)}

# ============================================================
#  SIGNAL GENERATION
# ============================================================
def generate_signal(name, symbol):
    print(f"\n🔍 Analysing {name} ({symbol})...")
    df = get_ohlcv(symbol)
    if df is None or len(df) < 50:
        return None

    df  = calculate_emas(df)
    df  = calculate_atr(df)
    df  = calculate_rsi(df)
    sr  = find_support_resistance(df, CONFIG["sr_sensitivity"])
    sd  = find_supply_demand_zones(df)
    fib = calculate_fibonacci(df)
    trend = determine_trend(df)

    last  = df.iloc[-1]
    price = sr["current_price"]
    atr   = last["atr"]
    rsi   = last["rsi"]

    signal_type = None
    reasons     = []
    confidence  = 0

    # ── BUY LOGIC ──────────────────────────────────────────
    if trend["trend"] == "BULLISH":
        confidence += 25
        reasons.append(f"✅ Bullish trend — EMA50 ({trend['ema50']}) above EMA200 ({trend['ema200']})")

        if trend["strength"] == "STRONG":
            confidence += 10
            reasons.append("✅ Strong trend — price also above both EMAs")

        if sr["nearest_support"]:
            sup = sr["nearest_support"][0]
            if abs(price - sup) / price < 0.005:
                confidence += 20
                reasons.append(f"✅ Price sitting on support level: {round(sup,4)}")

        if sd["price_in_demand"]:
            confidence += 20
            reasons.append("✅ Price inside a demand zone (institutional buying area)")

        if fib["in_golden_zone"] and fib["trend_direction"] == "BULLISH":
            confidence += 20
            reasons.append(f"✅ Price at Fibonacci 61.8% Golden Zone — strongest retracement level!")
        elif fib["near_382_or_50"] and fib["trend_direction"] == "BULLISH":
            confidence += 12
            reasons.append("✅ Price near Fibonacci 38.2% or 50% retracement")

        if rsi < 45:
            confidence += 15
            reasons.append(f"✅ RSI at {round(rsi,1)} — not overbought, room to move up")

        if confidence >= 55:
            signal_type = "BUY"

    # ── SELL LOGIC ─────────────────────────────────────────
    elif trend["trend"] == "BEARISH":
        confidence += 25
        reasons.append(f"✅ Bearish trend — EMA50 ({trend['ema50']}) below EMA200 ({trend['ema200']})")

        if trend["strength"] == "STRONG":
            confidence += 10
            reasons.append("✅ Strong trend — price also below both EMAs")

        if sr["nearest_resistance"]:
            res = sr["nearest_resistance"][0]
            if abs(price - res) / price < 0.005:
                confidence += 20
                reasons.append(f"✅ Price pushing against resistance level: {round(res,4)}")

        if sd["price_in_supply"]:
            confidence += 20
            reasons.append("✅ Price inside a supply zone (institutional selling area)")

        if fib["in_golden_zone"] and fib["trend_direction"] == "BEARISH":
            confidence += 20
            reasons.append(f"✅ Price at Fibonacci 61.8% Golden Zone — strongest retracement level!")
        elif fib["near_382_or_50"] and fib["trend_direction"] == "BEARISH":
            confidence += 12
            reasons.append("✅ Price near Fibonacci 38.2% or 50% retracement")

        if rsi > 55:
            confidence += 15
            reasons.append(f"✅ RSI at {round(rsi,1)} — elevated, room to move down")

        if confidence >= 55:
            signal_type = "SELL"

    if not signal_type:
        print(f"   No signal for {name} (confidence: {confidence}%)")
        return None

    # ── SL / TP CALCULATION ────────────────────────────────
    sl_dist = atr * CONFIG["atr_sl_multiplier"]
    tp_dist = sl_dist * CONFIG["rr_ratio"]
    stop_loss   = round(price - sl_dist, 4) if signal_type == "BUY" else round(price + sl_dist, 4)
    take_profit = round(price + tp_dist, 4) if signal_type == "BUY" else round(price - tp_dist, 4)
    position    = calculate_position_size(price, stop_loss)

    return {
        "instrument":  name,
        "symbol":      symbol,
        "signal":      signal_type,
        "price":       round(price, 4),
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "confidence":  confidence,
        "trend":       trend,
        "rsi":         round(rsi, 1),
        "atr":         round(atr, 4),
        "sr_levels":   sr,
        "sd_zones":    sd,
        "fibonacci":   fib,
        "reasons":     reasons,
        "position":    position,
        "timestamp":   datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }

# ============================================================
#  EMAIL ALERTS
# ============================================================
def build_fib_table(fib):
    rows = ""
    for name, level in fib["levels"].items():
        is_golden = "61.8" in name
        highlight = "background:#1a2a00; color:#3fb950; font-weight:bold;" if is_golden else "color:#e6edf3;"
        rows += f"""
        <tr>
            <td style="padding:5px 8px; color:#8b949e;">{name}</td>
            <td style="padding:5px 8px; {highlight}">{level}</td>
        </tr>"""
    return f"""
    <table style="width:100%; border-collapse:collapse; margin-top:8px; font-size:13px;">
        <tr>
            <th style="padding:5px 8px; color:#58a6ff; text-align:left;">Fib Level</th>
            <th style="padding:5px 8px; color:#58a6ff; text-align:left;">Price</th>
        </tr>
        {rows}
        <tr>
            <td style="padding:5px 8px; color:#8b949e;">Swing High</td>
            <td style="padding:5px 8px; color:#e6edf3;">{fib['swing_high']}</td>
        </tr>
        <tr>
            <td style="padding:5px 8px; color:#8b949e;">Swing Low</td>
            <td style="padding:5px 8px; color:#e6edf3;">{fib['swing_low']}</td>
        </tr>
    </table>"""

def send_email_alert(signals, news_events):
    if not signals and not news_events:
        return

    subject = f"🤖 Trading Bot — {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}"
    if signals:
        names   = ", ".join([s["instrument"] for s in signals])
        subject += f" | SIGNAL: {names}"

    html = """
    <html><body style="font-family:Arial,sans-serif; background:#0d1117; color:#e6edf3; padding:20px; max-width:700px; margin:auto;">
    <h2 style="color:#58a6ff; border-bottom:2px solid #30363d; padding-bottom:10px;">
        🤖 Trading Analysis Bot Report
    </h2>"""

    if news_events:
        html += """
        <div style="background:#3d1a00;border:1px solid #f85149;border-radius:8px;padding:15px;margin-bottom:20px;">
        <h3 style="color:#f85149;margin:0 0 10px 0;">⚠️ HIGH-IMPACT NEWS — Consider waiting before trading</h3>"""
        for e in news_events:
            html += f"""
            <div style="background:#1a0a00;padding:8px;margin:5px 0;border-radius:4px;">
                <strong style="color:#ffa657;">{e['time']}</strong> — {e['country']}: 
                <span style="color:#e6edf3;">{e['event']}</span>
            </div>"""
        html += "</div>"

    if signals:
        for s in signals:
            color = "#3fb950" if s["signal"] == "BUY" else "#f85149"
            bg    = "#0d2818" if s["signal"] == "BUY" else "#2d0f0f"
            emoji = "🟢" if s["signal"] == "BUY" else "🔴"
            fib   = s["fibonacci"]

            golden_note = ""
            if fib["in_golden_zone"]:
                golden_note = "<span style='color:#ffd700; font-weight:bold;'> ⭐ GOLDEN ZONE (61.8%)</span>"
            elif fib["near_382_or_50"]:
                golden_note = "<span style='color:#ffa657;'> Fib 38.2%/50% zone</span>"

            html += f"""
            <div style="background:{bg};border:1px solid {color};border-radius:8px;padding:20px;margin-bottom:25px;">
                <h3 style="color:{color};margin:0 0 5px 0;">{emoji} {s['instrument']} — {s['signal']} SIGNAL{golden_note}</h3>
                <p style="color:#8b949e;margin:0 0 15px 0;font-size:13px;">{s['timestamp']}</p>

                <table style="width:100%;border-collapse:collapse;margin-bottom:15px;">
                    <tr>
                        <td style="padding:7px;color:#8b949e;width:30%;">Entry Price</td>
                        <td style="padding:7px;color:#e6edf3;font-weight:bold;">{s['price']}</td>
                        <td style="padding:7px;color:#8b949e;width:30%;">Trend</td>
                        <td style="padding:7px;color:{color};">{s['trend']['trend']} ({s['trend']['strength']})</td>
                    </tr>
                    <tr>
                        <td style="padding:7px;color:#8b949e;">Stop Loss 🛑</td>
                        <td style="padding:7px;color:#f85149;font-weight:bold;">{s['stop_loss']}</td>
                        <td style="padding:7px;color:#8b949e;">RSI</td>
                        <td style="padding:7px;color:#e6edf3;">{s['rsi']}</td>
                    </tr>
                    <tr>
                        <td style="padding:7px;color:#8b949e;">Take Profit 🎯</td>
                        <td style="padding:7px;color:#3fb950;font-weight:bold;">{s['take_profit']}</td>
                        <td style="padding:7px;color:#8b949e;">ATR</td>
                        <td style="padding:7px;color:#e6edf3;">{s['atr']}</td>
                    </tr>
                    <tr>
                        <td style="padding:7px;color:#8b949e;">Suggested Lots</td>
                        <td style="padding:7px;color:#e6edf3;">{s['position']['lots']}</td>
                        <td style="padding:7px;color:#8b949e;">Risk Amount</td>
                        <td style="padding:7px;color:#ffa657;">${s['position']['risk_amount']}</td>
                    </tr>
                    <tr>
                        <td style="padding:7px;color:#8b949e;">Confidence</td>
                        <td colspan="3" style="padding:7px;color:{color};font-weight:bold;">{s['confidence']}%</td>
                    </tr>
                </table>

                <div style="background:#0d1117;padding:12px;border-radius:6px;margin-bottom:12px;">
                    <strong style="color:#ffd700;">📐 Fibonacci Levels (Swing: {fib['swing_low']} → {fib['swing_high']})</strong>
                    {build_fib_table(fib)}
                </div>

                <div style="background:#0d1117;padding:12px;border-radius:6px;margin-bottom:12px;">
                    <strong style="color:#58a6ff;">📊 Support & Resistance</strong><br><br>
                    <span style="color:#3fb950;">🟢 Support: </span>
                    {', '.join([str(x) for x in s['sr_levels']['nearest_support']]) or 'None identified'}<br>
                    <span style="color:#f85149;">🔴 Resistance: </span>
                    {', '.join([str(x) for x in s['sr_levels']['nearest_resistance']]) or 'None identified'}<br>
                    <span style="color:#ffa657;">📦 In Demand Zone: {'Yes ✅' if s['sd_zones']['price_in_demand'] else 'No'}</span><br>
                    <span style="color:#ffa657;">📦 In Supply Zone: {'Yes ✅' if s['sd_zones']['price_in_supply'] else 'No'}</span>
                </div>

                <div style="background:#0d1117;padding:12px;border-radius:6px;margin-bottom:12px;">
                    <strong style="color:#58a6ff;">🧠 Why This Signal Was Generated:</strong><br><br>
                    {'<br>'.join(s['reasons'])}
                </div>

                <div style="background:#1a1f00;border-left:3px solid #ffa657;padding:12px;border-radius:4px;">
                    <strong style="color:#ffa657;">⚠️ Always confirm this setup on your MT5 chart before entering!</strong>
                    <span style="color:#8b949e;"> Never skip your stop loss. This is demo mode — treat it like real money to build good habits.</span>
                </div>
            </div>"""
    else:
        html += """
        <div style="background:#1c2128;border-radius:8px;padding:20px;text-align:center;">
            <p style="color:#8b949e;">No high-confidence signals this scan. The bot is being patient — that's a good thing. 🎯</p>
        </div>"""

    html += """
    <p style="color:#30363d;font-size:11px;margin-top:20px;border-top:1px solid #21262d;padding-top:10px;">
        Automated analysis only. Not financial advice. Always manage your risk. Past signals ≠ future results.
    </p></body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = CONFIG["sender_email"]
        msg["To"]      = CONFIG["receiver_email"]
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(CONFIG["sender_email"], CONFIG["sender_password"])
            server.sendmail(CONFIG["sender_email"], CONFIG["receiver_email"], msg.as_string())
        print(f"✅ Email sent to {CONFIG['receiver_email']}")
    except Exception as e:
        print(f"❌ Email error: {e}")
        print("   Double-check your Gmail App Password is correct.")

# ============================================================
#  MAIN LOOP
# ============================================================
def run_bot():
    print("=" * 60)
    print("  🤖 TRADING BOT — Starting Up")
    print(f"  Account: Demo | Balance: ${CONFIG['account_balance']}")
    print(f"  Instruments: {', '.join(INSTRUMENTS.keys())}")
    print(f"  Scanning every {CONFIG['scan_interval_minutes']} minutes")
    print(f"  Risk per trade: {CONFIG['risk_percent']}% (${CONFIG['account_balance'] * CONFIG['risk_percent'] / 100})")
    print("=" * 60)

    while True:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        print(f"\n⏰ Scan at {now}")

        signals = []
        for name, symbol in INSTRUMENTS.items():
            try:
                sig = generate_signal(name, symbol)
                if sig:
                    signals.append(sig)
                    print(f"   🎯 {name}: {sig['signal']} | Confidence: {sig['confidence']}% | Fib Golden Zone: {sig['fibonacci']['in_golden_zone']}")
            except Exception as e:
                print(f"   ❌ Error on {name}: {e}")

        news_events = []  # Add news API here once you have a key

        if signals or news_events:
            print(f"\n📧 Sending email — {len(signals)} signal(s)...")
            send_email_alert(signals, news_events)
        else:
            print("💤 No signals. Waiting...")

        next_time = (datetime.utcnow() + timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%H:%M UTC")
        print(f"⏳ Next scan at {next_time}")
        time.sleep(CONFIG["scan_interval_minutes"] * 60)

if __name__ == "__main__":
    run_bot()
