import datetime

import pandas as pd

from exchange.coindcx import CoinDCX
from scanner.coiled_spring_scan import scan_coiled_spring
from scanner.sheets_writer import write_leaderboard
from notifications.telegram_bot import send_telegram_message

SHEET_TAB_NAME = "Coiled_Spring_Watchlist"

# 250 = testing/manual run ke liye fast rakha hai. Jab Windows Task
# Scheduler se automation set ho jayegi (raat ko khud chalega), tab
# ise None kar dena taaki SAARE active pairs scan hon (time ki chinta
# nahi rahegi kyunki tum so rahe hoge, script khud chalegi).
MAX_PAIRS_TO_SCAN = 300

# Telegram message mein zyada se zyada kitne coins ki list dikhani hai
# (poori list hamesha Google Sheet mein hoti hi hai)
MAX_COINS_IN_TELEGRAM = 20


def main():
    exchange = CoinDCX()
    all_pairs = exchange.get_active_pairs()
    pairs_to_scan = [p for p in all_pairs if p != "B-BTC_USDT"]
    if MAX_PAIRS_TO_SCAN is not None:
        pairs_to_scan = pairs_to_scan[:MAX_PAIRS_TO_SCAN]

    print(f"Scanning {len(pairs_to_scan)} pairs on Daily timeframe for coiled-spring setups...\n")
    watchlist = scan_coiled_spring(pairs_to_scan)

    if watchlist.empty:
        print("Abhi koi coin coiled-spring pattern mein nahi hai.")
    else:
        print(f"\n{len(watchlist)} coin(s) coiled-spring watchlist mein mile:\n")
        print(watchlist)

    # ---- Google Sheet mein bhejo ----
    push_to_sheet(watchlist)

    # ---- Telegram pe bhejo (sirf agar coins mile) ----
    push_to_telegram(watchlist)


def push_to_sheet(watchlist: pd.DataFrame):
    """
    watchlist ko Google Sheet ke "Coiled_Spring_Watchlist" tab mein likhta hai.
    Agar koi coin nahi mila, tab bhi ek "no result" row likhta hai (timestamp
    ke saath) taaki Sheet check karne pe pata chale ki scan chala tha, sirf
    koi match nahi mila.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if watchlist.empty:
        placeholder = pd.DataFrame([{
            "Pair": "-",
            "Close": "-",
            "Trend_Stage": "-",
            "Basing_Duration": "-",
            "Range_Contraction": "-",
            "ATR_Contraction_Ratio": "-",
            "Coiled_Spring": "-",
            "RS_Score": "-",
            "Reason_Codes": "Koi coiled-spring coin nahi mila",
            "Last_Updated": timestamp,
        }])
        write_leaderboard(placeholder, worksheet_name=SHEET_TAB_NAME)
    else:
        to_write = watchlist.copy()
        to_write["Last_Updated"] = timestamp
        write_leaderboard(to_write, worksheet_name=SHEET_TAB_NAME)

    print(f"\nGoogle Sheet ke '{SHEET_TAB_NAME}' tab mein bhi likh diya.")


def push_to_telegram(watchlist: pd.DataFrame):
    """
    Sirf tab message bhejta hai jab koi coin mila ho — khali result pe
    chup rehta hai, taaki phone pe roz spam na aaye.
    """
    if watchlist.empty:
        print("Telegram: koi coin nahi mila, isliye message skip.")
        return

    today = datetime.date.today().strftime("%d-%b-%Y")
    lines = [f"🔍 <b>Coiled Spring Watchlist</b> — {today}", f"{len(watchlist)} coin(s) mile:\n"]

    # RS_UNDERPERFORMING wale coins ko ⚠️ se mark karo (risky, avoid karo)
    for _, row in watchlist.head(MAX_COINS_IN_TELEGRAM).iterrows():
        warn = "⚠️ " if "RS_UNDERPERFORMING" in str(row.get("Reason_Codes", "")) else ""
        lines.append(
            f"{warn}{row['Pair']} — {row['Trend_Stage']} ({int(row['Basing_Duration'])} din)"
        )

    if len(watchlist) > MAX_COINS_IN_TELEGRAM:
        lines.append(f"\n...+{len(watchlist) - MAX_COINS_IN_TELEGRAM} aur, poori list Sheet mein hai.")

    lines.append("\n⚠️ = weak coin (BTC ke against underperform), inse bachna behtar.")

    send_telegram_message("\n".join(lines))
    print("Telegram pe bhi message bhej diya.")


if __name__ == "__main__":
    main()
