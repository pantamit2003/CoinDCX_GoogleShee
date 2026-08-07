from exchange.coindcx import CoinDCX
from scanner.ranking_engine import scan_market
from scanner.sheets_writer import write_leaderboard
from scanner.multi_timeframe_report import generate_timeframe_report

CONFIRM_TIMEFRAMES = ["15m", "1h", "4h"]


def build_trade_reason(pair: str, mtf: "pd.DataFrame") -> dict:
    """
    Looks at 15m/1h/4h rows for this pair and builds a human-readable
    reason for WHY this trade qualifies (or doesn't).
    """
    aligned = mtf[mtf["Timeframe"].isin(CONFIRM_TIMEFRAMES)]
    if aligned.empty or aligned["Direction"].isnull().any():
        return {"confirmed": False, "reason": "Missing data on one or more confirm timeframes."}

    directions = aligned["Direction"].unique()
    if len(directions) != 1:
        detail = ", ".join(f"{row.Timeframe}={row.Direction}" for row in aligned.itertuples())
        return {"confirmed": False, "reason": f"Timeframes disagree on direction ({detail})."}

    direction = directions[0]
    stages = dict(zip(aligned["Timeframe"], aligned["Trend_Stage"]))
    reason_codes = dict(zip(aligned["Timeframe"], aligned["Reason_Codes"]))
    avg_confidence = aligned["Confidence"].mean()

    reason_lines = [
        f"Direction aligned '{direction}' across {', '.join(CONFIRM_TIMEFRAMES)}.",
        "Trend stages: " + ", ".join(f"{tf}={stages[tf]}" for tf in CONFIRM_TIMEFRAMES),
        f"Avg confidence across confirm TFs: {avg_confidence:.2f}",
    ]
    for tf in CONFIRM_TIMEFRAMES:
        if reason_codes.get(tf):
            reason_lines.append(f"[{tf}] {reason_codes[tf]}")

    return {
        "confirmed": True,
        "direction": direction,
        "avg_confidence": avg_confidence,
        "reason": " | ".join(reason_lines),
    }


def main():

    print("=" * 60)
    print("CoinDCX Institutional Momentum Scanner")
    print("=" * 60)

    exchange = CoinDCX()
    all_pairs = exchange.get_active_pairs()
    pairs_to_scan = [p for p in all_pairs if p != "B-BTC_USDT"][:100]

    # ---------------------------------------------------------------
    # STAGE 1: BIG TIMEFRAME (1h) -> sirf TREND DIRECTION dekhne ke liye
    # ---------------------------------------------------------------
    print(f"\nStage 1: {len(pairs_to_scan)} pairs ko 1h par trend ke liye scan kar rahe hain...\n")
    trend_leaderboard = scan_market(pairs_to_scan, resolution="60", days=30)

    if trend_leaderboard.empty:
        print("\nNo Data Found on 1h scan.")
        return

    # Sirf woh coins nikalo jinka 1h par clear trend direction hai
    trending_pairs = trend_leaderboard[
        trend_leaderboard["Direction"].isin(["LONG", "SHORT"])
    ]["Pair"].tolist()

    print(f"1h par {len(trending_pairs)} coins trending mile: {trending_pairs}")

    if not trending_pairs:
        print("\nKoi bhi coin 1h par trending nahi hai. Exit.")
        return

    # ---------------------------------------------------------------
    # STAGE 2: SMALL TIMEFRAME (15m) -> ENTRY TIMING ke liye
    # ---------------------------------------------------------------
    print(f"\nStage 2: Trending coins ko 15m par entry ke liye scan kar rahe hain...\n")
    entry_leaderboard = scan_market(trending_pairs, resolution="15", days=10)

    if entry_leaderboard.empty:
        print("\nNo Data Found on 15m scan.")
        return

    print("Top Opportunities (15m entry scan)\n")
    print(
        entry_leaderboard[
            ["Pair", "Close", "Trend_Stage", "Direction", "Confidence", "Entry_Trigger"]
        ].head(10)
    )

    # ---------------------------------------------------------------
    # STAGE 3: CONFIRM top candidates across 15m/1h/4h before trusting them
    # ---------------------------------------------------------------
    ready_now = entry_leaderboard[entry_leaderboard["Entry_Trigger"] == "NOW"]
    top_candidates = ready_now.head(5)["Pair"].tolist()

    if not top_candidates:
        print("\nAbhi koi bhi coin 'NOW' entry ke layak nahi hai.")
        write_leaderboard(entry_leaderboard)
        print("\nDone.")
        return

    print(f"\nStage 3: Top {len(top_candidates)} candidates ko 15m/1h/4h par confirm kar rahe hain...\n")

    confirmed_trades = []
    for pair in top_candidates:
        try:
            mtf = generate_timeframe_report(pair)
        except Exception as e:
            print(f"{pair}: could not generate multi-timeframe report ({e})")
            continue

        verdict = build_trade_reason(pair, mtf)

        if verdict["confirmed"]:
            print(f"\n✅ {pair} — CONFIRMED ({verdict['direction']}, conf={verdict['avg_confidence']:.2f})")
            print(f"   Reason: {verdict['reason']}")
            confirmed_trades.append(pair)
        else:
            print(f"\n❌ {pair} — SKIP")
            print(f"   Reason: {verdict['reason']}")

    print(f"\n{len(confirmed_trades)}/{len(top_candidates)} candidates confirmed.")

    write_leaderboard(entry_leaderboard)

    print("\nDone.")


if __name__ == "__main__":
    main()