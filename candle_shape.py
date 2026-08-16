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
    CONFUSION — chhota body, dono taraf MEANINGFUL aur BALANCED wicks.
                Koi clear winner nahi, buyer/seller balance mein hain.

KAISE USE HOGA:
    Har spike-candle ko classify karenge — taaki pata chale ki jo
    RVOL-spike hui hai, wo genuinely "Dominance" (strong, trust-worthy)
    thi ya "Confusion" (weak, kam bharosemand).

CHANGE LOG (CONFUSION tightening):
    Pehle: DOMINANCE aur REJECTION dono na banne par candle seedha
    CONFUSION ban jaati thi — isse false CONFUSION signals aate the.

    Ab: CONFUSION sirf tabhi milega jab candle:
        1. body_pct <= 35%                         (genuinely small body)
        2. upper_wick_pct >= 20%                    (upper wick meaningful)
        3. lower_wick_pct >= 20%                    (lower wick meaningful)
        4. max(upper, lower) <= min(upper, lower)*2 (dono wicks balanced)
    In sab conditions fail hone par candle CONFUSION nahi banegi — us
    case mein jo wick bigger hai uske hisaab se ek LENIENT REJECTION
    diya jaata hai (kyunki na to body itna bada hai ki DOMINANCE ho, na
    wicks itni balanced hain ki genuinely CONFUSION ho — is beech ki
    candle ko ek "weak lean" ke roop mein REJECTION treat karna zyada
    accurate hai).

    Classification order bhi is tarah rakha gaya hai ki STRONG/LENIENT
    REJECTION (existing 45%/1.5x wali dominant-wick check) hamesha
    CONFUSION check se PEHLE evaluate ho — taaki koi candle jo clearly
    REJECTION qualify karti hai, wo galti se CONFUSION na ban jaaye.
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

    # ---- REJECTION (STRONG/LENIENT): ek taraf ki wick lambi hai
    #      (dusri se kaafi zyada). Yeh check CONFUSION check se pehle
    #      hai taaki clear rejection candles galti se CONFUSION na bane. ----
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

    # ---- CONFUSION (TIGHTENED): sirf genuinely small body +
    #      dono taraf meaningful aur balanced wicks. ----
    is_body_small = body_pct <= 35
    is_upper_meaningful = upper_wick_pct >= 20
    is_lower_meaningful = lower_wick_pct >= 20
    bigger_wick = max(upper_wick_pct, lower_wick_pct)
    smaller_wick = min(upper_wick_pct, lower_wick_pct)
    # smaller_wick == 0 ho to balance test fail maana jaayega (excessively
    # dominant ek taraf), is_upper/lower_meaningful checks waise bhi isko
    # rok denge, lekin explicit rakhte hain safety ke liye.
    is_balanced = smaller_wick > 0 and bigger_wick <= smaller_wick * 2

    if is_body_small and is_upper_meaningful and is_lower_meaningful and is_balanced:
        return {
            "shape": "CONFUSION",
            "direction": direction,
            "body_pct": body_pct,
            "strength": "LENIENT",
            "rejection_side": None,
        }

    # ---- FALLBACK: na DOMINANCE, na STRONG/LENIENT REJECTION, na
    #      genuinely balanced CONFUSION. Yeh "beech ki" candle hai —
    #      body itna bada nahi ki dominance ho, wicks itni balanced
    #      nahi ki confusion ho. Jo wick bigger hai uske hisaab se
    #      ek LENIENT rejection lean diya jaata hai (CONFUSION default
    #      hata diya gaya hai). ----
    rejection_side = "UPPER" if upper_wick_pct >= lower_wick_pct else "LOWER"
    return {
        "shape": "REJECTION",
        "direction": direction,
        "body_pct": body_pct,
        "strength": "LENIENT",
        "rejection_side": rejection_side,
    }
