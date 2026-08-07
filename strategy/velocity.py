import pandas as pd


class Velocity:

    @staticmethod
    def analyze(df: pd.DataFrame):

        close = df["Close"]

        last = close.iloc[-1]

        # -----------------------------
        # Returns (%)
        # -----------------------------

        return_1 = ((last - close.iloc[-2]) / close.iloc[-2]) * 100

        return_3 = ((last - close.iloc[-4]) / close.iloc[-4]) * 100

        return_5 = ((last - close.iloc[-6]) / close.iloc[-6]) * 100

        # -----------------------------
        # Acceleration
        # -----------------------------

        acceleration = return_1 - (return_3 / 3)

        # -----------------------------
        # Velocity Score
        # -----------------------------

        score = 0

        if return_1 > 0:
            score += 20

        if return_3 > 0:
            score += 30

        if return_5 > 0:
            score += 30

        if acceleration > 0:
            score += 20

        return {

            "Return1": round(return_1, 3),

            "Return3": round(return_3, 3),

            "Return5": round(return_5, 3),

            "Acceleration": round(acceleration, 3),

            "VelocityScore": score

        }