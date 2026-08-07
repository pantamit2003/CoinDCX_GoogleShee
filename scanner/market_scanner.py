import pandas as pd
from strategy.multi_timeframe import MultiTimeFrame
from exchange.coindcx import CoinDCX
from strategy.relative_strength import RelativeStrength
from strategy.trend_lifecycle import TrendLifeCycle
from engine.confidence_engine import calculate_confidence

from data.candles import get_candles

from indicators.ema import add_ema
from indicators.rsi import add_rsi
from indicators.macd import add_macd
from indicators.volume import add_volume
from indicators.atr import add_atr

from indicators.momentum_score import calculate_momentum

from sheets.sheet import upload_dataframe


class MarketScanner:

    def __init__(self):
        self.exchange = CoinDCX()

    # ----------------------------------
    # Get All Active Futures Pairs
    # ----------------------------------
    def get_pairs(self):

        return self.exchange.get_active_pairs()

    def quick_scan(self, pair):

        try:

            df = get_candles(pair)

            if df.empty:
                return None

            # Fast Indicators Only
            df = add_ema(df)
            df = add_rsi(df)
            df = add_macd(df)

            last = df.iloc[-1]

            score = 0

            # EMA
            if (
                    last["EMA9"] >
                    last["EMA20"] >
                    last["EMA50"]
            ):
                score += 40

            # MACD
            if last["MACD"] > last["Signal"]:
                score += 40

            # RSI
            if 55 <= last["RSI"] <= 70:
                score += 20

            elif 50 <= last["RSI"] < 55:
                score += 10

            return {
                "Pair": pair,
                "QuickScore": score,
                "Price": round(last["Close"], 6)
            }

        except Exception as e:

            print(f"{pair} -> {e}")

            return None
    # ----------------------------------
    # Scan Single Coin
    # ----------------------------------
    def momentum_scan(self, pair):

        try:

            df = get_candles(pair)

            if df.empty:
                return None

            # Indicators
            df = add_ema(df)
            df = add_rsi(df)
            df = add_macd(df)
            df = add_volume(df)
            df = add_atr(df)

            # Momentum Score
            result = calculate_momentum(df)
            # Relative Strength
            rs = RelativeStrength.analyze(pair)

            # Trend Life Cycle
            lifecycle = TrendLifeCycle.analyze(df)

            # Multi TimeFrame
            mtf = self.confirmation_scan(pair)

            # Confidence
            confidence = calculate_confidence(
                result,
                rs,
                lifecycle,
                mtf
            )

            result["Pair"] = pair
            result["Price"] = round(df.iloc[-1]["Close"], 6)
            result["RelativeStrength"] = rs["RelativeStrength"]
            result["RS_Score"] = rs["RS_Score"]

            result["TrendStage"] = lifecycle["Stage"]
            result["TrendScore"] = lifecycle["TrendScore"]

            result["Alignment"] = mtf["Alignment"]

            result["1m"] = mtf["1m"]["Signal"]
            result["5m"] = mtf["5m"]["Signal"]
            result["15m"] = mtf["15m"]["Signal"]

            result["Confidence"] = confidence["Confidence"]
            result["Reasons"] = ", ".join(confidence["Reasons"])

            return result

        except Exception as e:

            print(f"{pair} -> {e}")

            return None

    def confirmation_scan(self, pair):

        return MultiTimeFrame.analyze(pair)

    # ----------------------------------
    # Scan Complete Market
    # ----------------------------------
    def scan_market(self):

        pairs = self.get_pairs()

        print(f"\nTotal Futures : {len(pairs)}\n")

        # ===================================================
        # STAGE 1
        # Quick Scan
        # ===================================================

        print("========== STAGE 1 : QUICK SCAN ==========\n")

        quick_results = []

        for i, pair in enumerate(pairs):

            print(f"[{i + 1}/{len(pairs)}] {pair}")

            result = self.quick_scan(pair)

            if result is not None:
                quick_results.append(result)

        if len(quick_results) == 0:
            print("No Quick Scan Results")

            return pd.DataFrame()

        quick_df = pd.DataFrame(quick_results)

        quick_df = quick_df.sort_values(
            by="QuickScore",
            ascending=False
        )

        TOP_50 = quick_df.head(50)

        print(f"\nSelected {len(TOP_50)} Coins For Momentum Scan\n")

        # ===================================================
        # STAGE 2
        # Momentum Scan
        # ===================================================

        print("========== STAGE 2 : MOMENTUM ==========\n")

        momentum_results = []

        for i, pair in enumerate(TOP_50["Pair"]):

            print(f"[{i + 1}/{len(TOP_50)}] {pair}")

            result = self.momentum_scan(pair)

            if result is not None:
                momentum_results.append(result)

        if len(momentum_results) == 0:
            print("No Momentum Results")

            return pd.DataFrame()

        momentum_df = pd.DataFrame(momentum_results)

        print(f"\nSelected {len(TOP_50)} Coins For Final Ranking\n")

        # ===================================================
        # STAGE 3
        # Multi TimeFrame Confirmation
        # ===================================================

        print("========== STAGE 3 : MTF ==========\n")

        # ==================================
        # FINAL RANKING
        # ==================================

        TOP_50 = momentum_df.copy()

        TOP_50["FinalScore"] = (

                TOP_50["Score"] * 0.35 +

                TOP_50["Confidence"] * 0.30 +

                TOP_50["RS_Score"] * 0.20 +

                TOP_50["Alignment"] * 0.15

        )



        TOP_50 = TOP_50.sort_values(
            by="FinalScore",
            ascending=False
        )

        TOP_15 = TOP_50.head(15).copy()



        TOP_15 = TOP_15.sort_values(

            by="FinalScore",

            ascending=False

        ).reset_index(drop=True)

        TOP_15.insert(

            0,

            "Rank",

            range(1, len(TOP_15) + 1)

        )

        upload_dataframe(TOP_15)

        print("\n✅ Google Sheet Updated Successfully\n")

        return TOP_15

# ----------------------------------
# Run Scanner
# ----------------------------------
if __name__ == "__main__":

    scanner = MarketScanner()

    df = scanner.scan_market()

    print("\nTop 20 Momentum Coins\n")

    print(df.head(20))