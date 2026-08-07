from exchange.coindcx import CoinDCX
from scanner.multi_coin_backtest import run_batch_backtest, summarize_by_stage, summarize_overall

exchange = CoinDCX()
all_pairs = exchange.get_active_pairs()

# 15 coins ke liye backtest — zyada karoge to zyada time lagega
# (har coin ke liye multiple API calls hoti hain)
test_pairs = [p for p in all_pairs if p != "B-BTC_USDT"][:15]

print(f"Running multi-coin backtest on {len(test_pairs)} pairs (15m, 60 days)...\n")

trades_df = run_batch_backtest(test_pairs, resolution="15", days=60)

if trades_df.empty:
    print("\nNo trades found.")
else:
    print("\n===== OVERALL RESULTS (all coins combined) =====\n")
    overall = summarize_overall(trades_df)
    for k, v in overall.items():
        print(f"{k}: {v}")

    print("\n===== BREAKDOWN BY ENTRY STAGE =====\n")
    print(summarize_by_stage(trades_df))