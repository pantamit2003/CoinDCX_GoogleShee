from data.candles import get_candles

from strategy.velocity import Velocity

df = get_candles("B-BTC_USDT")

result = Velocity.analyze(df)

print(result)