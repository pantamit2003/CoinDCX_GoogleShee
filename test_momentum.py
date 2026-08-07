from data.candles import get_candles

from indicators.ema import add_ema
from indicators.rsi import add_rsi
from indicators.macd import add_macd
from indicators.volume import add_volume
from indicators.atr import add_atr

from scanner.market_scanner import calculate_score


df = get_candles("B-BTC_USDT")

df = add_ema(df)
df = add_rsi(df)
df = add_macd(df)
df = add_volume(df)
df = add_atr(df)

result = calculate_score(df)

print(result)