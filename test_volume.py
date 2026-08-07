from data.candles import CandleData
from indicators.volume import add_volume

# Object banao
candle = CandleData()

# Candles fetch karo
df = candle.get_candles("B-BTC_USDT")

# Volume indicator add karo
df = add_volume(df)

print(df[
    [
        "Time",
        "Volume",
        "AvgVolume",
        "VolumeRatio",
        "VolumeSpike"
    ]
].tail(20))