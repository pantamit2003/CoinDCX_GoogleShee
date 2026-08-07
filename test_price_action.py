from data.candles import get_candles

from strategy.price_action import PriceAction

df = get_candles("B-BTC_USDT")

result = PriceAction.analyze(df)

print(result)