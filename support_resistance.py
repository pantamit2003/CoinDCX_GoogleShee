"""
support_resistance.py  (v3 — pre-breakout consolidation + liquidity zone)
=======================
NAYA STANDALONE MODULE — support/resistance levels nikaalne ke liye.
intraday_spike_monitor.py isko import karke use karega.
LOGIC:
1. Pichle N candles (default 50) mein "swing highs" aur "swing lows"
   dhoondo — matlab wo candles jinka High/Low apne 2 pados wali
   candles (dono taraf) se zyada/kam ho.
2. Nazdeek wale swing points ko "cluster" kar do — taaki 3-4 alag
   swing highs jo 0.5% ke andar hain, ek hi resistance zone maane jayein.
   Har cluster mein kitne swing points aaye, wo bhi count karte hain —
   yehi uska "touch count" hai (level kitni baar respect hua).
3. Jab naya spike detect ho, uske Close price ko in levels se compare
   karo aur ek label do:
   - NEAR_RESISTANCE           → resistance ke 0.5% andar
   - BREAKOUT_ABOVE_RESISTANCE → resistance ko todke upar nikal gaya
   - NEAR_SUPPORT              → support ke 0.5% andar
   - BREAKDOWN_BELOW_SUPPORT   → support ko todke neeche gaya
   - MID_RANGE                 → kisi bhi level ke paas nahi
v2 CHANGE: Ab levels sirf price nahi, balki (price, touch_count) tuples
hote hain — taaki Telegram alert mein bhi dikha sakein "ye support
kitni baar test ho chuka hai" — jitna zyada touch count, utna zyada
"proven/reliable" level maana jata hai.

v3 CHANGE (NAYA) — do naye functions, "YouTube masterclass" ke concepts
ko measurable banane ke liye:
    1. count_pre_breakout_consolidation() — breakout se PEHLE us level
       ke paas kitni candles consolidate hui thi. Video ka core insight:
       "real breakout tabhi hota hai jab breakout se pehle consolidation
       ho" — bina consolidation ke breakout fake hone ka chance zyada hai.
    2. find_next_liquidity_zone() — jo level abhi break hui, uske AAGE
       (breakout direction mein) agla level kitni door hai — video ka
       "liquidity zone / magnet" wala concept. Yeh batata hai price
       abhi bhi kitna aur "chase" kar sakta hai agle liquidity pool tak.
    Dono functions purane logic ko bilkul touch nahi karte, sirf naye
    optional helpers hain.

EXTRA API CALLS NAHI LAGTE — same candle-data use hota hai jo RVOL ke
liye already fetch ho chuka hota hai.
"""
def find_swing_points(df, window=2):
    """
    df ke 'High'/'Low' columns mein swing highs aur swing lows dhoondta hai.
    window=2 matlab candle ke dono taraf 2-2 candles se compare hota hai.
    Return: (swing_highs: list of float, swing_lows: list of float)
    """
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)
    swing_highs = []
    swing_lows = []
    for i in range(window, n - window):
        # Swing High: is candle ka High, dono taraf ki window candles
        # ke High se zyada ya barabar hai
        left_highs = highs[i - window:i]
        right_highs = highs[i + 1:i + 1 + window]
        if highs[i] >= max(left_highs) and highs[i] >= max(right_highs):
            swing_highs.append(highs[i])
        # Swing Low: is candle ka Low, dono taraf ki window candles
        # ke Low se kam ya barabar hai
        left_lows = lows[i - window:i]
        right_lows = lows[i + 1:i + 1 + window]
        if lows[i] <= min(left_lows) and lows[i] <= min(right_lows):
            swing_lows.append(lows[i])
    return swing_highs, swing_lows


def cluster_levels(levels, tolerance_pct=0.5):
    """
    Nazdeek wale price-levels ko group karke ek average level bana deta
    hai — taaki bahut saare chhote-chhote alag levels ki jagah kuch
    clean, meaningful zones milein.
    v2: Ab har cluster ka SIZE (kitne swing points usme aaye) bhi
    return karta hai — yehi "touch count" hai.
    Return: list of (avg_price: float, touch_count: int) tuples
    """
    if not levels:
        return []
    sorted_levels = sorted(levels)
    clusters = [[sorted_levels[0]]]
    for level in sorted_levels[1:]:
        last_cluster_avg = sum(clusters[-1]) / len(clusters[-1])
        pct_diff = abs(level - last_cluster_avg) / last_cluster_avg * 100
        if pct_diff <= tolerance_pct:
            clusters[-1].append(level)
        else:
            clusters.append([level])
    return [(sum(c) / len(c), len(c)) for c in clusters]


def get_support_resistance(df, lookback=50, cluster_tolerance_pct=0.5):
    """
    Poora S/R pipeline: swing points dhoondo, cluster karo, clean
    resistance/support levels return karo.
    Return: (resistance_levels, support_levels)
        Dono list of (price: float, touch_count: int) tuples hain.
    """
    recent_df = df.tail(lookback).reset_index(drop=True)
    swing_highs, swing_lows = find_swing_points(recent_df, window=2)
    resistance_levels = cluster_levels(swing_highs, cluster_tolerance_pct)
    support_levels = cluster_levels(swing_lows, cluster_tolerance_pct)
    return resistance_levels, support_levels


def classify_price_position(close, prev_close, resistance_levels, support_levels,
                             proximity_pct=0.5):
    """
    Current close price ko S/R levels ke against classify karta hai.
    resistance_levels, support_levels: list of (price, touch_count) tuples
    (get_support_resistance() se seedha aate hain)
    Return: dict:
        {
            "label": "NEAR_RESISTANCE" / "BREAKOUT_ABOVE_RESISTANCE" /
                     "NEAR_SUPPORT" / "BREAKDOWN_BELOW_SUPPORT" / "MID_RANGE",
            "level_price": float ya None (jo level relevant hai us candle ke liye),
            "touch_count": int (0 agar koi relevant level nahi mila),
        }
    """
    # Sabse nazdeek resistance aur support dhoondo (upar aur neeche)
    resistances_above = [r for r in resistance_levels if r[0] >= prev_close]
    supports_below = [s for s in support_levels if s[0] <= prev_close]
    nearest_resistance = min(resistances_above, key=lambda x: x[0]) if resistances_above else None
    nearest_support = max(supports_below, key=lambda x: x[0]) if supports_below else None
    # BREAKOUT: pichli candle resistance ke neeche thi, ab close usse upar hai
    if nearest_resistance is not None and prev_close < nearest_resistance[0] <= close:
        return {
            "label": "BREAKOUT_ABOVE_RESISTANCE",
            "level_price": round(float(nearest_resistance[0]), 8),
            "touch_count": nearest_resistance[1],
        }
    # BREAKDOWN: pichli candle support ke upar thi, ab close usse neeche hai
    if nearest_support is not None and prev_close > nearest_support[0] >= close:
        return {
            "label": "BREAKDOWN_BELOW_SUPPORT",
            "level_price": round(float(nearest_support[0]), 8),
            "touch_count": nearest_support[1],
        }
    # NEAR_RESISTANCE: close, resistance ke proximity_pct% ke andar hai (neeche se)
    if nearest_resistance is not None:
        pct_to_resistance = abs(nearest_resistance[0] - close) / close * 100
        if pct_to_resistance <= proximity_pct:
            return {
                "label": "NEAR_RESISTANCE",
                "level_price": round(float(nearest_resistance[0]), 8),
                "touch_count": nearest_resistance[1],
            }
    # NEAR_SUPPORT: close, support ke proximity_pct% ke andar hai (upar se)
    if nearest_support is not None:
        pct_to_support = abs(close - nearest_support[0]) / close * 100
        if pct_to_support <= proximity_pct:
            return {
                "label": "NEAR_SUPPORT",
                "level_price": round(float(nearest_support[0]), 8),
                "touch_count": nearest_support[1],
            }
    return {
        "label": "MID_RANGE",
        "level_price": None,
        "touch_count": 0,
    }


# ============================================
# v3 NAYA — PRE-BREAKOUT CONSOLIDATION
# ============================================
def count_pre_breakout_consolidation(df, level_price, band_pct=1.5, max_lookback=20):
    """
    Breakout-candle se PEHLE, us level ke paas kitni candles CONSECUTIVE
    consolidate hui thi — count karta hai. Video ka concept: real breakout
    ke liye pehle consolidation zaroori hai, warna fake hone ka risk zyada.

    df ki AAKHRI row current (breakout) candle maani jaati hai — usse
    EXCLUDE karke, uske TURANT PEHLE wali candles check hoti hain,
    peeche ki taraf, jab tak koi candle level ke band se bahar na mile.

    Ek candle "consolidating" mani jaati hai agar uska poora High-Low
    range level ke ±band_pct% ke andar hi rahe (matlab price wahan se
    door nahi bhatka).

    Params:
        df: candle dataframe (High/Low columns chahiye)
        level_price: jis level ke against check karna hai
        band_pct: kitna % tolerance (default 1.5% — proximity_pct se
                  wider rakha hai jaan-boojh kar, kyunki consolidation
                  ek chhota range hota hai, sirf ek precise touch nahi)
        max_lookback: zyada se zyada kitni candles peeche dekhein (safety cap)

    Return: int (0 se max_lookback tak) — kitni consecutive candles
            consolidate hui thi breakout se pehle.
    """
    if level_price is None or df.empty or len(df) < 2:
        return 0

    band = level_price * band_pct / 100
    lower = level_price - band
    upper = level_price + band

    count = 0
    # len(df)-1 = current (breakout) candle, isliye len(df)-2 se peeche shuru
    start_idx = len(df) - 2
    end_idx = max(start_idx - max_lookback, -1)
    for i in range(start_idx, end_idx, -1):
        row = df.iloc[i]
        if lower <= float(row["Low"]) and float(row["High"]) <= upper:
            count += 1
        else:
            break  # consolidation-streak yahin toot gayi

    return count


# ============================================
# v3 NAYA — NEXT LIQUIDITY ZONE
# ============================================
def find_next_liquidity_zone(close, resistance_levels, support_levels,
                              broken_level_price, direction):
    """
    Jo level abhi break hui (broken_level_price), uske AAGE (breakout
    direction mein) agla resistance/support level kitni door hai — yeh
    dhoondta hai. Video ka "liquidity zone / magnet" concept: price
    aksar agle aise zone tak "khinchta" hai jahan zyada stop-losses/
    orders pending hote hain, phir wahan se react karta hai.

    direction: "UP" (breakout hua, agla resistance dhoondo) ya
               "DOWN" (breakdown hua, agla support dhoondo)
    broken_level_price: jo level abhi cross hui — isse upar/neeche
               wala AGLA level dhoondna hai (khud broken level nahi)

    Return: dict:
        {
            "zone_price": float ya None,
            "distance_pct": float ya None (current close se kitna % door),
            "touch_count": int (us agle level ka touch count, 0 agar na mila),
        }
    """
    if direction == "UP":
        if broken_level_price is not None:
            candidates = [r for r in resistance_levels if r[0] > broken_level_price]
        else:
            candidates = [r for r in resistance_levels if r[0] > close]
        if not candidates:
            return {"zone_price": None, "distance_pct": None, "touch_count": 0}
        nearest = min(candidates, key=lambda x: x[0])
    elif direction == "DOWN":
        if broken_level_price is not None:
            candidates = [s for s in support_levels if s[0] < broken_level_price]
        else:
            candidates = [s for s in support_levels if s[0] < close]
        if not candidates:
            return {"zone_price": None, "distance_pct": None, "touch_count": 0}
        nearest = max(candidates, key=lambda x: x[0])
    else:
        return {"zone_price": None, "distance_pct": None, "touch_count": 0}

    distance_pct = round(abs(nearest[0] - close) / close * 100, 3)
    return {
        "zone_price": round(float(nearest[0]), 8),
        "distance_pct": distance_pct,
        "touch_count": nearest[1],
    }
