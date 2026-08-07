from strategy.price_action import PriceAction
from strategy.velocity import Velocity
from strategy.momentum_engine import MomentumEngine


class MomentumScore:

    def calculate(self, df):

        last = df.iloc[-1]

        # -------------------------
        # EMA
        # -------------------------
        if (
                last["EMA9"] >
                last["EMA20"] >
                last["EMA50"]
        ):
            ema_signal = "Bullish"
        else:
            ema_signal = "Bearish"

        # -------------------------
        # MACD
        # -------------------------
        if last["MACD"] > last["Signal"]:
            macd_signal = "Bullish"
        else:
            macd_signal = "Bearish"

        # -------------------------
        # Basic Values
        # -------------------------
        rsi = float(last["RSI"])

        volume_ratio = float(last["VolumeRatio"])

        atr_percent = float(last["ATR_Percent"])

        # -------------------------
        # Strategy Modules
        # -------------------------
        pa = PriceAction.analyze(df)

        vel = Velocity.analyze(df)

        # -------------------------
        # Final Engine
        # -------------------------
        score, signal = MomentumEngine.calculate(

            ema=ema_signal,

            macd=macd_signal,

            rsi=rsi,

            volume_ratio=volume_ratio,

            atr_percent=atr_percent,

            structure_score=pa["StructureScore"],

            velocity_score=vel["VelocityScore"]

        )

        return {

            "EMA": ema_signal,

            "MACD": macd_signal,

            "RSI": round(rsi, 2),

            "VolumeRatio": round(volume_ratio, 2),

            "ATR%": round(atr_percent, 4),

            "StructureScore": pa["StructureScore"],

            "VelocityScore": vel["VelocityScore"],

            "Score": score,

            "Signal": signal

        }


def calculate_momentum(df):
    return MomentumScore().calculate(df)