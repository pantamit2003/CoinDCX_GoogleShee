"""
support_resistance.py
=======================
NAYA STANDALONE MODULE — support/resistance levels nikaalne ke liye.
intraday_spike_monitor.py isko import karke use karega.

LOGIC:
1. Pichle N candles (default 50) mein "swing highs" aur "swing lows"
   dhoondo — matlab wo candles jinka High/Low apne 2 pados wali
   candles (dono taraf) se zyada/kam ho.
2. Nazdeek wale swing points ko "cluster" kar do — taaki 3-4 alag
   swing highs jo 0.5% ke andar hain, ek hi resistance zone maane jayein.
3. Jab naya spike detect ho, uske Close price ko in levels se compare
   karo aur ek label do:
   - NEAR_RESISTANCE          → resistance ke 0.5% andar
   - BREAKOUT_ABOVE_RESISTANCE → resistance ko todke upar nikal gaya
   - NEAR_SUPPORT             → support ke 0.5% andar
   - BREAKDOWN_BELOW_SUPPORT   → support ko todke neeche gaya
   - MID_RANGE                → kisi bhi level ke paas nahi

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

    return [sum(c) / len(c) for c in clusters]


def get_support_resistance(df, lookback=50, cluster_tolerance_pct=0.5):
    """
    Poora S/R pipeline: swing points dhoondo, cluster karo, clean
    resistance/support levels return karo.

    Return: (resistance_levels: list of float, support_levels: list of float)
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

    Return: ek label string:
        NEAR_RESISTANCE, BREAKOUT_ABOVE_RESISTANCE,
        NEAR_SUPPORT, BREAKDOWN_BELOW_SUPPORT, MID_RANGE
    """
    # Sabse nazdeek resistance aur support dhoondo (upar aur neeche)
    resistances_above = [r for r in resistance_levels if r >= prev_close]
    supports_below = [s for s in support_levels if s <= prev_close]

    nearest_resistance = min(resistances_above) if resistances_above else None
    nearest_support = max(supports_below) if supports_below else None

    # BREAKOUT: pichli candle resistance ke neeche thi, ab close usse upar hai
    if nearest_resistance is not None and prev_close < nearest_resistance <= close:
        return "BREAKOUT_ABOVE_RESISTANCE"

    # BREAKDOWN: pichli candle support ke upar thi, ab close usse neeche hai
    if nearest_support is not None and prev_close > nearest_support >= close:
        return "BREAKDOWN_BELOW_SUPPORT"

    # NEAR_RESISTANCE: close, resistance ke proximity_pct% ke andar hai (neeche se)
    if nearest_resistance is not None:
        pct_to_resistance = abs(nearest_resistance - close) / close * 100
        if pct_to_resistance <= proximity_pct:
            return "NEAR_RESISTANCE"

    # NEAR_SUPPORT: close, support ke proximity_pct% ke andar hai (upar se)
    if nearest_support is not None:
        pct_to_support = abs(close - nearest_support) / close * 100
        if pct_to_support <= proximity_pct:
            return "NEAR_SUPPORT"

    return "MID_RANGE"
