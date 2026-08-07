from exchange.coindcx import CoinDCX


exchange = CoinDCX()

pairs = exchange.get_active_pairs()

print("Total Pairs:", len(pairs))
print("First 10:")

for pair in pairs[:10]:
    print(pair)