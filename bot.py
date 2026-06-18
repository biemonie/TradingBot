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
    "NASDAQ":  "QQQ",
    "EURUSD":  "EUR/USD",
}

# Market hours (UTC) — when each instrument trades
MARKET_HOURS = {
    "XAUUSD":  {"open": 1,  "close": 22},  # Gold — nearly 24h but respects forex close
    "NASDAQ":  {"open": 13, "close": 20},  # NASDAQ — 13:30-20:00 UTC
    "EURUSD":  {"open": 7,  "close": 21},  # Forex — London + NY sessions
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
WATCH_LOG = []  # Track sent watch alerts to avoid duplicates

# ============================================================
#  MARKET HOURS CHECK
# ============================================================
def is_market_open(instrument):
    """Check if the specific market is open right now."""
    if instrument == "EURUSD":
        return 7 <= datetime.utcnow().hour < 21 and datetime.utcnow().weekday() < 5
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
        time.sleep(1.5)  # Prevent API rate limiting
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
#  ENTRY TIMING CONFIRMATION
# ============================================================
def check_30min_candle_confirmation(symbol, signal_type):
    """
    Check if the current 1H candle is closing in the signal direction.
    This prevents entering when the 1H candle is still forming against us.
    """
    data = get_ohlcv(symbol, interval="30min", bars=5)
    if not data or len(data["closes"]) < 3:
        return False, "No 30min data"
    
    opens  = data["opens"]
    closes = data["closes"]
    highs  = data["highs"]
    lows   = data["lows"]
    
    # Current 1H candle (last one)
    current_open  = opens[-1]
    current_close = closes[-1]
    prev_open     = opens[-2]
    prev_close    = closes[-2]
    
    # Candle body size
    current_body  = abs(current_close - current_open)
    prev_body     = abs(prev_close - prev_open)
    avg_body      = mean([abs(closes[i]-opens[i]) for i in range(len(closes))])
    
    if signal_type == "BUY":
        # Current 1H candle must be bullish (closing higher than open)
        candle_bullish = current_close > current_open
        # Previous candle also bullish or current candle is strong
        strong_move    = current_body > avg_body * 0.7
        # Price not in a small range (consolidating)
        not_ranging    = current_body > avg_body * 0.3
        
        if candle_bullish and strong_move:
            return True, "✅ 30min candle confirming BUY — bullish close with strong body"
        elif candle_bullish and not_ranging:
            return True, "✅ 30min candle bullish — moderate confirmation"
        else:
            return False, "❌ 30min candle NOT confirming BUY — wait for bullish 30min close"
    
    elif signal_type == "SELL":
        # Current 1H candle must be bearish
        candle_bearish = current_close < current_open
        strong_move    = current_body > avg_body * 0.7
        not_ranging    = current_body > avg_body * 0.3
        
        if candle_bearish and strong_move:
            return True, "✅ 30min candle confirming SELL — bearish close with strong body"
        elif candle_bearish and not_ranging:
            return True, "✅ 30min candle bearish — moderate confirmation"
        else:
            return False, "❌ 30min candle NOT confirming SELL — wait for bearish 30min close"
    
    return False, "No confirmation"

def check_structure_break(highs, lows, closes, signal_type, lookback=5):
    """
    Check if price has just broken a recent swing high (BUY) or swing low (SELL).
    A structure break means momentum is actually there right now.
    """
    if len(closes) < lookback + 2:
        return False, "Not enough data"
    
    recent_highs = highs[-(lookback+1):-1]
    recent_lows  = lows[-(lookback+1):-1]
    current      = closes[-1]
    
    if signal_type == "BUY":
        # Price must have just broken above recent swing high
        prev_high = max(recent_highs)
        if current > prev_high:
            return True, f"✅ Structure break — price broke above recent high ({round(prev_high,2)})"
        else:
            return False, f"⚠️ No structure break yet — recent high at {round(prev_high,2)} not broken"
    
    elif signal_type == "SELL":
        # Price must have just broken below recent swing low
        prev_low = min(recent_lows)
        if current < prev_low:
            return True, f"✅ Structure break — price broke below recent low ({round(prev_low,2)})"
        else:
            return False, f"⚠️ No structure break yet — recent low at {round(prev_low,2)} not broken"
    
    return False, "No structure break"

def check_consolidation(highs, lows, closes, lookback=6):
    """
    Detect if price is just ranging/consolidating.
    We don't want to enter during consolidation — wait for a breakout.
    """
    if len(closes) < lookback:
        return False
    
    recent_highs = highs[-lookback:]
    recent_lows  = lows[-lookback:]
    price_range  = max(recent_highs) - min(recent_lows)
    current      = closes[-1]
    
    # If range is less than 0.3% of price — it's consolidating
    is_consolidating = price_range / max(current, 0.0001) < 0.003
    return is_consolidating

def get_precise_entry(highs, lows, closes, opens, signal_type, fvg_data, sr_data, atr_val):
    """
    Find the most precise entry point:
    - For BUY: enter at nearest demand zone, FVG bottom, or support level
    - For SELL: enter at nearest supply zone, FVG top, or resistance level
    This gives a better entry than just entering at market price.
    """
    current = closes[-1]
    
    if signal_type == "BUY":
        candidates = []
        # Add FVG bottoms as entry candidates
        for fvg in fvg_data.get("bull_fvgs", []):
            if fvg["bottom"] <= current <= fvg["top"]:
                candidates.append(("FVG Bottom", fvg["bottom"]))
        # Add support levels
        for sup in sr_data.get("nearest_support", []):
            if abs(current - sup) / max(current, 0.0001) < 0.005:
                candidates.append(("Support", sup))
        
        if candidates:
            # Use the highest candidate (closest to current price for buys)
            best = max(candidates, key=lambda x: x[1])
            return round(current, 4), f"Entry at market ({best[0]} zone: {round(best[1],4)})"
        return round(current, 4), "Entry at market price"
    
    elif signal_type == "SELL":
        candidates = []
        for fvg in fvg_data.get("bear_fvgs", []):
            if fvg["bottom"] <= current <= fvg["top"]:
                candidates.append(("FVG Top", fvg["top"]))
        for res in sr_data.get("nearest_resistance", []):
            if abs(current - res) / max(current, 0.0001) < 0.005:
                candidates.append(("Resistance", res))
        
        if candidates:
            best = min(candidates, key=lambda x: x[1])
            return round(current, 4), f"Entry at market ({best[0]} zone: {round(best[1],4)})"
        return round(current, 4), "Entry at market price"
    
    return round(current, 4), "Entry at market"


# ============================================================
#  LIQUIDITY SWEEP DETECTION
# ============================================================
def detect_liquidity_sweep(highs, lows, closes, opens, signal_type):
    """
    Liquidity sweeps happen when price briefly spikes past a key level
    to grab stop losses, then reverses sharply.
    
    Bullish sweep: price wicks BELOW recent low then closes ABOVE it = smart money grabbed liquidity, now going up
    Bearish sweep: price wicks ABOVE recent high then closes BELOW it = smart money grabbed liquidity, now going down
    
    This is one of the most reliable entry signals in Smart Money trading.
    """
    if len(closes) < 10:
        return False, "Not enough data", 0

    # Look at last 3 candles for sweep
    for i in range(-3, 0):
        candle_open  = opens[i]
        candle_close = closes[i]
        candle_high  = highs[i]
        candle_low   = lows[i]
        
        # Recent swing levels (last 5-10 candles before this one)
        lookback_highs = highs[i-8:i]
        lookback_lows  = lows[i-8:i]
        
        if not lookback_highs or not lookback_lows:
            continue
            
        recent_high = max(lookback_highs)
        recent_low  = min(lookback_lows)
        
        if signal_type == "BUY":
            # Bullish liquidity sweep:
            # Candle wicked BELOW recent low BUT closed ABOVE it
            swept_low    = candle_low < recent_low
            closed_above = candle_close > recent_low
            bullish_close = candle_close > candle_open  # Closed bullish
            
            wick_size = recent_low - candle_low
            body_size = abs(candle_close - candle_open)
            
            if swept_low and closed_above and bullish_close:
                sweep_strength = min(int((wick_size / max(body_size, 0.0001)) * 10), 3)
                return True, f"✅ LIQUIDITY SWEEP DETECTED — price swept below {round(recent_low,2)} then reversed bullish! Smart money grabbed stops, move UP likely NOW", sweep_strength
                
        elif signal_type == "SELL":
            # Bearish liquidity sweep:
            # Candle wicked ABOVE recent high BUT closed BELOW it
            swept_high   = candle_high > recent_high
            closed_below = candle_close < recent_high
            bearish_close = candle_close < candle_open  # Closed bearish
            
            wick_size = candle_high - recent_high
            body_size = abs(candle_close - candle_open)
            
            if swept_high and closed_below and bearish_close:
                sweep_strength = min(int((wick_size / max(body_size, 0.0001)) * 10), 3)
                return True, f"✅ LIQUIDITY SWEEP DETECTED — price swept above {round(recent_high,2)} then reversed bearish! Smart money grabbed stops, move DOWN likely NOW", sweep_strength

    return False, "No liquidity sweep detected recently", 0

def build_plain_english_explanation(signal_type, trend, fvg, bpr, sr, sd, fib, sweep_detected, sweep_msg, h1_msg, struct_msg, instrument):
    """
    Build a plain English explanation of WHY to take this trade
    so the trader can look at their chart and see exactly what the bot sees.
    """
    direction = "UP" if signal_type == "BUY" else "DOWN"
    action    = "BUY" if signal_type == "BUY" else "SELL"
    
    lines = []
    lines.append(f"📖 WHAT TO LOOK FOR ON YOUR {instrument} CHART:")
    lines.append("")
    
    # Trend explanation
    lines.append(f"1️⃣  TREND: The overall trend is {trend['trend']} ({trend['strength']})")
    if signal_type == "BUY":
        lines.append(f"   → Price is above the EMA200 ({trend['ema200']}) — this means the big picture is UP")
        lines.append(f"   → We are looking for a BUY opportunity in the direction of the trend")
    else:
        lines.append(f"   → Price is below the EMA200 ({trend['ema200']}) — this means the big picture is DOWN")
        lines.append(f"   → We are looking for a SELL opportunity in the direction of the trend")
    lines.append("")
    
    # Liquidity sweep
    if sweep_detected:
        lines.append(f"2️⃣  LIQUIDITY SWEEP (Most important!):")
        lines.append(f"   → {sweep_msg}")
        lines.append(f"   → On your chart you should see a LONG WICK candle that poked below/above a level then came back")
        lines.append(f"   → This is your confirmation that smart money has made their move")
        lines.append("")
    
    # Key zone
    if bpr["price_in_bpr"] or bpr["near_bpr"]:
        lines.append(f"3️⃣  KEY ZONE: Price is in a BALANCED PRICE RANGE (BPR)")
        lines.append(f"   → This is an area where buyers AND sellers previously fought hard")
        lines.append(f"   → Price respects these zones strongly — look for the reaction HERE")
        lines.append("")
    elif fvg["price_in_bull"] and signal_type == "BUY":
        lines.append(f"3️⃣  KEY ZONE: Price is inside a BULLISH FAIR VALUE GAP")
        lines.append(f"   → On your chart look for a gap between two candles where price is trading now")
        lines.append(f"   → This gap acts like a magnet — price fills it then continues UP")
        lines.append("")
    elif fvg["price_in_bear"] and signal_type == "SELL":
        lines.append(f"3️⃣  KEY ZONE: Price is inside a BEARISH FAIR VALUE GAP")
        lines.append(f"   → On your chart look for a gap between two candles where price is trading now")
        lines.append(f"   → This gap acts like a magnet — price fills it then continues DOWN")
        lines.append("")
    elif sd["in_demand"] and signal_type == "BUY":
        lines.append(f"3️⃣  KEY ZONE: Price is in a DEMAND ZONE")
        lines.append(f"   → Look for an area on your chart where price previously shot up aggressively")
        lines.append(f"   → Price has returned to this zone — buyers should defend it here")
        lines.append("")
    elif sd["in_supply"] and signal_type == "SELL":
        lines.append(f"3️⃣  KEY ZONE: Price is in a SUPPLY ZONE")
        lines.append(f"   → Look for an area on your chart where price previously dropped aggressively")
        lines.append(f"   → Price has returned to this zone — sellers should defend it here")
        lines.append("")
    
    # Fibonacci
    if fib["in_golden"]:
        lines.append(f"4️⃣  FIBONACCI: Price is at the 61.8% GOLDEN ZONE ⭐")
        lines.append(f"   → On your chart, draw a Fibonacci from the last swing low to swing high")
        lines.append(f"   → The 61.8% level ({fib['levels'].get('61.8% Golden Zone','')}) is where you should see price reacting")
        lines.append(f"   → This is the strongest retracement level — used by professional traders worldwide")
        lines.append("")
    
    # 1H confirmation
    lines.append(f"5️⃣  30MIN CANDLE: {h1_msg}")
    lines.append(f"   → Switch your MT5 chart to the 30 MINUTE timeframe")
    if signal_type == "BUY":
        lines.append(f"   → You should see the current 30min candle closing as a BULLISH (green) candle")
    else:
        lines.append(f"   → You should see the current 30min candle closing as a BEARISH (red) candle")
    lines.append("")
    
    # What to do
    lines.append(f"✅  WHAT TO DO:")
    lines.append(f"   → Open MT5 → go to {instrument} → switch to 15 MIN chart")
    lines.append(f"   → Look for everything described above")
    lines.append(f"   → If YOU can see the same setup — {action} with the stop loss and take profit in the email")
    lines.append(f"   → If you CANNOT see it clearly — DO NOT enter. Wait for the next signal.")
    lines.append("")
    lines.append(f"⚠️  REMEMBER: The bot shows you WHERE and WHY. YOU decide if you see it. Never trade blind.")
    
    return lines


# ============================================================
#  KEY LEVEL PROXIMITY DETECTION
# ============================================================
def check_key_level_proximity(price, sr, fib, fvg, bpr, sd):
    """
    Detect if price is currently sitting at ANY major level that
    could influence the market, regardless of whether a full signal exists.
    This gives the trader a heads-up that something important is happening NOW.
    """
    alerts = []

    # Support/Resistance
    if sr["nearest_support"] and abs(price - sr["nearest_support"][0]) / max(price,1) < 0.003:
        alerts.append(f"📍 Price is sitting AT a key SUPPORT level ({round(sr['nearest_support'][0],2)}) — watch for a bounce or break")
    if sr["nearest_resistance"] and abs(price - sr["nearest_resistance"][0]) / max(price,1) < 0.003:
        alerts.append(f"📍 Price is sitting AT a key RESISTANCE level ({round(sr['nearest_resistance'][0],2)}) — watch for a rejection or break")

    # Fibonacci Golden Zone
    if fib["in_golden"]:
        golden_level = fib["levels"].get("61.8% Golden Zone", 0)
        alerts.append(f"📐 Price is AT the Fibonacci 61.8% Golden Zone ({golden_level}) — historically the strongest reaction level")

    # FVG
    if fvg["price_in_bull"]:
        alerts.append(f"🏦 Price is INSIDE a Bullish Fair Value Gap — institutional zone, expect a reaction")
    if fvg["price_in_bear"]:
        alerts.append(f"🏦 Price is INSIDE a Bearish Fair Value Gap — institutional zone, expect a reaction")

    # BPR
    if bpr["price_in_bpr"]:
        alerts.append(f"⚖️ Price is INSIDE a Balanced Price Range (BPR) — strong confluence zone, high probability of reaction")

    # Supply/Demand
    if sd["in_demand"]:
        alerts.append(f"📦 Price has returned to a DEMAND zone — buyers previously defended this area")
    if sd["in_supply"]:
        alerts.append(f"📦 Price has returned to a SUPPLY zone — sellers previously defended this area")

    return alerts

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
        # No trend-based signal, but check if price is sitting at an important level anyway
        key_alerts = check_key_level_proximity(price, sr, fib, fvg, bpr, sd)
        if key_alerts:
            print(f"      📍 No trend signal, but key level activity detected")
            return {
                "alert_type":   "KEY_LEVEL",
                "instrument":   name,
                "price":        round(price, 4),
                "trend":        trend,
                "key_alerts":   key_alerts,
                "timestamp":    datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            }
        print(f"      No signal ({min(confidence,100)}% confidence — below 50% baseline)")
        return None

    # ── WATCH ALERT CHECK (50-69% = building setup, not yet confirmed) ─
    pre_confirm_confidence = min(confidence, 100)
    if pre_confirm_confidence < CONFIG["min_confidence"]:
        # Not yet at full confidence — check if it qualifies for a WATCH alert
        watch_key = f"{name}_{signal_type}_{datetime.utcnow().strftime('%Y%m%d%H')}"
        if pre_confirm_confidence >= 50 and watch_key not in WATCH_LOG:
            WATCH_LOG.append(watch_key)
            # Keep log small
            if len(WATCH_LOG) > 50:
                WATCH_LOG.pop(0)
            print(f"      🔔 WATCH-LEVEL setup forming ({pre_confirm_confidence}%) — sending early heads-up")
            return {
                "alert_type":   "WATCH",
                "instrument":   name,
                "signal":       signal_type,
                "price":        round(price, 4),
                "confidence":   pre_confirm_confidence,
                "trend":        trend,
                "fib":          fib,
                "fvg":          fvg,
                "bpr":          bpr,
                "sd":           sd,
                "reasons":      reasons,
                "timestamp":    datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            }
        print(f"      No signal ({pre_confirm_confidence}% confidence — below {CONFIG['min_confidence']}% threshold)")
        return None

    # ── CONSOLIDATION CHECK ────────────────────────────────
    if check_consolidation(highs, lows, closes):
        print(f"      ⏸️  Price consolidating — skipping, waiting for breakout")
        return None

    # ── STRUCTURE BREAK CHECK ─────────────────────────────
    struct_ok, struct_msg = check_structure_break(highs, lows, closes, signal_type)
    if struct_ok:
        confidence += 8
        print(f"      {struct_msg}")
    else:
        confidence -= 10
        print(f"      {struct_msg}")

    # ── 1H CANDLE CONFIRMATION ────────────────────────────
    print(f"      🕯️  Checking 30min candle confirmation...")
    h1_confirmed, h1_msg = check_30min_candle_confirmation(symbol, signal_type)
    if not h1_confirmed:
        print(f"      {h1_msg} — entry timing not right, skipping")
        return None
    confidence += 10
    print(f"      {h1_msg}")

    # Final confidence check
    confidence = min(confidence, 100)
    if confidence < CONFIG["min_confidence"]:
        print(f"      Dropped below {CONFIG['min_confidence']}% after confirmations ({confidence}%) — skipping")
        return None

    # ── PRECISE ENTRY ─────────────────────────────────────
    entry_price, entry_note = get_precise_entry(highs, lows, closes, opens, signal_type, fvg, sr, atr_val)

    # ── LIQUIDITY SWEEP CHECK ─────────────────────────────
    print(f"      🌊 Checking for liquidity sweep...")
    sweep_detected, sweep_msg, sweep_bonus = detect_liquidity_sweep(highs, lows, closes, opens, signal_type)
    if sweep_detected:
        confidence = min(confidence + (sweep_bonus * 5), 100)
        print(f"      {sweep_msg}")
    else:
        print(f"      {sweep_msg}")

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

    reasons.append(struct_msg)
    reasons.append(h1_msg)
    print(f"      🎯 {signal_type} | {confidence}% | {quality}")

    sig = {
        "instrument":  name,
        "entry_note":  entry_note,
        "sweep_detected": sweep_detected,
        "sweep_msg":   sweep_msg,
        "plain_english": build_plain_english_explanation(signal_type, trend, fvg, bpr, sr, sd, fib, sweep_detected, sweep_msg, h1_msg, struct_msg, name),
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

                {"<div style=\'background:#1a2a00;border:2px solid #ffd700;border-radius:8px;padding:15px;margin-bottom:10px;\'><strong style=\'color:#ffd700;font-size:16px;\'>🌊 LIQUIDITY SWEEP CONFIRMED!</strong><br><br><span style=\'color:#e6edf3;\'>" + s["sweep_msg"] + "</span></div>" if s.get("sweep_detected") else ""}

                <div style='background:#0a1628;border:1px solid #58a6ff;border-radius:8px;padding:15px;margin-bottom:10px;'>
                    <strong style='color:#58a6ff;font-size:15px;'>📖 HOW TO READ THIS ON YOUR MT5 CHART</strong><br><br>
                    {"<br>".join(s.get("plain_english", []))}
                </div>

                <div style='background:#0d1117;padding:12px;border-radius:6px;margin-bottom:10px;'>
                    <strong style='color:#58a6ff;'>🧠 Technical reasons ({len(s["reasons"])}):</strong><br><br>
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

def build_watch_email(alerts):
    subject = f"🔔 WATCH Alert — {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')} | " + " | ".join([f"{a['instrument']} {a['signal']}" for a in alerts])

    html = """<html><body style='font-family:Arial,sans-serif;background:#0d1117;
    color:#e6edf3;padding:20px;max-width:720px;margin:auto;'>
    <h2 style='color:#ffa657;border-bottom:2px solid #30363d;padding-bottom:10px;'>
    🔔 WATCH ALERT — Setup Forming (Not Confirmed Yet)</h2>
    <p style='color:#8b949e;'>These setups are building but have not fully confirmed. 
    Open your chart and watch closely — a CONFIRMED signal may follow shortly if conditions align.</p>"""

    for a in alerts:
        color = "#3fb950" if a["signal"] == "BUY" else "#f85149"
        bg    = "#1a2410" if a["signal"] == "BUY" else "#2a1410"
        emoji = "🟢" if a["signal"] == "BUY" else "🔴"

        html += f"""
        <div style='background:{bg};border:2px dashed {color};border-radius:10px;padding:18px;margin-bottom:20px;'>
            <h3 style='color:{color};margin:0 0 5px 0;'>{emoji} {a['instrument']} — Possible {a['signal']} forming</h3>
            <p style='color:#8b949e;margin:0 0 12px 0;font-size:12px;'>{a['timestamp']} | Confidence so far: {a['confidence']}%</p>
            <p style='color:#e6edf3;margin:0 0 10px 0;'>Current price: <strong>{a['price']}</strong></p>
            <div style='background:#0d1117;padding:10px;border-radius:6px;margin-bottom:8px;'>
                <strong style='color:#ffa657;'>What's building:</strong><br><br>
                {"<br>".join(a['reasons'])}
            </div>
            <div style='background:#1a1500;border-left:3px solid #ffd700;padding:10px;border-radius:4px;'>
                <strong style='color:#ffd700;'>👀 Watch this zone closely on your {a['instrument']} chart now.</strong>
                <span style='color:#8b949e;'> Do NOT enter yet — wait for the CONFIRMED email or watch for a strong reaction at this level yourself.</span>
            </div>
        </div>"""

    html += """<p style='color:#30363d;font-size:11px;margin-top:15px;'>
    This is an early heads-up only, not a trade signal. Wait for confirmation or your own chart analysis.</p>
    </body></html>"""

    return subject, html

def send_watch_email(alerts):
    subject, html = build_watch_email(alerts)
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = CONFIG["sender_email"]
        msg["To"]      = CONFIG["receiver_email"]
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(CONFIG["sender_email"], CONFIG["sender_password"])
            s.sendmail(CONFIG["sender_email"], CONFIG["receiver_email"], msg.as_string())
        print(f"   ✅ Watch email sent!")
    except Exception as e:
        print(f"   ❌ Watch email error: {e}")


def build_key_level_email(alerts):
    subject = f"📍 Key Level Alert — {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')} | " + ", ".join([a['instrument'] for a in alerts])

    html = """<html><body style='font-family:Arial,sans-serif;background:#0d1117;
    color:#e6edf3;padding:20px;max-width:720px;margin:auto;'>
    <h2 style='color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px;'>
    📍 Price At Key Level — No Trend Signal Yet</h2>
    <p style='color:#8b949e;'>No directional trade signal right now, but price is sitting at a level 
    that could influence the market. Worth a glance at your chart.</p>"""

    for a in alerts:
        html += f"""
        <div style='background:#1c2128;border:1px solid #30363d;border-radius:10px;padding:18px;margin-bottom:18px;'>
            <h3 style='color:#58a6ff;margin:0 0 5px 0;'>{a['instrument']}</h3>
            <p style='color:#8b949e;margin:0 0 12px 0;font-size:12px;'>{a['timestamp']} | Price: {a['price']} | Trend: {a['trend']['trend']}</p>
            <div style='background:#0d1117;padding:10px;border-radius:6px;'>
                {"<br>".join(a['key_alerts'])}
            </div>
        </div>"""

    html += """<p style='color:#30363d;font-size:11px;margin-top:15px;'>
    Informational only — not a trade signal. Wait for trend alignment before considering any entry.</p>
    </body></html>"""

    return subject, html

def send_key_level_email(alerts):
    subject, html = build_key_level_email(alerts)
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = CONFIG["sender_email"]
        msg["To"]      = CONFIG["receiver_email"]
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(CONFIG["sender_email"], CONFIG["sender_password"])
            s.sendmail(CONFIG["sender_email"], CONFIG["receiver_email"], msg.as_string())
        print(f"   ✅ Key level email sent!")
    except Exception as e:
        print(f"   ❌ Key level email error: {e}")

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
        all_signals  = []
        watch_alerts = []
        key_level_alerts = []
        for name, symbol in INSTRUMENTS.items():
            try:
                sig = generate_signal(name, symbol)
                if sig and sig.get("alert_type") == "WATCH":
                    watch_alerts.append(sig)
                elif sig and sig.get("alert_type") == "KEY_LEVEL":
                    key_level_alerts.append(sig)
                elif sig:
                    all_signals.append(sig)
            except Exception as e:
                print(f"   ❌ {name} error: {e}")

        # Sort by confidence and take top signals only
        all_signals.sort(key=lambda x: x["confidence"], reverse=True)
        signals = all_signals[:CONFIG["max_signals_per_scan"]]

        if signals:
            print(f"\n   📧 Sending {len(signals)} CONFIRMED signal(s)...")
            send_email(signals, [])
        elif watch_alerts:
            print(f"\n   🔔 Sending {len(watch_alerts)} WATCH alert(s)...")
            send_watch_email(watch_alerts)
        elif key_level_alerts:
            print(f"\n   📍 Sending {len(key_level_alerts)} key level alert(s)...")
            send_key_level_email(key_level_alerts)
        else:
            print("   💤 No signals met the 70% threshold — no email sent")

        next_scan = (now + timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%H:%M UTC")
        print(f"   ⏳ Next scan: {next_scan}")
        time.sleep(CONFIG["scan_interval_minutes"] * 60)

if __name__ == "__main__":
    run_bot()
