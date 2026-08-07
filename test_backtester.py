from scanner.backtester import backtest

# Coin aur period jo test karna hai — badal sakte ho
test_pair = "B-PLAY_USDT"

# IMPORTANT: resolution="15" kyunki tumhara actual system 15m par entry
# leta hai (Stage 2 in main.py), 1m par nahi. days=60 rakha hai taaki
# 15m par bhi ek reasonable number of bars mil sakein testing ke liye.
print(f"Backtesting {test_pair} on 15m timeframe...\n")

result = backtest(test_pair, resolution="15", days=60)

print(f"Pair: {result['Pair']}")
print(f"Total Trades: {result.get('Total_Trades', 0)}")

if result.get("Total_Trades", 0) > 0:
    print(f"Closed Trades: {result['Closed_Trades']}")
    print(f"Still Open (ran out of data): {result['Still_Open']}")
    print(f"Wins: {result['Wins']}")
    print(f"Losses: {result['Losses']}")
    print(f"Win Rate: {result['Win_Rate_Percent']}%")
    print(f"Avg Win (R): {result['Avg_Win_R']}")
    print(f"Avg Loss (R): {result['Avg_Loss_R']}")
    print(f"Total R: {result['Total_R']}")
    print(f"Expectancy per trade (R): {result['Expectancy_R_Per_Trade']}")

    print("\n--- All Trades ---\n")
    print(result["Trades"])

    print("\n--- Performance by Entry Stage ---\n")
    trades_df = result["Trades"]
    closed = trades_df[trades_df["Outcome"] != "OPEN"]
    breakdown = closed.groupby("Trend_Stage_At_Entry").agg(
        Trades=("Outcome", "count"),
        Wins=("Outcome", lambda x: (x == "WIN").sum()),
        Avg_R=("R_Multiple", "mean"),
    )
    breakdown["Win_Rate_%"] = (breakdown["Wins"] / breakdown["Trades"] * 100).round(1)
    print(breakdown)
else:
    print(result.get("Summary", "No trades."))