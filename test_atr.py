from data.candles import get_candles
from indicators.atr import add_atr

df = get_candles("B-BTC_USDT")

df = add_atr(df)

print(
    df[
        [
            "Time",
            "Close",
            "ATR",
            "ATR_Percent"
        ]
    ].tail(20)
)