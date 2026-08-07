from exchange.coindcx import CoinDCX
from scanner.ranking_engine import scan_market

exchange = CoinDCX()
all_pairs = exchange.get_active_pairs()

# Top 20 non-BTC pairs for testing (change this limit once you're ready
# to scan the full market)
test_pairs = [p for p in all_pairs if p != "B-BTC_USDT"][:20]

print(f"Scanning {len(test_pairs)} pairs...\n")

leaderboard = scan_market(test_pairs, resolution="1", days=2)

print("\n===== LEADERBOARD (Top by Confidence) =====\n")
print(
    leaderboard[
        [
            "Pair",
            "Close",
            "Trend_Stage",
            "Direction",
            "Confidence",
            "Entry_Trigger",
            "Risk_Reward"
        ]
    ].head(20)
)

print("\n===== Full detail for top 5 =====\n")
for i in range(min(5, len(leaderboard))):
    row = leaderboard.iloc[i]
    print(f"\n{row['Pair']}")
    print(f"  Trend: {row['Trend_Stage']}  Direction: {row['Direction']}")
    print(f"  Confidence: {row['Confidence']:.1f}%")
    print(f"  Entry: {row['Entry_Trigger']} @ {row['Entry_Price']}")
    print(f"  SL: {row['Stop_Loss_Price']}  TP: {row['Take_Profit_Price']}")
    print(f"  Risk:Reward = {row['Risk_Reward']}")
    print(f"  Reason: {row['Reason_Codes']}")