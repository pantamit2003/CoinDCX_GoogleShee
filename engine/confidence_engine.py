class ConfidenceEngine:

    @staticmethod
    def analyze(

            momentum,

            rs,

            lifecycle,

            mtf

    ):

        confidence = 0

        reasons = []

        # ---------------------------------
        # Momentum Score
        # ---------------------------------

        if momentum["Score"] >= 85:

            confidence += 25

            reasons.append("High Momentum")

        elif momentum["Score"] >= 70:

            confidence += 18

            reasons.append("Good Momentum")

        elif momentum["Score"] >= 55:

            confidence += 10

        # ---------------------------------
        # Relative Strength
        # ---------------------------------

        if rs["RS_Score"] >= 80:

            confidence += 25

            reasons.append("Strong Relative Strength")

        elif rs["RS_Score"] >= 60:

            confidence += 18

            reasons.append("Above BTC Strength")

        elif rs["RS_Score"] >= 40:

            confidence += 10

        # ---------------------------------
        # Trend LifeCycle
        # ---------------------------------

        stage = lifecycle["Stage"]

        if stage == "FRESH_BREAKOUT":

            confidence += 25

            reasons.append("Fresh Breakout")

        elif stage == "EARLY_TREND":

            confidence += 20

            reasons.append("Early Trend")

        elif stage == "MID_TREND":

            confidence += 12

        elif stage == "LATE_TREND":

            confidence += 5

        elif stage == "EXHAUSTION":

            confidence -= 10

            reasons.append("Possible Exhaustion")

        # ---------------------------------
        # Multi Timeframe
        # ---------------------------------

        alignment = mtf["Alignment"]

        if alignment == 100:

            confidence += 25

            reasons.append("All Timeframes Agree")

        elif alignment >= 67:

            confidence += 18

            reasons.append("Most Timeframes Agree")

        elif alignment >= 34:

            confidence += 10

        # ---------------------------------
        # Clamp
        # ---------------------------------

        confidence = max(0, min(100, confidence))

        return {

            "Confidence": confidence,

            "Reasons": reasons

        }


# ---------------------------------
# Helper
# ---------------------------------

def calculate_confidence(

        momentum,

        rs,

        lifecycle,

        mtf

):

    return ConfidenceEngine.analyze(

        momentum,

        rs,

        lifecycle,

        mtf

    )