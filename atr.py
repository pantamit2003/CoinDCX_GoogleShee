"""
atr.py
=======
NAYA MODULE — ATR (Average True Range) calculate karta hai, jo batata hai
ki ek coin "normally" ek candle mein kitna move karta hai.

KYUN CHAHIYE:
    Target fixed hai (jaise 1-2%), lekin har coin ka "typical move" alag
    hota hai. Agar coin ka apna ATR chhota hai (jaise 0.3% per candle),
    to 1-2% target tak pahunchne mein bahut zyada candles/time lagega —
    is beech reversal ka risk badh jaata hai. Agar ATR bada hai (jaise
    1.5%), to target jaldi mil sakta hai.

FORMULA (standard True Range):
    True Range = max(
        High - Low,
        |High - Previous_Close|,
        |Low - Previous_Close|
    )
    ATR = pichle N candles ka True Range ka average
"""


def calculate_atr(df, period=14):
    """
    Candle dataframe se ATR nikalta hai.

    Parameters:
    -----------
    df : pandas DataFrame with 'High', 'Low', 'Close' columns
    period : kitne candles ka average lena hai (default 14, standard hai)

    Return: (atr_absolute: float, atr_pct: float)
        atr_absolute = raw price-units mein ATR
        atr_pct = current price ke against ATR ka % (isse hi hum "typical
                  move %" bolte hain, jo cross-coin comparable hai)
    """
    if len(df) < period + 1:
        return None, None

    df = df.copy().reset_index(drop=True)
    df["prev_close"] = df["Close"].shift(1)

    true_ranges = []
    for i in range(1, len(df)):
        high = df.loc[i, "High"]
        low = df.loc[i, "Low"]
        prev_close = df.loc[i, "prev_close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None, None

    atr_absolute = sum(true_ranges[-period:]) / period
    current_close = df["Close"].iloc[-1]

    if current_close == 0:
        return atr_absolute, None

    atr_pct = round((atr_absolute / current_close) * 100, 3)

    return atr_absolute, atr_pct


def estimate_candles_to_target(atr_pct, target_pct):
    """
    Rough estimate — kitni candles (15-min each) lagengi target % tak
    pahunchne mein, coin ke apne typical ATR% ke hisaab se.

    Ye ek SIMPLE estimate hai (target_pct / atr_pct) — asal mein price
    seedhi line mein nahi chalti, lekin ye ek useful "speed reference"
    deta hai compare karne ke liye.

    Return: (estimated_candles: float, estimated_minutes: float) ya
    (None, None) agar calculate nahi ho sakta
    """
    if atr_pct is None or atr_pct <= 0:
        return None, None

    estimated_candles = round(target_pct / atr_pct, 1)
    estimated_minutes = round(estimated_candles * 15)

    return estimated_candles, estimated_minutes
