from exchange.coindcx import CoinDCX
from scanner.backtester_htf import run_htf_filtered_batch
from scanner.multi_coin_backtest import summarize_by_stage, summarize_overall

exchange = CoinDCX()
all_pairs = exchange.get_active_pairs()
test_pairs = [p for p in all_pairs if p != "B-BTC_USDT"][:15]

print(f"Running 1h-FILTERED backtest on {len(test_pairs)} pairs...\n")

trades_df = run_htf_filtered_batch(test_pairs, ltf_days=60, htf_days=60)

if trades_df.empty:
    print("\nNo trades passed the 1h alignment filter.")
else:
    print("\n===== OVERALL RESULTS (1h-filtered) =====\n")
    overall = summarize_overall(trades_df)
    for k, v in overall.items():
        print(f"{k}: {v}")

    print("\n===== BREAKDOWN BY ENTRY STAGE (1h-filtered) =====\n")
    print(summarize_by_stage(trades_df))