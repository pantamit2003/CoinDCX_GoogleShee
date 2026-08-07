"""
volume_watch.py

2-DIN WALA TRACKING SYSTEM — jo tumne bola wahi idea:

  DIN 1 (aaj): Jis coin ka volume aaj spike hua (RVOL >= 2.0), uska
  naam aur aaj ka High + Close ek file (volume_watchlist.json) mein
  save kar lo. Koi trade nahi, sirf note karna.

  DIN 2 (kal): Isi script ko phir chalao. Ye pehle watchlist file
  kholega, aur check karega — jo coins kal note kiye the, unka price
  AAJ kal ke High se upar nikla kya? Agar haan -> CONFIRMED, trade
  layak signal. Agar nahi -> chup-chap discard, koi trade nahi.

  Isse RIVER jaisa case automatically handle hota hai: 6 Aug ko
  volume spike -> watchlist mein aata. 7 Aug ko price upar nahi gayi
  (jaisa dekha gaya) -> confirm nahi hota -> khud-ba-khud discard.

Roz isi ek script ko chalate raho — pehle purani watchlist check
karega (confirm/discard), phir naye spikes watchlist mein add karega.
Koi extra step nahi, bas ye ek hi command roz chalao:

    python volume_watch.py
"""

import json
import os
import datetime
import pandas as pd

from exchange.coindcx import CoinDCX
from scanner.ranking_engine import RankingEngine
from scanner.sheets_writer import write_leaderboard
from notifications.telegram_bot import send_telegram_message

WATCHLIST_FILE = "volume_watchlist.json"
RVOL_ALERT_THRESHOLD = 2.0      # itna volume ho toh "note kar lo" (halka threshold)
MAX_WATCH_AGE_DAYS = 3          # itne din tak confirm na ho toh discard kar do

# 250 = testing/manual run ke liye fast rakha hai. Jab Windows Task
# Scheduler se automation set ho jayegi (raat ko khud chalega), tab
# ise None kar dena taaki SAARE active pairs scan hon.
MAX_PAIRS_TO_SCAN = 250

SHEET_TAB_NAME = "Volume_Confirmed"

CONFIRMED_COLUMNS = [
    "Pair", "Spike_Date", "Spike_High", "Today_Close", "RVOL_20_Today",
    "Trend_Stage", "Entry_Trigger", "Entry_Price", "Stop_Loss_Price",
    "Take_Profit_Price", "Risk_Reward",
]


def load_watchlist() -> dict:
    if not os.path.exists(WATCHLIST_FILE):
        return {}
    with open(WATCHLIST_FILE, "r") as f:
        return json.load(f)


def save_watchlist(watchlist: dict):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(watchlist, f, indent=2)


def scan_today(pairs=None) -> pd.DataFrame:
    if pairs is None:
        exchange = CoinDCX()
        all_pairs = exchange.get_active_pairs()
        pairs = [p for p in all_pairs if p != "B-BTC_USDT"]
        if MAX_PAIRS_TO_SCAN is not None:
            pairs = pairs[:MAX_PAIRS_TO_SCAN]

    engine = RankingEngine(resolution="1D", days=200)
    return engine.scan(pairs)


def main():
    today = datetime.date.today().isoformat()
    print(f"Volume-watch chal raha hai — {today}\n")

    leaderboard = scan_today()
    if leaderboard.empty:
        print("Koi data nahi mila, scan fail hua.")
        return

    watchlist = load_watchlist()
    confirmed_rows = []
    still_watching = {}

    # ---- STEP 1: purani watchlist ke coins check karo — confirm hue ya nahi ----
    for pair, entry in watchlist.items():
        age_days = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(entry["date"])).days

        if age_days > MAX_WATCH_AGE_DAYS:
            continue  # bahut purana ho gaya, discard — kabhi confirm nahi hua

        row_match = leaderboard[leaderboard["Pair"] == pair]
        if row_match.empty:
            still_watching[pair] = entry  # aaj data nahi mila, kal try karo
            continue

        row = row_match.iloc[0]

        # CONFIRM hone ki shart: aaj ka close, jis din volume spike hua tha
        # uske High se upar nikal jaye, aur coin abhi "still falling" na ho
        if (
            row["Close"] > entry["high"]
            and not bool(row.get("Made_New_Low_Recently", False))
        ):
            confirmed_rows.append({
                "Pair": pair,
                "Spike_Date": entry["date"],
                "Spike_High": entry["high"],
                "Today_Close": row["Close"],
                "RVOL_20_Today": row["RVOL_20"],
                "Trend_Stage": row["Trend_Stage"],
                "Entry_Trigger": row["Entry_Trigger"],
                "Entry_Price": row["Entry_Price"],
                "Stop_Loss_Price": row["Stop_Loss_Price"],
                "Take_Profit_Price": row["Take_Profit_Price"],
                "Risk_Reward": row["Risk_Reward"],
            })
            # confirm ho gaya -> watchlist se hata do, kaam ho gaya
        else:
            still_watching[pair] = entry  # abhi bhi wait karo

    # ---- STEP 2: aaj ke naye volume-spike coins watchlist mein add karo ----
    new_adds = 0
    for _, row in leaderboard.iterrows():
        pair = row["Pair"]
        rvol = row.get("RVOL_20")
        if pd.isna(rvol):
            continue

        if rvol >= RVOL_ALERT_THRESHOLD and not bool(row.get("Made_New_Low_Recently", False)):
            if pair not in still_watching:
                still_watching[pair] = {
                    "date": today,
                    "high": float(row["High"]),
                    "close": float(row["Close"]),
                    "rvol": float(rvol),
                }
                new_adds += 1

    save_watchlist(still_watching)

    # ---- Output ----
    if confirmed_rows:
        print(f"{len(confirmed_rows)} coin(s) CONFIRMED — kal volume spike hua tha, "
              f"aaj price kal ke High se upar nikal gayi:\n")
        print(pd.DataFrame(confirmed_rows).to_string(index=False))
    else:
        print("Aaj koi coin confirm nahi hua (purani watchlist mein se).")

    print(f"\n{new_adds} naya coin aaj watchlist mein add hua (volume spike dikha, "
          f"confirmation kal check hogi).")
    print(f"Total {len(still_watching)} coin(s) abhi watch mein hain.")

    # ---- Google Sheet mein bhejo ----
    push_to_sheet(confirmed_rows)

    # ---- Telegram pe bhejo (sirf agar confirm hua) ----
    push_to_telegram(confirmed_rows)


def push_to_sheet(confirmed_rows: list):
    """
    confirmed_rows (trade-ready coins) ko Google Sheet ke
    "Volume_Confirmed" tab mein likhta hai. Agar koi coin confirm
    nahi hua, tab bhi ek "no result" row likhta hai (timestamp ke
    saath) taaki Sheet check karne pe pata chale ki scan chala tha.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not confirmed_rows:
        placeholder = pd.DataFrame([{col: "-" for col in CONFIRMED_COLUMNS}])
        placeholder["Trend_Stage"] = "Koi coin aaj confirm nahi hua"
        placeholder["Last_Updated"] = timestamp
        write_leaderboard(placeholder, worksheet_name=SHEET_TAB_NAME)
    else:
        to_write = pd.DataFrame(confirmed_rows)[CONFIRMED_COLUMNS].copy()
        to_write["Last_Updated"] = timestamp
        write_leaderboard(to_write, worksheet_name=SHEET_TAB_NAME)

    print(f"\nGoogle Sheet ke '{SHEET_TAB_NAME}' tab mein bhi likh diya.")


def push_to_telegram(confirmed_rows: list):
    """
    Har CONFIRMED coin (2-din wala trade-ready signal) ke liye ek
    detailed message bhejta hai. Sirf tab bhejta hai jab kam se kam
    ek coin confirm hua ho — khali case mein chup rehta hai.
    """
    if not confirmed_rows:
        print("Telegram: koi coin confirm nahi hua, isliye message skip.")
        return

    today = datetime.date.today().strftime("%d-%b-%Y")
    lines = [f"✅ <b>Volume Confirmed</b> — {today}", f"{len(confirmed_rows)} coin(s) confirm hue:\n"]

    for row in confirmed_rows:
        lines.append(
            f"🎯 <b>{row['Pair']}</b>\n"
            f"   Spike Date: {row['Spike_Date']}  |  Spike High: {row['Spike_High']}\n"
            f"   Today Close: {row['Today_Close']}  |  RVOL: {round(row['RVOL_20_Today'], 2)}\n"
            f"   Entry: {row['Entry_Price']}  SL: {row['Stop_Loss_Price']}  TP: {row['Take_Profit_Price']}\n"
            f"   RR: {round(row['Risk_Reward'], 2) if row['Risk_Reward'] is not None else '-'}\n"
        )

    send_telegram_message("\n".join(lines))
    print("Telegram pe bhi message bhej diya.")


if __name__ == "__main__":
    main()