import requests
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time

# ============================================================
#  YOUR DETAILS
# ============================================================
YOUR_GMAIL          = "ebubeonianwah@gmail.com"
YOUR_APP_PASSWORD   = "twya tjbj hdez jjuk"
YOUR_TWELVEDATA_KEY = "4ab2a4a5f9844f709a0baa38957292a6"

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
    "min_confidence":        70,    # Only send 70%+ signals
    "news_pause_minutes":    20,    # Pause 20 mins before/after news
    "max_signals_per_scan":  3,     # Quality over quantity
}

# Instruments — Twelve Data symbols
INSTRUMENTS = {
    "XAUUSD":  "XAU/USD",
    "BTCUSD":  "BTC/USD",
    "US30":    "AMEX:DIA",
    "NASDAQ":  "AMEX:QQQ",
    "GER40":   "DAX",
}

# Market hours (UTC) — when each instrument trades
MARKET_HOURS = {
    "XAUUSD":  {"open": 1,  "close": 22},  # Gold — nearly 24h but respects forex close
    "BTCUSD":  {"open": 0,  "close": 24},  # Crypto — 24/7
    "US30":    {"open": 13, "close": 20},  # NYSE — 13:30-20:00 UTC
    "NASDAQ":  {"open": 13, "close": 20},  # NASDAQ — same as NYSE
    "GER40":   {"open": 7,  "close": 15},  # Frankfurt — 07:00-15:30 UTC
}

# Trading sessions (UTC)
SESSIONS = {
    "London":   {"open": 7,  "close": 16},
    "New York": {"open": 13, "close": 20},
    "Overlap":  {"open": 13, "close": 16},
}

FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_NAMES  = ["0%", "23.6%", "38.2%", "50%", "61.8% Golden Zone", "78.6%", "100%"]

TRADE_LOG = []

# ============================================================
#  MARKET HOURS CHECK
# ============================================================
def is_market_open(instrument):
    """Check if the specific market is open right now."""
    if instrument == "BTCUSD":
        return True  # Crypto never closes
    now_utc = datetime.utcnow()
    # Skip weekends for indices and gold
    if now_utc.weekday() >= 5:  # Saturday=5, Sunday=6
        if instrument in ["US30", "NASDAQ", "GER40"]:
            return False
        if instrument == "XAUUSD" and now_utc.weekday() == 6 and now_utc.hour < 22:
            return False  # Gold closes Sunday until 22:00 UTC
    hours = MARKET_HOURS.get(instrument, {"open": 0, "close": 24})
    return hours["open"] <= now_utc.hour < hours["close"]

def get_active_session():
    hour = datetime.utcnow().hour
    if 13 <= hour < 16:
        return "London/New York Overlap ⭐⭐⭐", 15
    elif 7 <= hour < 16:
        return "London Session ⭐⭐", 8
    elif 13 <= hour < 20:
        return "New York Session ⭐⭐", 8
    return "Off-Peak Session", -5

# ============================================================
#  NEWS FILTER
# ============================================================
def get_high_impact_news():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=8)
        events = r.json()
        now = datetime.utcnow()
        upcoming = []
        for e in events:
            try:
                if e.get("impact", "").lower() != "high":
                    continue
                date_str = e.get("date", "")
                if not date_str:
                    continue
                event_time = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")
                diff_mins = (event_time - now).total_seconds() / 60
                if -CONFIG["news_pause_minutes"] <= diff_mins <= CONFIG["news_pause_minutes"]:
                    upcoming.append({
                        "title":   e.get("title", "Unknown event"),
                        "country": e.get("country", ""),
                        "time":    event_time.strftime("%H:%M UTC"),
                        "minutes": round(diff_mins),
                    })
            except:
                continue
        return upcoming
    except:
        return []

# ============================================================
#  MATH HELPERS
# ============================================================
def mean(v):
    return sum(v) / len(v) if v else 0

def ema(values, period):
    if len(values) < period:
        return [mean(values)] * len(values)
    k = 2.0 / (period + 1)
    result = [mean(values[:period])]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    pad = len(values) - len(result)
    return [result[0]] * pad + result

def atr(highs, lows, closes, period=14):
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    return mean(trs[-period:]) if trs else 0

def rsi(closes, period=14):
    if len(closes) < period+1: return 50
    gains  = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag, al = mean(gains[-period:]), mean(losses[-period:])
    return 50 if al == 0 else 100 - (100/(1+ag/al))

def macd_histogram(closes):
    if len(closes) < 26: return 0
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    ml  = [a-b for a,b in zip(e12, e26)]
    sig = ema(ml, 9)
    return ml[-1] - sig[-1]

# ============================================================
#  DATA FETCH
# ============================================================
def get_ohlcv(symbol, interval="15min", bars=100):
    try:
        r = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": symbol, "interval": interval,
            "outputsize": bars, "apikey": CONFIG["twelvedata_api_key"], "format": "JSON"
        }, timeout=10)
        data = r.json()
        if "values" not in data:
            print(f"      API: {data.get('message','no data')}")
            return None
        vals = sorted(data["values"], key=lambda x: x["datetime"])
        return {
            "opens":  [float(v["open"])  for v in vals],
            "highs":  [float(v["high"])  for v in vals],
            "lows":   [float(v["low"])   for v in vals],
            "closes": [float(v["close"]) for v in vals],
        }
    except Exception as e:
        print(f"      Fetch error: {e}")
        return None

# ============================================================
#  FVG — FAIR VALUE GAP
# ============================================================
def find_fvg(highs, lows, closes, lookback=30):
    """
    Fair Value Gap (FVG):
    Bullish FVG: candle[i-1].high < candle[i+1].low  → gap between them (price likely returns)
    Bearish FVG: candle[i-1].low  > candle[i+1].high → gap between them
    """
    current   = closes[-1]
    bull_fvgs = []
    bear_fvgs = []
    start = max(1, len(highs) - lookback)

    for i in range(start, len(highs) - 1):
        # Bullish FVG — gap UP (candle i is big bullish, gap above i-1 high and below i+1 low)
        if highs[i-1] < lows[i+1]:
            gap_top    = lows[i+1]
            gap_bottom = highs[i-1]
            gap_mid    = (gap_top + gap_bottom) / 2
            bull_fvgs.append({"top": gap_top, "bottom": gap_bottom, "mid": gap_mid, "type": "Bullish FVG"})

        # Bearish FVG — gap DOWN
        if lows[i-1] > highs[i+1]:
            gap_top    = lows[i-1]
            gap_bottom = highs[i+1]
            gap_mid    = (gap_top + gap_bottom) / 2
            bear_fvgs.append({"top": gap_top, "bottom": gap_bottom, "mid": gap_mid, "type": "Bearish FVG"})

    # Check if price is inside or near an FVG (within 0.3%)
    price_in_bull_fvg = any(f["bottom"] <= current <= f["top"] for f in bull_fvgs)
    price_in_bear_fvg = any(f["bottom"] <= current <= f["top"] for f in bear_fvgs)
    near_bull_fvg     = any(abs(current - f["mid"]) / max(current,1) < 0.003 for f in bull_fvgs)
    near_bear_fvg     = any(abs(current - f["mid"]) / max(current,1) < 0.003 for f in bear_fvgs)

    return {
        "bull_fvgs":       bull_fvgs[-3:],
        "bear_fvgs":       bear_fvgs[-3:],
        "price_in_bull":   price_in_bull_fvg,
        "price_in_bear":   price_in_bear_fvg,
        "near_bull":       near_bull_fvg,
        "near_bear":       near_bear_fvg,
    }

# ============================================================
#  IFVG — INVERSE FAIR VALUE GAP
# ============================================================
def find_ifvg(highs, lows, closes, fvg_data):
    """
    IFVG: When price returns INTO an FVG zone and closes through it,
    the FVG flips — the old bullish FVG becomes bearish (IFVG) and vice versa.
    We detect this by checking if price has already passed through recent FVGs.
    """
    current = closes[-1]
    ifvg_bull = []  # Old bear FVG that price passed through = now bullish
    ifvg_bear = []  # Old bull FVG that price passed through = now bearish

    # If price is ABOVE a bullish FVG it already filled → potential IFVG resistance
    for fvg in fvg_data["bull_fvgs"]:
        if current > fvg["top"]:
            ifvg_bear.append({"level": fvg["top"], "type": "IFVG Resistance (filled bull FVG)"})

    # If price is BELOW a bearish FVG it already filled → potential IFVG support
    for fvg in fvg_data["bear_fvgs"]:
        if current < fvg["bottom"]:
            ifvg_bull.append({"level": fvg["bottom"], "type": "IFVG Support (filled bear FVG)"})

    near_ifvg_support    = any(abs(current - f["level"]) / max(current,1) < 0.004 for f in ifvg_bull)
    near_ifvg_resistance = any(abs(current - f["level"]) / max(current,1) < 0.004 for f in ifvg_bear)

    return {
        "ifvg_bull":           ifvg_bull[-2:],
        "ifvg_bear":           ifvg_bear[-2:],
        "near_ifvg_support":   near_ifvg_support,
        "near_ifvg_resistance": near_ifvg_resistance,
    }

# ============================================================
#  BPR — BALANCED PRICE RANGE
# ============================================================
def find_bpr(highs, lows, closes, lookback=50):
    """
    Balanced Price Range (BPR):
    An area where TWO overlapping FVGs exist — one bullish and one bearish.
    The overlap is called the BPR and is considered a premium/discount zone.
    Price respects BPR strongly as it represents balanced supply & demand.
    """
    current   = closes[-1]
    bpr_zones = []
    start     = max(1, len(highs) - lookback)

    bull_gaps = []
    bear_gaps = []

    for i in range(start, len(highs) - 1):
        if highs[i-1] < lows[i+1]:
            bull_gaps.append({"top": lows[i+1], "bottom": highs[i-1]})
        if lows[i-1] > highs[i+1]:
            bear_gaps.append({"top": lows[i-1], "bottom": highs[i+1]})

    # Find overlapping bull and bear FVGs = BPR
    for bg in bull_gaps:
        for brg in bear_gaps:
            overlap_top    = min(bg["top"],    brg["top"])
            overlap_bottom = max(bg["bottom"], brg["bottom"])
            if overlap_top > overlap_bottom:
                mid = (overlap_top + overlap_bottom) / 2
                bpr_zones.append({
                    "top":    round(overlap_top,    4),
                    "bottom": round(overlap_bottom, 4),
                    "mid":    round(mid,            4),
                })

    price_in_bpr  = any(z["bottom"] <= current <= z["top"] for z in bpr_zones)
    near_bpr      = any(abs(current - z["mid"]) / max(current,1) < 0.005 for z in bpr_zones)

    return {
        "bpr_zones":    bpr_zones[-3:],
        "price_in_bpr": price_in_bpr,
        "near_bpr":     near_bpr,
        "count":        len(bpr_zones),
    }

# ============================================================
#  TREND + S/R + SUPPLY/DEMAND + FIB
# ============================================================
def get_trend(closes):
    if len(closes) < 50:
        return {"trend": "UNKNOWN", "strength": "WEAK", "ema50": 0, "ema200": 0}
    e50  = ema(closes, 50)[-1]
    e200 = ema(closes, min(200, len(closes)))[-1]
    c    = closes[-1]
    if c > e50 > e200:   return {"trend":"BULLISH","strength":"STRONG",   "ema50":round(e50,2),"ema200":round(e200,2)}
    elif c > e200:        return {"trend":"BULLISH","strength":"MODERATE", "ema50":round(e50,2),"ema200":round(e200,2)}
    elif c < e50 < e200: return {"trend":"BEARISH","strength":"STRONG",   "ema50":round(e50,2),"ema200":round(e200,2)}
    elif c < e200:        return {"trend":"BEARISH","strength":"MODERATE", "ema50":round(e50,2),"ema200":round(e200,2)}
    return {"trend":"RANGING","strength":"WEAK","ema50":round(e50,2),"ema200":round(e200,2)}

def find_sr(highs, lows, closes):
    current = closes[-1]
    res, sup = [], []
    for i in range(2, len(highs)-2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            res.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            sup.append(lows[i])
    def cluster(lvls):
        if not lvls: return []
        lvls = sorted(set(lvls))
        out  = [lvls[0]]
        for l in lvls[1:]:
            if abs(l-out[-1])/max(out[-1],0.0001) > 0.002: out.append(l)
        return out
    return {
        "nearest_resistance": sorted([r for r in cluster(res) if r > current])[:3],
        "nearest_support":    sorted([s for s in cluster(sup) if s < current], reverse=True)[:3],
    }

def find_sd(opens, closes):
    avg = mean([abs(c-o) for c,o in zip(closes,opens)]) or 0.0001
    demand, supply = [], []
    current = closes[-1]
    for i in range(len(closes)):
        size = abs(closes[i]-opens[i])
        if size > avg*1.5:
            top,bot = max(opens[i],closes[i]),min(opens[i],closes[i])
            (demand if closes[i]>opens[i] else supply).append({"top":top,"bottom":bot})
    return {
        "in_demand": any(z["bottom"]<current<z["top"]*1.02 for z in demand),
        "in_supply": any(z["bottom"]*0.98<current<z["top"] for z in supply),
    }

def calc_fib(highs, lows, closes):
    lookback    = min(50, len(closes))
    rh          = highs[-lookback:]
    rl          = lows[-lookback:]
    swing_high  = max(rh)
    swing_low   = min(rl)
    bullish     = rl.index(swing_low) < rh.index(swing_high)
    price_range = swing_high - swing_low
    levels = {}
    for lvl,name in zip(FIB_LEVELS, FIB_NAMES):
        levels[name] = round((swing_high-price_range*lvl) if bullish else (swing_low+price_range*lvl), 4)
    c          = closes[-1]
    golden     = levels.get("61.8% Golden Zone", 0)
    fib382     = levels.get("38.2%", 0)
    fib50      = levels.get("50%", 0)
    in_golden  = abs(c-golden)/max(c,1) < 0.003
    near_38_50 = abs(c-fib382)/max(c,1) < 0.003 or abs(c-fib50)/max(c,1) < 0.003
    return {
        "swing_high":      round(swing_high,4),
        "swing_low":       round(swing_low,4),
        "levels":          levels,
        "in_golden":       in_golden,
        "near_38_50":      near_38_50,
        "trend_direction": "BULLISH" if bullish else "BEARISH",
    }

# ============================================================
#  MULTI-TIMEFRAME
# ============================================================
def mtf_check(symbol, primary_trend):
    score   = 0
    results = []
    for tf,label in [("1h","1 Hour"),("4h","4 Hour")]:
        data = get_ohlcv(symbol, interval=tf, bars=100)
        if not data or len(data["closes"]) < 50:
            results.append(f"⚠️ {label}: No data")
            continue
        t = get_trend(data["closes"])
        if t["trend"] == primary_trend:
            score += 1
            results.append(f"✅ {label}: {t['trend']} ({t['strength']}) — confirms!")
        else:
            results.append(f"❌ {label}: {t['trend']} — does NOT confirm")
    return {"score": score, "results": results, "fully_aligned": score==2}

# ============================================================
#  SIGNAL GENERATION
# ============================================================
def generate_signal(name, symbol):
    print(f"\n   🔍 {name}...")

    if not is_market_open(name):
        print(f"      🔴 Market closed")
        return None

    data = get_ohlcv(symbol)
    if not data or len(data["closes"]) < 55:
        return None

    opens  = data["opens"]
    highs  = data["highs"]
    lows   = data["lows"]
    closes = data["closes"]
    price  = closes[-1]

    trend   = get_trend(closes)
    sr      = find_sr(highs, lows, closes)
    sd      = find_sd(opens, closes)
    fib     = calc_fib(highs, lows, closes)
    atr_val = atr(highs, lows, closes)
    rsi_val = rsi(closes)
    macd_h  = macd_histogram(closes)
    fvg     = find_fvg(highs, lows, closes)
    ifvg    = find_ifvg(highs, lows, closes, fvg)
    bpr     = find_bpr(highs, lows, closes)
    session, sess_bonus = get_active_session()

    signal_type = None
    reasons     = []
    confidence  = 0

    # ── BUY CONDITIONS ──────────────────────────────────────
    if trend["trend"] == "BULLISH":
        confidence += 20
        reasons.append(f"✅ Bullish trend — EMA50 {trend['ema50']} > EMA200 {trend['ema200']}")
        if trend["strength"] == "STRONG":
            confidence += 8
            reasons.append("✅ Strong trend — price above both EMAs")

        # S/R
        if sr["nearest_support"] and abs(price-sr["nearest_support"][0])/max(price,1) < 0.005:
            confidence += 12
            reasons.append(f"✅ Price at support: {round(sr['nearest_support'][0],2)}")

        # Supply/Demand
        if sd["in_demand"]:
            confidence += 12
            reasons.append("✅ Price in demand zone")

        # Fibonacci
        if fib["in_golden"] and fib["trend_direction"] == "BULLISH":
            confidence += 18
            reasons.append("✅ Fibonacci 61.8% Golden Zone ⭐")
        elif fib["near_38_50"]:
            confidence += 8
            reasons.append("✅ Near Fibonacci 38.2%/50%")

        # FVG
        if fvg["price_in_bull"] or fvg["near_bull"]:
            confidence += 15
            reasons.append("✅ Price in/near Bullish FVG — institutional gap")

        # IFVG
        if ifvg["near_ifvg_support"]:
            confidence += 12
            reasons.append("✅ Price near IFVG Support — flipped zone acting as support")

        # BPR
        if bpr["price_in_bpr"] or bpr["near_bpr"]:
            confidence += 15
            reasons.append("✅ Price in Balanced Price Range (BPR) — strong confluence zone!")

        # RSI
        if rsi_val < 45:
            confidence += 8
            reasons.append(f"✅ RSI {round(rsi_val,1)} — not overbought, room to rise")

        # MACD
        if macd_h > 0:
            confidence += 5
            reasons.append("✅ MACD bullish momentum")

        # Session
        confidence += sess_bonus
        if sess_bonus > 0:
            reasons.append(f"✅ {session}")

        if confidence >= 50:  # Pre-MTF check — final 70% check happens after MTF
            signal_type = "BUY"

    # ── SELL CONDITIONS ─────────────────────────────────────
    elif trend["trend"] == "BEARISH":
        confidence += 20
        reasons.append(f"✅ Bearish trend — EMA50 {trend['ema50']} < EMA200 {trend['ema200']}")
        if trend["strength"] == "STRONG":
            confidence += 8
            reasons.append("✅ Strong trend — price below both EMAs")

        if sr["nearest_resistance"] and abs(price-sr["nearest_resistance"][0])/max(price,1) < 0.005:
            confidence += 12
            reasons.append(f"✅ Price at resistance: {round(sr['nearest_resistance'][0],2)}")

        if sd["in_supply"]:
            confidence += 12
            reasons.append("✅ Price in supply zone")

        if fib["in_golden"] and fib["trend_direction"] == "BEARISH":
            confidence += 18
            reasons.append("✅ Fibonacci 61.8% Golden Zone ⭐")
        elif fib["near_38_50"]:
            confidence += 8
            reasons.append("✅ Near Fibonacci 38.2%/50%")

        if fvg["price_in_bear"] or fvg["near_bear"]:
            confidence += 15
            reasons.append("✅ Price in/near Bearish FVG — institutional gap")

        if ifvg["near_ifvg_resistance"]:
            confidence += 12
            reasons.append("✅ Price near IFVG Resistance — flipped zone acting as resistance")

        if bpr["price_in_bpr"] or bpr["near_bpr"]:
            confidence += 15
            reasons.append("✅ Price in Balanced Price Range (BPR) — strong confluence zone!")

        if rsi_val > 55:
            confidence += 8
            reasons.append(f"✅ RSI {round(rsi_val,1)} — elevated, room to fall")

        if macd_h < 0:
            confidence += 5
            reasons.append("✅ MACD bearish momentum")

        confidence += sess_bonus
        if sess_bonus > 0:
            reasons.append(f"✅ {session}")

        if confidence >= 50:  # Pre-MTF check — final 70% check happens after MTF
            signal_type = "SELL"

    if not signal_type:
        print(f"      No signal ({min(confidence,100)}% confidence — below {CONFIG['min_confidence']}% threshold)")
        return None

    # Multi-timeframe check
    print(f"      📊 Checking higher timeframes...")
    mtf = mtf_check(symbol, trend["trend"])
    if mtf["fully_aligned"]:
        confidence += 15
        reasons.append("✅ ALL 3 timeframes aligned (15min + 1H + 4H) 🔥")
    elif mtf["score"] == 1:
        confidence += 7
        reasons.append("✅ 1 higher timeframe confirms")
    else:
        confidence -= 15
        reasons.append("⚠️ Higher timeframes contradict — reducing confidence")
    for r in mtf["results"]:
        reasons.append(f"   {r}")

    confidence = min(confidence, 100)

    # Final quality check after MTF
    if confidence < CONFIG["min_confidence"]:
        print(f"      Signal dropped below threshold after MTF check ({confidence}%)")
        return None

    quality = "🔥 PREMIUM" if confidence >= 85 else "⭐ STRONG" if confidence >= 70 else "📊 STANDARD"

    sl_dist     = atr_val * CONFIG["atr_sl_multiplier"]
    tp_dist     = sl_dist * CONFIG["rr_ratio"]
    stop_loss   = round(price - sl_dist, 4) if signal_type == "BUY" else round(price + sl_dist, 4)
    take_profit = round(price + tp_dist, 4) if signal_type == "BUY" else round(price - tp_dist, 4)
    risk_amt    = CONFIG["account_balance"] * CONFIG["risk_percent"] / 100
    lots        = round(risk_amt / max(sl_dist, 0.0001), 2)

    print(f"      🎯 {signal_type} | {confidence}% | {quality}")

    sig = {
        "instrument":  name,
        "symbol":      symbol,
        "signal":      signal_type,
        "quality":     quality,
        "price":       round(price,4),
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "confidence":  confidence,
        "trend":       trend,
        "rsi":         round(rsi_val,1),
        "macd_h":      round(macd_h,4),
        "atr":         round(atr_val,4),
        "sr":          sr,
        "sd":          sd,
        "fib":         fib,
        "fvg":         fvg,
        "ifvg":        ifvg,
        "bpr":         bpr,
        "mtf":         mtf,
        "session":     session,
        "reasons":     reasons,
        "lots":        lots,
        "risk_amount": round(risk_amt,2),
        "timestamp":   datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }

    TRADE_LOG.append({
        "time": sig["timestamp"], "instrument": name,
        "signal": signal_type, "confidence": confidence,
        "entry": price, "sl": stop_loss, "tp": take_profit,
    })

    return sig

# ============================================================
#  EMAIL
# ============================================================
def build_email(signals, news_events):
    subject = f"🤖 Bot Alert — {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}"
    if signals:
        subject += " | " + " | ".join([f"{s['quality']} {s['instrument']} {s['signal']}" for s in signals])

    html = """<html><body style='font-family:Arial,sans-serif;background:#0d1117;
    color:#e6edf3;padding:20px;max-width:720px;margin:auto;'>
    <h2 style='color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px;'>
    🤖 Trading Bot — Quality Signals Only (70%+)</h2>"""

    if news_events:
        html += "<div style='background:#3d1a00;border:1px solid #f85149;border-radius:8px;padding:15px;margin-bottom:20px;'>"
        html += "<h3 style='color:#f85149;margin:0 0 8px 0;'>⚠️ NEWS ALERT — 20 min trading pause active</h3>"
        for e in news_events:
            mins = e['minutes']
            timing = f"{abs(mins)} min {'ago' if mins<0 else 'away'}"
            html += f"<p style='margin:4px 0;'><strong style='color:#ffa657;'>{e['time']}</strong> — {e['country']}: {e['title']} ({timing})</p>"
        html += "</div>"

    if not signals:
        html += "<div style='background:#1c2128;border-radius:8px;padding:25px;text-align:center;'>"
        html += "<h3 style='color:#58a6ff;'>No signals met the 70% threshold this scan</h3>"
        html += "<p style='color:#8b949e;'>The bot is being selective — that's exactly what you want. 🎯<br>Quality over quantity.</p></div>"
    else:
        for s in signals:
            color = "#3fb950" if s["signal"] == "BUY" else "#f85149"
            bg    = "#0d2818" if s["signal"] == "BUY" else "#2d0f0f"
            emoji = "🟢" if s["signal"] == "BUY" else "🔴"

            # Fib table
            fib_rows = ""
            for n,l in s["fib"]["levels"].items():
                gold_style = "color:#ffd700;font-weight:bold;" if "61.8" in n else "color:#e6edf3;"
                fib_rows += f"<tr><td style='padding:4px 8px;color:#8b949e;'>{n}</td><td style='padding:4px 8px;{gold_style}'>{l}</td></tr>"

            # Smart money section
            fvg_bull = s["fvg"]["price_in_bull"] or s["fvg"]["near_bull"]
            fvg_bear = s["fvg"]["price_in_bear"] or s["fvg"]["near_bear"]
            ifvg_sup = s["ifvg"]["near_ifvg_support"]
            ifvg_res = s["ifvg"]["near_ifvg_resistance"]
            in_bpr   = s["bpr"]["price_in_bpr"] or s["bpr"]["near_bpr"]

            html += f"""
            <div style='background:{bg};border:2px solid {color};border-radius:10px;padding:20px;margin-bottom:25px;'>
                <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;'>
                    <h3 style='color:{color};margin:0;font-size:20px;'>{emoji} {s['instrument']} — {s['signal']} {s['quality']}</h3>
                    <span style='color:{color};font-size:22px;font-weight:bold;'>{s['confidence']}%</span>
                </div>
                <p style='color:#8b949e;margin:0 0 15px 0;font-size:12px;'>{s['timestamp']} | {s['session']}</p>

                <table style='width:100%;border-collapse:collapse;margin-bottom:15px;background:#0d1117;border-radius:6px;'>
                    <tr><td style='padding:8px;color:#8b949e;'>📍 Entry</td><td style='padding:8px;color:#e6edf3;font-weight:bold;font-size:16px;'>{s['price']}</td>
                        <td style='padding:8px;color:#8b949e;'>📈 Trend</td><td style='padding:8px;color:{color};'>{s['trend']['trend']} ({s['trend']['strength']})</td></tr>
                    <tr><td style='padding:8px;color:#8b949e;'>🛑 Stop Loss</td><td style='padding:8px;color:#f85149;font-weight:bold;font-size:16px;'>{s['stop_loss']}</td>
                        <td style='padding:8px;color:#8b949e;'>RSI</td><td style='padding:8px;'>{s['rsi']}</td></tr>
                    <tr><td style='padding:8px;color:#8b949e;'>🎯 Take Profit</td><td style='padding:8px;color:#3fb950;font-weight:bold;font-size:16px;'>{s['take_profit']}</td>
                        <td style='padding:8px;color:#8b949e;'>MACD</td><td style='padding:8px;'>{"↑ Bullish" if s["macd_h"]>0 else "↓ Bearish"}</td></tr>
                    <tr><td style='padding:8px;color:#8b949e;'>📦 Lots</td><td style='padding:8px;'>{s['lots']}</td>
                        <td style='padding:8px;color:#8b949e;'>Risk</td><td style='padding:8px;color:#ffa657;'>${s['risk_amount']}</td></tr>
                </table>

                <div style='background:#0d1117;padding:12px;border-radius:6px;margin-bottom:10px;'>
                    <strong style='color:#ffd700;'>🏦 Smart Money Concepts</strong>
                    <table style='width:100%;margin-top:8px;border-collapse:collapse;'>
                        <tr><td style='padding:5px 8px;color:#8b949e;'>Fair Value Gap (FVG)</td>
                            <td style='padding:5px 8px;color:{"#3fb950" if fvg_bull else "#f85149" if fvg_bear else "#8b949e"};'>
                            {"✅ Bullish FVG present" if fvg_bull else "✅ Bearish FVG present" if fvg_bear else "None nearby"}</td></tr>
                        <tr><td style='padding:5px 8px;color:#8b949e;'>Inverse FVG (IFVG)</td>
                            <td style='padding:5px 8px;color:{"#3fb950" if ifvg_sup else "#f85149" if ifvg_res else "#8b949e"};'>
                            {"✅ IFVG Support" if ifvg_sup else "✅ IFVG Resistance" if ifvg_res else "None nearby"}</td></tr>
                        <tr><td style='padding:5px 8px;color:#8b949e;'>Balanced Price Range</td>
                            <td style='padding:5px 8px;color:{"#ffd700" if in_bpr else "#8b949e"};'>
                            {"✅ Price in/near BPR zone!" if in_bpr else "Not in BPR"}</td></tr>
                        <tr><td style='padding:5px 8px;color:#8b949e;'>Demand Zone</td>
                            <td style='padding:5px 8px;color:{"#3fb950" if s["sd"]["in_demand"] else "#8b949e"};'>
                            {"✅ Yes" if s["sd"]["in_demand"] else "No"}</td></tr>
                        <tr><td style='padding:5px 8px;color:#8b949e;'>Supply Zone</td>
                            <td style='padding:5px 8px;color:{"#f85149" if s["sd"]["in_supply"] else "#8b949e"};'>
                            {"✅ Yes" if s["sd"]["in_supply"] else "No"}</td></tr>
                    </table>
                </div>

                <div style='background:#0d1117;padding:12px;border-radius:6px;margin-bottom:10px;'>
                    <strong style='color:#ffd700;'>📐 Fibonacci (Swing: {s["fib"]["swing_low"]} → {s["fib"]["swing_high"]})</strong>
                    <table style='width:100%;margin-top:8px;border-collapse:collapse;font-size:13px;'>
                        <tr><th style='padding:4px 8px;color:#58a6ff;text-align:left;'>Level</th>
                            <th style='padding:4px 8px;color:#58a6ff;text-align:left;'>Price</th></tr>
                        {fib_rows}
                    </table>
                </div>

                <div style='background:#0d1117;padding:12px;border-radius:6px;margin-bottom:10px;'>
                    <strong style='color:#58a6ff;'>📊 Multi-Timeframe</strong><br><br>
                    {"<br>".join(s["mtf"]["results"])}
                </div>

                <div style='background:#0d1117;padding:12px;border-radius:6px;margin-bottom:10px;'>
                    <strong style='color:#58a6ff;'>📋 Key Levels</strong><br><br>
                    <span style='color:#3fb950;'>Support: </span>{", ".join([str(round(x,2)) for x in s["sr"]["nearest_support"]]) or "None"}<br>
                    <span style='color:#f85149;'>Resistance: </span>{", ".join([str(round(x,2)) for x in s["sr"]["nearest_resistance"]]) or "None"}
                </div>

                <div style='background:#0d1117;padding:12px;border-radius:6px;margin-bottom:10px;'>
                    <strong style='color:#58a6ff;'>🧠 Why this signal ({len(s["reasons"])} reasons):</strong><br><br>
                    {"<br>".join(s["reasons"])}
                </div>

                <div style='background:#1a0f00;border:1px solid #ffa657;padding:12px;border-radius:6px;'>
                    <strong style='color:#ffa657;'>⚠️ ALWAYS:</strong>
                    <span style='color:#8b949e;'> Confirm on MT5 chart · Use your stop loss · Demo account only until profitable · Never risk more than 1%</span>
                </div>
            </div>"""

    html += """<p style='color:#30363d;font-size:11px;margin-top:15px;border-top:1px solid #21262d;padding-top:10px;'>
    Automated analysis only. Not financial advice. Past signals do not guarantee future results.</p>
    </body></html>"""

    return subject, html

def send_email(signals, news_events):
    subject, html = build_email(signals, news_events)
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = CONFIG["sender_email"]
        msg["To"]      = CONFIG["receiver_email"]
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(CONFIG["sender_email"], CONFIG["sender_password"])
            s.sendmail(CONFIG["sender_email"], CONFIG["receiver_email"], msg.as_string())
        print(f"   ✅ Email sent!")
    except Exception as e:
        print(f"   ❌ Email error: {e}")

def send_weekly_report():
    if not TRADE_LOG: return
    total = len(TRADE_LOG)
    html  = f"""<html><body style='font-family:Arial;background:#0d1117;color:#e6edf3;padding:20px;'>
    <h2 style='color:#58a6ff;'>📊 Weekly Report — {datetime.utcnow().strftime('%d %b %Y')}</h2>
    <p style='color:#8b949e;'>Total signals this week: <strong>{total}</strong></p>
    <table style='width:100%;border-collapse:collapse;'>
    <tr style='color:#58a6ff;'><th style='padding:8px;text-align:left;'>Time</th>
    <th>Pair</th><th>Signal</th><th>Confidence</th><th>Entry</th></tr>"""
    for t in TRADE_LOG:
        c = "#3fb950" if t["signal"]=="BUY" else "#f85149"
        html += f"<tr><td style='padding:6px;color:#8b949e;font-size:12px;'>{t['time']}</td><td style='padding:6px;'>{t['instrument']}</td><td style='padding:6px;color:{c};font-weight:bold;'>{t['signal']}</td><td style='padding:6px;color:{c};'>{t['confidence']}%</td><td style='padding:6px;'>{t['entry']}</td></tr>"
    html += "</table></body></html>"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"📊 Weekly Bot Report — {datetime.utcnow().strftime('%d %b %Y')}"
        msg["From"]    = CONFIG["sender_email"]
        msg["To"]      = CONFIG["receiver_email"]
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(CONFIG["sender_email"], CONFIG["sender_password"])
            s.sendmail(CONFIG["sender_email"], CONFIG["receiver_email"], msg.as_string())
        print("   ✅ Weekly report sent!")
        TRADE_LOG.clear()
    except Exception as e:
        print(f"   ❌ Weekly report error: {e}")

# ============================================================
#  MAIN LOOP
# ============================================================
def run_bot():
    print("=" * 60)
    print("  🤖 TRADING BOT — Quality Edition v3.0")
    print(f"  Pairs: {', '.join(INSTRUMENTS.keys())}")
    print(f"  Min confidence: {CONFIG['min_confidence']}%")
    print(f"  Features: FVG ✅ | IFVG ✅ | BPR ✅ | MTF ✅ | News ✅ | Sessions ✅")
    print("=" * 60)

    last_weekly = datetime.utcnow().date()

    while True:
        now = datetime.utcnow()
        print(f"\n⏰ {now.strftime('%Y-%m-%d %H:%M UTC')} — Scanning markets...")

        # Weekly report on Fridays
        if now.strftime("%A") == "Friday" and now.date() != last_weekly and now.hour == 20:
            send_weekly_report()
            last_weekly = now.date()

        # News check
        news_events = get_high_impact_news()
        if news_events:
            print(f"   ⚠️ {len(news_events)} high-impact news event(s) — pausing all trading!")
            send_email([], news_events)
            time.sleep(CONFIG["scan_interval_minutes"] * 60)
            continue

        # Check if ANY market is open
        open_markets = [name for name in INSTRUMENTS if is_market_open(name)]
        if not open_markets:
            print("   😴 All markets closed — no email sent, sleeping...")
            time.sleep(CONFIG["scan_interval_minutes"] * 60)
            continue

        print(f"   📍 Open markets: {', '.join(open_markets)}")

        # Generate signals — sorted by confidence, take top 3 only
        all_signals = []
        for name, symbol in INSTRUMENTS.items():
            try:
                sig = generate_signal(name, symbol)
                if sig:
                    all_signals.append(sig)
            except Exception as e:
                print(f"   ❌ {name} error: {e}")

        # Sort by confidence and take top signals only
        all_signals.sort(key=lambda x: x["confidence"], reverse=True)
        signals = all_signals[:CONFIG["max_signals_per_scan"]]

        if signals:
            print(f"\n   📧 Sending {len(signals)} quality signal(s)...")
            send_email(signals, [])
        else:
            print("   💤 No signals met the 70% threshold — no email sent")

        next_scan = (now + timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%H:%M UTC")
        print(f"   ⏳ Next scan: {next_scan}")
        time.sleep(CONFIG["scan_interval_minutes"] * 60)

if __name__ == "__main__":
    run_bot()
