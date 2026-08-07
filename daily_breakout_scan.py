"""
daily_breakout_scan.py

Do tarah ke "coin abhi uth raha hai, log kharidna shuru kar rahe hain"
signals dhundhta hai — dono mein momentum ke saath entry lekar profit
lene ka goal hai, bas trigger karne ka tarika alag hai:

  TYPE 1 - EXPLOSIVE BREAKOUT (jaisa CTSI/HFT)
    Mahino ki boring/tight range ke baad EK DIN mein bade volume ke
    saath sudden upward move. Sharp, sudden, high RVOL.

  TYPE 2 - BOTTOMING REVERSAL (jaisa ACE/GWEI/XAI/STG/SKYAI/BSB)
    Bada dump hone ke baad, base banake, DHEERE-DHEERE upar palatna
    shuru karna. Gradual, moderate RVOL, koi ek bada dhamaka nahi —
    lekin trend structure clearly upar palat chuka hai.

Dono ko alag rakha hai kyunki dono ka risk profile alag hai:
Type 1 sharp but proven momentum hai, Type 2 early lekin abhi confirm
ho raha hai ("dead cat bounce" ka risk zyada hai).

Isse alag ek aur cheez milti hai:
  check_watchlist.py -> "abhi coiled hai, breakout se PEHLE" (sirf
  Type 1 pattern ke liye — coiled_spring sirf tight-range coins pakadta
  hai, dump-and-base wale coins nahi)
"""

import datetime

import pandas as pd

from exchange.coindcx import CoinDCX
from scanner.ranking_engine import RankingEngine
from scanner.sheets_writer import write_leaderboard
from notifications.telegram_bot import send_telegram_message

# ---------------------------------------------------------------------
# 250 = testing/manual run ke liye fast rakha hai. Jab Windows Task
# Scheduler se automation set ho jayegi (raat ko khud chalega), tab
# ise None kar dena taaki SAARE active pairs scan hon.
# ---------------------------------------------------------------------
MAX_PAIRS_TO_SCAN = 250

# TYPE 1 — explosive breakout: sudden, high volume
EXPLOSIVE_STAGES = {
    "EARLY_BREAKOUT_UP",
    "FRESH_MOMENTUM_UP",
}
EXPLOSIVE_RVOL_THRESHOLD = 3.0

# TYPE 2 — bottoming reversal: gradual, lower volume bar hai kyunki ye
# multi-day process hai, ek din ka spike nahi
REVERSAL_STAGES = {
    "REVERSAL_UP",
}
REVERSAL_RVOL_THRESHOLD = 1.3   # sirf itna chahiye ki volume "mar" na raha ho

MIN_RISK_REWARD = 1.5           # dono types ke liye — bina achhe RR ke entry mat do

SHEET_TAB_NAME = "Daily_Breakout_Signals"


def scan_daily_breakouts(pairs=None) -> pd.DataFrame:
    if pairs is None:
        exchange = CoinDCX()
        all_pairs = exchange.get_active_pairs()
        pairs = [p for p in all_pairs if p != "B-BTC_USDT"]
        if MAX_PAIRS_TO_SCAN is not None:
            pairs = pairs[:MAX_PAIRS_TO_SCAN]

    # days=200 taaki RVOL_20 / ATR baseline / basing lookback jaise rolling
    # windows ke liye kaafi daily candles maujood hon
    engine = RankingEngine(resolution="1D", days=200)
    leaderboard = engine.scan(pairs)

    return leaderboard  # poora unfiltered leaderboard


def _finalize(df: pd.DataFrame, signal_type: str) -> pd.DataFrame:
    df = df.copy()
    df["Signal_Type"] = signal_type
    df = df.sort_values(by=["Breakout_Score", "Confidence"], ascending=[False, False])
    return df.reset_index(drop=True)


def filter_explosive(leaderboard: pd.DataFrame) -> pd.DataFrame:
    if leaderboard.empty:
        return leaderboard
    matched = leaderboard[
        leaderboard["Trend_Stage"].isin(EXPLOSIVE_STAGES)
        & (leaderboard["RVOL_20"] >= EXPLOSIVE_RVOL_THRESHOLD)
        & (~leaderboard["Made_New_Low_Recently"])
        & (leaderboard["Risk_Reward"].fillna(0) >= MIN_RISK_REWARD)
    ]
    return _finalize(matched, "EXPLOSIVE_BREAKOUT")


def filter_reversal(leaderboard: pd.DataFrame) -> pd.DataFrame:
    if leaderboard.empty:
        return leaderboard
    matched = leaderboard[
        leaderboard["Trend_Stage"].isin(REVERSAL_STAGES)
        & (leaderboard["RVOL_20"] >= REVERSAL_RVOL_THRESHOLD)
        & (~leaderboard["Made_New_Low_Recently"])
        & (leaderboard["Risk_Reward"].fillna(0) >= MIN_RISK_REWARD)
    ]
    return _finalize(matched, "BOTTOMING_REVERSAL")


DISPLAY_COLUMNS = [
    "Signal_Type", "Pair", "Close", "Trend_Stage", "RVOL_20",
    "Bars_Since_90d_Low", "Confidence", "Entry_Trigger",
    "Entry_Price", "Stop_Loss_Price", "Take_Profit_Price",
    "Risk_Reward", "Reason_Codes",
]


def main():
    label = "all active" if MAX_PAIRS_TO_SCAN is None else f"up to {MAX_PAIRS_TO_SCAN}"
    print(f"Scanning {label} pairs on Daily timeframe...\n")
    leaderboard = scan_daily_breakouts()

    if leaderboard.empty:
        print("Koi data hi nahi mila — sab pairs fail ho gaye, upar ke errors check karo.")
        return

    explosive = filter_explosive(leaderboard)
    reversal = filter_reversal(leaderboard)
    combined = pd.concat([explosive, reversal], ignore_index=True)

    if combined.empty:
        print("Abhi koi coin explosive breakout ya bottoming reversal criteria pe match nahi kar raha.\n")
        print("Diagnostic: aaj ke top 10 coins RVOL_20 se sort karke:\n")
        closest = leaderboard.sort_values(by="RVOL_20", ascending=False).head(10)
        print(closest[["Pair", "Close", "Trend_Stage", "RVOL_20", "Made_New_Low_Recently", "Risk_Reward", "Confidence"]])
    else:
        print(f"{len(combined)} coin(s) mile:\n")
        print(combined[DISPLAY_COLUMNS])

    # ---- Google Sheet mein bhejo ----
    push_to_sheet(combined)

    # ---- Telegram pe bhejo (sirf agar signal mile) ----
    push_to_telegram(combined)


def push_to_sheet(combined: pd.DataFrame):
    """
    combined (explosive + reversal signals) ko Google Sheet ke
    "Daily_Breakout_Signals" tab mein likhta hai. Agar koi signal nahi
    mila, tab bhi ek "no result" row likhta hai (timestamp ke saath)
    taaki Sheet check karne pe pata chale ki scan chala tha.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if combined.empty:
        placeholder = pd.DataFrame([{col: "-" for col in DISPLAY_COLUMNS}])
        placeholder["Reason_Codes"] = "Koi breakout/reversal signal nahi mila"
        placeholder["Last_Updated"] = timestamp
        write_leaderboard(placeholder, worksheet_name=SHEET_TAB_NAME)
    else:
        to_write = combined[DISPLAY_COLUMNS].copy()
        to_write["Last_Updated"] = timestamp
        write_leaderboard(to_write, worksheet_name=SHEET_TAB_NAME)

    print(f"\nGoogle Sheet ke '{SHEET_TAB_NAME}' tab mein bhi likh diya.")


def push_to_telegram(combined: pd.DataFrame):
    """
    Har signal (EXPLOSIVE_BREAKOUT ya BOTTOMING_REVERSAL) ke liye ek
    detailed trade-ready message bhejta hai — Entry/SL/TP/RR sab
    saath mein, taaki phone pe dekh ke seedha decide kar sako.
    Sirf tab bhejta hai jab koi signal ho — khali result pe chup.
    """
    if combined.empty:
        print("Telegram: koi signal nahi mila, isliye message skip.")
        return

    today = datetime.date.today().strftime("%d-%b-%Y")
    emoji_map = {"EXPLOSIVE_BREAKOUT": "🚀", "BOTTOMING_REVERSAL": "🔄"}

    lines = [f"📢 <b>Daily Breakout Signals</b> — {today}", f"{len(combined)} coin(s) mile:\n"]

    for _, row in combined.iterrows():
        emoji = emoji_map.get(row["Signal_Type"], "•")
        lines.append(
            f"{emoji} <b>{row['Pair']}</b> ({row['Signal_Type']})\n"
            f"   Close: {row['Close']}  |  RVOL: {round(row['RVOL_20'], 2)}\n"
            f"   Entry: {row['Entry_Price']}  SL: {row['Stop_Loss_Price']}  TP: {row['Take_Profit_Price']}\n"
            f"   RR: {round(row['Risk_Reward'], 2)}  |  Confidence: {round(row['Confidence'], 1)}\n"
        )

    send_telegram_message("\n".join(lines))
    print("Telegram pe bhi message bhej diya.")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------
# Debug helper: ek single pair ka poora detail dekhne ke liye
# ---------------------------------------------------------------------
def inspect_pair(pair: str):
    lb = scan_daily_breakouts(pairs=[pair])
    if lb.empty:
        print(f"{pair}: data nahi mila")
        return
    row = lb.iloc[0]
    for col in lb.columns:
        print(f"{col}: {row[col]}")