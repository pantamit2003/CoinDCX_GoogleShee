"""
volume_watch.py

2-DIN WALA TRACKING SYSTEM — jo tumne bola wahi idea:

  DIN 1 (candle-day): Jis coin ka volume us din spike hua (RVOL_20 >= 2.0),
  uska naam aur us din ka High + Close ek file (volume_watchlist.json)
  mein save kar lo. Koi trade nahi, sirf note karna. YE LIST TERMINAL +
  SHEET ("Volume_Watching" tab) + TELEGRAM teeno mein dikhti hai.

  Ab har coin ke saath ek "Spike_Confidence" bhi dikhta hai — 4 alag
  time-windows (20/40/60/80 din) se compare karke, kitne windows mein
  volume genuinely unusual hai.

  DIN 2 (agla candle-day): Isi script ko phir chalao. Ye pehle watchlist
  file kholega, aur check karega — jo coins pichhle din note kiye the,
  unka price us din ke High se upar nikla kya? Agar haan -> CONFIRMED,
  trade layak signal (Sheet: "Volume_Confirmed" tab). Agar nahi ->
  chup-chap discard, koi trade nahi.

IMPORTANT FIX (8 Aug 2026): Pehle ye script "Spike_Date" ke liye LOCAL
SYSTEM DATE use karta tha (jab script chali, wahi date save hoti thi).
Lekin candles.py ka fix hamesha "kal" tak ki COMPLETE candle analyze
karta hai (aaj ki incomplete candle drop ho jaati hai) — toh agar tum
"8 Aug" ko din mein script chalao, asal candle jo analyze hoti hai wo
"7 Aug" ki hoti hai, lekin local-date se Spike_Date "8 Aug" save ho
jaata tha — EK DIN AAGE, galat label.

FIX: Ab Spike_Date, aur age-tracking (3-din discard) dono, LEADERBOARD
ke andar wali ASAL CANDLE DATE (row["Time"]) se calculate hote hain —
system ki local date se nahi. Isse label hamesha sahi candle ki date
dikhayega, chahe script kisi bhi time pe chale.

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
MAX_WATCH_AGE_DAYS = 3          # itne (candle) din tak confirm na ho toh discard kar do

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


def get_candle_date(leaderboard: pd.DataFrame, fallback: str) -> str:
    """
    Leaderboard ke andar se ASAL candle ki date nikalta hai (row["Time"]
    se) — local system date pe depend nahi karta. Saare coins ka "Time"
    same hona chahiye (sabki last complete daily candle same din ki
    hoti hai), isliye pehli valid row se le lete hain.
    Agar kisi wajah se "Time" available na ho, fallback (system date)
    use hota hai taaki script crash na ho.
    """
    if "Time" in leaderboard.columns:
        valid_times = leaderboard["Time"].dropna()
        if not valid_times.empty:
            return pd.to_datetime(valid_times.iloc[0]).date().isoformat()
    return fallback


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
    run_timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_date_fallback = datetime.date.today().isoformat()
    print(f"Volume-watch chal raha hai — {run_timestamp}\n")

    leaderboard = scan_today()
    if leaderboard.empty:
        print("Koi data nahi mila, scan fail hua.")
        return

    # ---- ASAL candle ki date nikalo (local system date se NAHI) ----
    candle_date = get_candle_date(leaderboard, fallback=system_date_fallback)
    print(f"Analyze ho rahi candle ki date: {candle_date}\n")

    watchlist = load_watchlist()
    confirmed_rows = []
    still_watching = {}

    # ---- STEP 1: purani watchlist ke coins check karo — confirm hue ya nahi ----
    for pair, entry in watchlist.items():
        age_days = (
            datetime.date.fromisoformat(candle_date)
            - datetime.date.fromisoformat(entry["date"])
        ).days

        if age_days > MAX_WATCH_AGE_DAYS:
            continue  # bahut purana ho gaya, discard — kabhi confirm nahi hua

        row_match = leaderboard[leaderboard["Pair"] == pair]
        if row_match.empty:
            still_watching[pair] = entry  # aaj data nahi mila, kal try karo
            continue

        row = row_match.iloc[0]

        # CONFIRM hone ki shart: is candle ka close, jis din volume spike
        # hua tha uske High se upar nikal jaye, aur coin abhi "still
        # falling" na ho
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

    # ---- STEP 2: is candle-din ke naye volume-spike coins watchlist mein add karo ----
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
                    "date": candle_date,
                    "high": float(row["High"]),
                    "close": float(row["Close"]),
                    "rvol": float(rvol_20),
                }
                new_spike_rows.append({
                    "Pair": pair,
                    "Spike_Date": candle_date,
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
        print(f"{len(confirmed_rows)} coin(s) CONFIRMED — pichhle candle-din volume spike hua tha, "
              f"is candle ka close pichhle High se upar nikal gaya:\n")
        print(pd.DataFrame(confirmed_rows).to_string(index=False))
    else:
        print("Is candle-din koi coin confirm nahi hua (purani watchlist mein se).")

    if new_spike_rows:
        sorted_spikes = sorted(new_spike_rows, key=lambda r: r["_confidence_count"], reverse=True)
        print(f"\n{len(new_spike_rows)} NAYA coin {candle_date} ki candle mein volume-spike dikha "
              f"(confirmation agli candle pe check hogi), sabse genuine pehle:\n")
        display_df = pd.DataFrame(sorted_spikes).drop(columns=["_confidence_count"])
        print(display_df.to_string(index=False))
    else:
        print(f"\n{candle_date} ki candle mein koi naya volume-spike coin nahi mila.")

    print(f"\nTotal {len(still_watching)} coin(s) abhi watch mein hain.")

    # ---- Google Sheet mein bhejo (2 tabs) ----
    push_confirmed_to_sheet(confirmed_rows, run_timestamp)
    push_watching_to_sheet(new_spike_rows, run_timestamp)

    # ---- Telegram pe bhejo (dono, sirf agar data ho) ----
    push_confirmed_to_telegram(confirmed_rows)
    push_watching_to_telegram(new_spike_rows, candle_date)


def push_confirmed_to_sheet(confirmed_rows: list, run_timestamp: str):
    """
    confirmed_rows (trade-ready coins) ko Google Sheet ke
    "Volume_Confirmed" tab mein likhta hai. Agar koi coin confirm
    nahi hua, tab bhi ek "no result" row likhta hai (timestamp ke
    saath) taaki Sheet check karne pe pata chale ki scan chala tha.
    """
    if not confirmed_rows:
        placeholder = pd.DataFrame([{col: "-" for col in CONFIRMED_COLUMNS}])
        placeholder["Trend_Stage"] = "Koi coin confirm nahi hua"
        placeholder["Last_Updated"] = run_timestamp
        write_leaderboard(placeholder, worksheet_name=SHEET_TAB_CONFIRMED)
    else:
        to_write = pd.DataFrame(confirmed_rows)[CONFIRMED_COLUMNS].copy()
        to_write["Last_Updated"] = run_timestamp
        write_leaderboard(to_write, worksheet_name=SHEET_TAB_CONFIRMED)

    print(f"\nGoogle Sheet ke '{SHEET_TAB_CONFIRMED}' tab mein bhi likh diya.")


def push_watching_to_sheet(new_spike_rows: list, run_timestamp: str):
    """
    new_spike_rows (is candle-din ke fresh volume-spike coins, abhi tak
    UNCONFIRMED) ko Google Sheet ke "Volume_Watching" tab mein likhta
    hai — 4-window RVOL aur Spike_Confidence ke saath.
    """
    if not new_spike_rows:
        placeholder = pd.DataFrame([{col: "-" for col in WATCHING_COLUMNS}])
        placeholder["Pair"] = "Koi naya volume-spike coin nahi mila"
        placeholder["Last_Updated"] = run_timestamp
        write_leaderboard(placeholder, worksheet_name=SHEET_TAB_WATCHING)
    else:
        to_write = pd.DataFrame(new_spike_rows).drop(columns=["_confidence_count"])
        to_write = to_write[WATCHING_COLUMNS].copy()
        to_write["Last_Updated"] = run_timestamp
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

    today_label = datetime.date.today().strftime("%d-%b-%Y")
    lines = [f"✅ <b>Volume Confirmed</b> — {today_label}", f"{len(confirmed_rows)} coin(s) confirm hue:\n"]

    for row in confirmed_rows:
        lines.append(
            f"🎯 <b>{row['Pair']}</b>\n"
            f"   Spike Date: {row['Spike_Date']}  |  Spike High: {row['Spike_High']}\n"
            f"   Close: {row['Today_Close']}  |  RVOL: {round(row['RVOL_20_Today'], 2)}\n"
            f"   Entry: {row['Entry_Price']}  SL: {row['Stop_Loss_Price']}  TP: {row['Take_Profit_Price']}\n"
            f"   RR: {round(row['Risk_Reward'], 2) if row['Risk_Reward'] is not None else '-'}\n"
        )

    send_telegram_message("\n".join(lines))
    print("Telegram pe 'Confirmed' message bhej diya.")


def push_watching_to_telegram(new_spike_rows: list, candle_date: str):
    """
    Is candle-din ke fresh volume-spike coins (abhi UNCONFIRMED) ki ek
    halki list bhejta hai — koi Entry/SL/TP nahi (kyunki abhi confirm
    nahi hua), sirf "inpe nazar rakho" wala note. Spike_Confidence bhi
    dikhata hai (4/4 = sabse genuine).
    """
    if not new_spike_rows:
        print("Telegram: koi naya spike nahi mila, isliye 'Watching' message skip.")
        return

    lines = [f"👀 <b>Naya Volume Spike</b> — candle date {candle_date}",
             f"{len(new_spike_rows)} coin(s) mein volume achanak badha, agli candle pe confirmation check hoga:\n"]

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
