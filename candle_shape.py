"""
candle_shape.py
=================
NAYA MODULE — YouTube video (Dominance/Rejection/Confusion candlestick
concept) ko measurable, code-friendly logic mein convert karta hai.

TEEN CATEGORIES (video ke concept se):
    DOMINANCE — bada body, chhoti wicks. Ek side (buyer/seller) ne
                poori candle control ki. Strong directional signal.
    REJECTION — ek taraf ki wick lambi hai, body chhota-medium hai.
                Price ek direction try karke wapas dhakela gaya.
    CONFUSION — chhota body, dono taraf wicks. Koi clear winner nahi,
                buyer/seller balance mein hain.

KAISE USE HOGA:
    Har spike-candle ko classify karenge — taaki pata chale ki jo
    RVOL-spike hui hai, wo genuinely "Dominance" (strong, trust-worthy)
    thi ya "Confusion" (weak, kam bharosemand).
"""


def classify_candle_shape(open_price, high, low, close):
    """
    Ek candle ke OHLC values leke, uska shape classify karta hai.

    Return dict:
        {
            "shape": "DOMINANCE" / "REJECTION" / "CONFUSION",
            "direction": "GREEN" / "RED",
            "body_pct": float (0-100),
            "strength": "STRONG" / "LENIENT" (sirf REJECTION ke liye meaningful),
            "rejection_side": "UPPER" / "LOWER" / None,
        }
    """
    body = abs(close - open_price)
    candle_range = high - low
    direction = "GREEN" if close >= open_price else "RED"

    if candle_range == 0:
        # Bilkul flat candle — koi movement hi nahi hui
        return {
            "shape": "CONFUSION",
            "direction": direction,
            "body_pct": 0.0,
            "strength": "LENIENT",
            "rejection_side": None,
        }

    body_pct = round((body / candle_range) * 100, 1)

    upper_wick = high - max(open_price, close)
    lower_wick = min(open_price, close) - low
    upper_wick_pct = (upper_wick / candle_range) * 100
    lower_wick_pct = (lower_wick / candle_range) * 100

    # ---- DOMINANCE: body candle ka bada hissa hai ----
    if body_pct >= 65:
        return {
            "shape": "DOMINANCE",
            "direction": direction,
            "body_pct": body_pct,
            "strength": "STRONG" if body_pct >= 80 else "LENIENT",
            "rejection_side": None,
        }

    # ---- REJECTION: ek taraf ki wick lambi hai (dusri se kaafi zyada) ----
    if upper_wick_pct >= 45 and upper_wick_pct > lower_wick_pct * 1.5:
        return {
            "shape": "REJECTION",
            "direction": direction,
            "body_pct": body_pct,
            "strength": "STRONG" if upper_wick_pct >= 65 else "LENIENT",
            "rejection_side": "UPPER",   # upar se price reject hui (bearish sign)
        }

    if lower_wick_pct >= 45 and lower_wick_pct > upper_wick_pct * 1.5:
        return {
            "shape": "REJECTION",
            "direction": direction,
            "body_pct": body_pct,
            "strength": "STRONG" if lower_wick_pct >= 65 else "LENIENT",
            "rejection_side": "LOWER",   # neeche se price reject hui (bullish sign)
        }

    # ---- Baaki sab CONFUSION (chhota body, dono taraf wicks, koi clear winner nahi) ----
    return {
        "shape": "CONFUSION",
        "direction": direction,
        "body_pct": body_pct,
        "strength": "LENIENT",
        "rejection_side": None,
    }
