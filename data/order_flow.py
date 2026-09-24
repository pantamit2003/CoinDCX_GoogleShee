"""
data/order_flow.py
====================
STANDALONE MODULE — CoinDCX futures trade-history se ek chhota
"snapshot" leta hai aur Aggressive Buy/Sell volume + Delta calculate
karta hai. Koi existing file (candles.py, sr_shape_outcome_tracker.py
ka core V5/S/R/regime/outcome logic) touch nahi karta.

IMPORTANT LIMITATION (transparently documented, hide nahi kiya):
CoinDCX ka public futures trade-history endpoint (coindcx pip package
ke through, Client.get_futures_trade_history(pair)) SIRF last ~30
trades deta hai — jo sirf ~4-5 SECONDS ka real data hota hai, poori
15-min candle ka NAHI. Koi time-range/from/to parameter available
nahi hai jisse poori candle ka data maanga ja sake.

Order Flow data represents ONLY the recent CoinDCX trade-history
snapshot captured at breakout confirmation. It is NOT the complete
15-minute candle's trade flow or Delta. Yeh limitation
Order_Flow_Sample_Size aur Order_Flow_Span_Seconds columns ke through
explicitly record hoti hai, taaki koi bhi analysis karte waqt yeh
saaf dikhe.

AGGRESSOR-SIDE CLASSIFICATION (tick rule — inferred, exchange-provided
NAHI):
CoinDCX trade data mein direct "buy"/"sell" field nahi hota, sirf
`is_maker` (bool) milta hai. Standard market-microstructure "tick
rule" use kiya hai (jab exchange direct side na de, tab yeh universal
accepted inference method hai — koi fake/random approximation nahi):
    - agar taker trade ka price pichle trade se UPAR hai -> BUY-aggressive
    - agar NEECHE hai -> SELL-aggressive
    - agar SAME hai -> pichli direction continue maani jaati hai
Sirf is_maker=False (yani taker/aggressive) trades Delta mein count
hote hain — maker trades (is_maker=True) delta mein include nahi
hote, kyunki unka initiator passive tha, aggressive nahi.
BUY/SELL side yahan TICK-RULE SE INFERRED hai, CoinDCX se direct
nahi milta — isko kabhi "exact exchange-provided Delta" na bola
jaaye, hamesha "tick-rule-inferred Delta" bola jaaye.

DEPENDENCY: `coindcx` pip package chahiye (requirements.txt mein
add karna hoga: coindcx).
"""
from coindcx import Client

_client = Client()


def _blank_result(sample_size=0, error=""):
    return {
        "Aggressive_Buy_Volume": None,
        "Aggressive_Sell_Volume": None,
        "Delta": None,
        "Delta_Pct": None,
        "Order_Flow_Sample_Size": sample_size,
        "Order_Flow_Span_Seconds": None,
        "Aggressive_Trade_Count": 0,
        "Order_Flow_Error": error,
    }


def get_order_flow_snapshot(pair):
    """
    Ek real (chhota, non-fake) trade snapshot leta hai aur Aggressive
    Buy/Sell volume + Delta calculate karta hai. Yeh function BREAKOUT
    CONFIRMATION ke waqt call hota hai (sirf LONG/SHORT confirmed
    setups ke liye) — NO_CONFIRMATION setups ke liye kabhi call nahi
    hota, taaki unnecessary API calls na hon.

    Return: dict:
        {
            "Aggressive_Buy_Volume": float or None,
            "Aggressive_Sell_Volume": float or None,
            "Delta": float or None,
            "Delta_Pct": float or None,   # Delta / total aggressive volume * 100
            "Order_Flow_Sample_Size": int,       # kitne trades total mile
            "Order_Flow_Span_Seconds": float or None,  # sample kitne second ka tha
            "Aggressive_Trade_Count": int,       # in sample mein kitne trades is_maker=False the
            "Order_Flow_Error": str,             # blank agar sab theek, warna reason
        }

    Kisi bhi failure (network, missing package, empty/malformed
    response, insufficient trades) par yeh function EXCEPTION NAHI
    raise karta — blank/None values return karta hai, reason
    Order_Flow_Error mein, taaki caller (resolve_confirmations) ka
    existing V5 flow kabhi break na ho.
    """
    try:
        trades = _client.get_futures_trade_history(pair)
    except Exception as e:
        return _blank_result(sample_size=0, error=f"fetch_error: {e}")

    if not trades:
        return _blank_result(sample_size=0, error="empty_response")

    sample_size = len(trades)

    if sample_size < 2:
        return _blank_result(sample_size=sample_size, error="insufficient_trades")

    # Malformed-trade safety: sirf wo trades rakho jinme price/quantity/
    # timestamp mile — malformed entries ko silently skip karo, crash
    # mat karo.
    clean_trades = []
    for t in trades:
        try:
            clean_trades.append({
                "price": float(t["price"]),
                "quantity": float(t["quantity"]),
                "timestamp": float(t["timestamp"]),
                "is_maker": t.get("is_maker", None),
            })
        except (KeyError, TypeError, ValueError):
            continue  # malformed trade, skip

    if len(clean_trades) < 2:
        return _blank_result(sample_size=sample_size, error="malformed_trades")

    # CoinDCX se newest-first aate hain (test mein dekha) — chronological
    # (oldest-first) order mein sort karo taaki tick rule sahi se chale.
    sorted_trades = sorted(clean_trades, key=lambda t: t["timestamp"])

    # Aggressive_Trade_Count: kitne trades is_maker == False the,
    # independent of direction-inference — yeh Order_Flow_Sample_Size
    # (saare trades) se ALAG hai (sirf taker trades).
    aggressive_trade_count = sum(1 for t in sorted_trades if t["is_maker"] is False)

    aggressive_buy_volume = 0.0
    aggressive_sell_volume = 0.0
    prev_price = None
    last_direction = None

    for trade in sorted_trades:
        price = trade["price"]
        quantity = trade["quantity"]
        is_maker = trade["is_maker"]

        if prev_price is None:
            prev_price = price
            continue  # pehla trade — direction infer nahi ho sakti, skip

        if price > prev_price:
            direction = "BUY"
        elif price < prev_price:
            direction = "SELL"
        else:
            direction = last_direction  # price same -> pichli direction continue

        prev_price = price
        if direction is None:
            continue  # abhi tak koi direction establish nahi hui
        last_direction = direction

        if is_maker is False:
            if direction == "BUY":
                aggressive_buy_volume += quantity
            elif direction == "SELL":
                aggressive_sell_volume += quantity

    delta = aggressive_buy_volume - aggressive_sell_volume
    total_aggressive = aggressive_buy_volume + aggressive_sell_volume
    delta_pct = round((delta / total_aggressive) * 100, 3) if total_aggressive > 0 else None

    try:
        newest_ts = sorted_trades[-1]["timestamp"]
        oldest_ts = sorted_trades[0]["timestamp"]
        span_seconds = round((newest_ts - oldest_ts) / 1000.0, 3)
    except Exception:
        span_seconds = None

    return {
        "Aggressive_Buy_Volume": round(aggressive_buy_volume, 8),
        "Aggressive_Sell_Volume": round(aggressive_sell_volume, 8),
        "Delta": round(delta, 8),
        "Delta_Pct": delta_pct,
        "Order_Flow_Sample_Size": sample_size,
        "Order_Flow_Span_Seconds": span_seconds,
        "Aggressive_Trade_Count": aggressive_trade_count,
        "Order_Flow_Error": "",
    }
