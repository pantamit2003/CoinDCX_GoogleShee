"""
volume_watch.py

2-DIN WALA TRACKING SYSTEM — jo tumne bola wahi idea:

  DIN 1 (aaj): Jis coin ka volume aaj spike hua (RVOL_20 >= 2.0), uska
  naam aur aaj ka High + Close ek file (volume_watchlist.json) mein
  save kar lo. Koi trade nahi, sirf note karna. YE LIST AB TERMINAL +
  SHEET ("Volume_Watching" tab) + TELEGRAM teeno mein dikhti hai.

  Ab har coin ke saath ek "Spike_Confidence" bhi dikhta hai — 4 alag
  time-windows (20/40/60/80 din) se compare karke, kitne windows mein
  volume genuinely unusual hai. "4/4" matlab pichhle 80 din ke muqable
  bhi ye volume unusual hai — bahut genuine spike. "1/4" matlab sirf
  pichhle 20 din ke muqable hi zyada laga, shayad chhota lull tha,
  itna trust-worthy nahi.

  DIN 2 (kal): Isi script ko phir chalao. Ye pehle watchlist file
  kholega, aur check karega — jo coins kal note kiye the, unka price
  AAJ kal ke High se upar nikla kya? Agar haan -> CONFIRMED, trade
  layak signal (Sheet: "Volume_Confirmed" tab). Agar nahi -> chup-chap
  discard, koi trade nahi.

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

# Kitne alag time-windows se volume ko compare karna hai — jitna zyada
# window pass kare, utna genuine spike (short-term fluke nahi)
RVOL_WINDOWS = [20, 40, 60, 80]

# None = saare active pairs scan karo (automation ke liye). Testing ke
# liye kabhi jaldi result chahiye ho toh yahan koi number (jaise 250)
# daal sakte ho.
MAX_PAIRS_TO_SCAN = None

SHEET_TAB_CONFIRMED = "Volume_Confirmed"
SHEET_TAB_WATCHING = "Volume_Watching"   # naya tab — Din-1 fresh spikes

CONFIRMED_COLUMNS = [
    "Pair", "Spike_Date", "Spike_High", "Today_Close", "RVOL_20_Today",
    "Trend_Stage", "Entry_Trigger", "Entry_Price", "Stop_Loss_Price",
    "Take_Profit_Price", "Risk_Reward",
]

WATCHING_COLUMNS = [
    "Pair", "Spike_Date", "Spike_High", "Spike_Close",
    "RVOL_20", "RVOL_40", "RVOL_60", "RVOL_80", "Spike_Confidence",
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


def spike_confidence(row) -> tuple:
    """
    Har RVOL window (20/40/60/80) ko check karta hai — kitne mein
    volume >= threshold hai. Return: (count, "count/total" string).
    Jitna zyada count, utna genuine spike (sirf ek chhoti window ka
    fluke nahi, balki lambe time ke muqable bhi unusual hai).
    """
    passed = 0
    for w in RVOL_WINDOWS:
        val = row.get(f"RVOL_{w}")
        if pd.notna(val) and val >= RVOL_ALERT_THRESHOLD:
            passed += 1
    return passed, f"{passed}/{len(RVOL_WINDOWS)}"


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
    # (ye woh list hai jo tumne poocha — "aaj kis coin ka volume achanak
    # bahut zyada hai pichhle din(s) ke muqable" — Din-1 ka detection.
    # Ab 4 windows se check hota hai, sirf 20-din se nahi)
    new_spike_rows = []
    for _, row in leaderboard.iterrows():
        pair = row["Pair"]
        rvol_20 = row.get("RVOL_20")
        if pd.isna(rvol_20):
            continue

        if rvol_20 >= RVOL_ALERT_THRESHOLD and not bool(row.get("Made_New_Low_Recently", False)):
            if pair not in still_watching:
                confidence_count, confidence_label = spike_confidence(row)
                still_watching[pair] = {
                    "date": today,
                    "high": float(row["High"]),
                    "close": float(row["Close"]),
                    "rvol": float(rvol_20),
                }
                new_spike_rows.append({
                    "Pair": pair,
                    "Spike_Date": today,
                    "Spike_High": float(row["High"]),
                    "Spike_Close": float(row["Close"]),
                    "RVOL_20": float(rvol_20),
                    "RVOL_40": float(row["RVOL_40"]) if pd.notna(row.get("RVOL_40")) else None,
                    "RVOL_60": float(row["RVOL_60"]) if pd.notna(row.get("RVOL_60")) else None,
                    "RVOL_80": float(row["RVOL_80"]) if pd.notna(row.get("RVOL_80")) else None,
                    "Spike_Confidence": confidence_label,
                    "_confidence_count": confidence_count,   # sirf sorting ke liye
                })

    save_watchlist(still_watching)

    # ---- Output ----
    if confirmed_rows:
        print(f"{len(confirmed_rows)} coin(s) CONFIRMED — kal volume spike hua tha, "
              f"aaj price kal ke High se upar nikal gayi:\n")
        print(pd.DataFrame(confirmed_rows).to_string(index=False))
    else:
        print("Aaj koi coin confirm nahi hua (purani watchlist mein se).")

    if new_spike_rows:
        # sabse genuine (zyada windows pass) sabse upar dikhado
        sorted_spikes = sorted(new_spike_rows, key=lambda r: r["_confidence_count"], reverse=True)
        print(f"\n{len(new_spike_rows)} NAYA coin aaj volume-spike dikha "
              f"(confirmation kal check hogi), sabse genuine pehle:\n")
        display_df = pd.DataFrame(sorted_spikes).drop(columns=["_confidence_count"])
        print(display_df.to_string(index=False))
    else:
        print("\nAaj koi naya volume-spike coin nahi mila.")

    print(f"\nTotal {len(still_watching)} coin(s) abhi watch mein hain.")

    # ---- Google Sheet mein bhejo (2 tabs) ----
    push_confirmed_to_sheet(confirmed_rows)
    push_watching_to_sheet(new_spike_rows)

    # ---- Telegram pe bhejo (dono, sirf agar data ho) ----
    push_confirmed_to_telegram(confirmed_rows)
    push_watching_to_telegram(new_spike_rows)


def push_confirmed_to_sheet(confirmed_rows: list):
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
        write_leaderboard(placeholder, worksheet_name=SHEET_TAB_CONFIRMED)
    else:
        to_write = pd.DataFrame(confirmed_rows)[CONFIRMED_COLUMNS].copy()
        to_write["Last_Updated"] = timestamp
        write_leaderboard(to_write, worksheet_name=SHEET_TAB_CONFIRMED)

    print(f"\nGoogle Sheet ke '{SHEET_TAB_CONFIRMED}' tab mein bhi likh diya.")


def push_watching_to_sheet(new_spike_rows: list):
    """
    new_spike_rows (aaj ke fresh Din-1 volume-spike coins, abhi tak
    UNCONFIRMED) ko Google Sheet ke "Volume_Watching" tab mein likhta
    hai — 4-window RVOL aur Spike_Confidence ke saath. Har roz
    overwrite hota hai — sirf AAJ ke naye spikes dikhata hai.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not new_spike_rows:
        placeholder = pd.DataFrame([{col: "-" for col in WATCHING_COLUMNS}])
        placeholder["Pair"] = "Aaj koi naya volume-spike coin nahi mila"
        placeholder["Last_Updated"] = timestamp
        write_leaderboard(placeholder, worksheet_name=SHEET_TAB_WATCHING)
    else:
        to_write = pd.DataFrame(new_spike_rows).drop(columns=["_confidence_count"])
        to_write = to_write[WATCHING_COLUMNS].copy()
        to_write["Last_Updated"] = timestamp
        write_leaderboard(to_write, worksheet_name=SHEET_TAB_WATCHING)

    print(f"Google Sheet ke '{SHEET_TAB_WATCHING}' tab mein bhi likh diya.")


def push_confirmed_to_telegram(confirmed_rows: list):
    """
    Har CONFIRMED coin (2-din wala trade-ready signal) ke liye ek
    detailed message bhejta hai. Sirf tab bhejta hai jab kam se kam
    ek coin confirm hua ho.
    """
    if not confirmed_rows:
        print("Telegram: koi coin confirm nahi hua, isliye 'Confirmed' message skip.")
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
    print("Telegram pe 'Confirmed' message bhej diya.")


def push_watching_to_telegram(new_spike_rows: list):
    """
    Aaj ke fresh Din-1 volume-spike coins (abhi UNCONFIRMED) ki ek
    halki list bhejta hai — koi Entry/SL/TP nahi (kyunki abhi confirm
    nahi hua), sirf "inpe nazar rakho, kal confirm hoga" wala note.
    Spike_Confidence bhi dikhata hai (4/4 = sabse genuine).
    """
    if not new_spike_rows:
        print("Telegram: koi naya spike nahi mila, isliye 'Watching' message skip.")
        return

    today = datetime.date.today().strftime("%d-%b-%Y")
    lines = [f"👀 <b>Naya Volume Spike (Din-1)</b> — {today}",
             f"{len(new_spike_rows)} coin(s) mein volume achanak badha, kal confirmation check hoga:\n"]

    sorted_rows = sorted(new_spike_rows, key=lambda r: r["_confidence_count"], reverse=True)
    for row in sorted_rows:
        lines.append(
            f"• {row['Pair']} — RVOL {round(row['RVOL_20'], 2)}x  "
            f"(High: {row['Spike_High']})  Confidence: {row['Spike_Confidence']}"
        )

    send_telegram_message("\n".join(lines))
    print("Telegram pe 'Watching' message bhej diya.")


if __name__ == "__main__":
    main()
