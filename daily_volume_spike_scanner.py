from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import math
import time

import gspread
from google.oauth2.service_account import Credentials

from config import SHEET_ID, CREDENTIALS_FILE
from data.candles import get_candles
from exchange.coindcx import CoinDCX


# ==========================================================
# DAILY VOLUME SPIKE SCANNER — RESEARCH / TESTING MODE
# Existing V1-V6 logic is not modified.
# ==========================================================

RESOLUTION = "1D"
LOOKBACK_DAYS = 20
MAX_WORKERS = 8
SPIKE_MINIMUM = 2.0

WORKSHEET_NAME = "Daily_Volume_Spikes"

HEADERS = [
    "Date_UTC",
    "Pair",
    "Daily_Volume",
    "Avg_Volume_7D",
    "Multiple_7D",
    "Spike_7D",
    "Avg_Volume_14D",
    "Multiple_14D",
    "Spike_14D",
    "Price_Change_Pct",
    "Open",
    "High",
    "Low",
    "Close",
]


def get_spike_category(multiple):
    """Return the highest threshold crossed."""
    if multiple is None:
        return "N/A"
    if multiple >= 5.0:
        return "5X"
    if multiple >= 3.0:
        return "3X"
    if multiple >= 2.0:
        return "2X"
    return "NORMAL"


def safe_float(value):
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    return None


def analyse_pair(pair):
    """
    Latest complete daily candle is the candidate.
    Its volume is compared with the preceding 7 and 14
    complete daily candles. Candidate day is excluded
    from both averages.
    """
    try:
        df = get_candles(
            pair=pair,
            resolution=RESOLUTION,
            days=LOOKBACK_DAYS,
        )

        if df is None or df.empty or len(df) < 15:
            return None

        df = df.sort_values("Time").reset_index(drop=True)

        # data/candles.py removes today's incomplete UTC daily candle.
        # Use the most recent completed candle returned by that helper.
        candidate = df.iloc[-1]
        candidate_date = candidate["Time"].date()

        volumes = [
            safe_float(value)
            for value in df["Volume"].tolist()
        ]

        # Need 14 prior complete candles plus the candidate candle.
        previous_volumes = volumes[-15:-1]

        if len(previous_volumes) != 14:
            return None

        if any(v is None or v < 0 for v in previous_volumes):
            return None

        daily_volume = safe_float(candidate["Volume"])
        if daily_volume is None or daily_volume < 0:
            return None

        previous_7 = previous_volumes[-7:]
        previous_14 = previous_volumes

        avg_7 = sum(previous_7) / 7
        avg_14 = sum(previous_14) / 14

        multiple_7 = daily_volume / avg_7 if avg_7 > 0 else None
        multiple_14 = daily_volume / avg_14 if avg_14 > 0 else None

        spike_7 = get_spike_category(multiple_7)
        spike_14 = get_spike_category(multiple_14)

        # Research sheet records only coins with at least a 2x
        # volume increase against either baseline.
        qualifies = (
            (multiple_7 is not None and multiple_7 >= SPIKE_MINIMUM)
            or
            (multiple_14 is not None and multiple_14 >= SPIKE_MINIMUM)
        )

        if not qualifies:
            return None

        open_price = safe_float(candidate["Open"])
        high_price = safe_float(candidate["High"])
        low_price = safe_float(candidate["Low"])
        close_price = safe_float(candidate["Close"])

        if open_price is not None and open_price != 0 and close_price is not None:
            price_change_pct = (
                (close_price - open_price) / open_price
            ) * 100
        else:
            price_change_pct = None

        return [
            candidate_date.isoformat(),
            pair,
            round(daily_volume, 8),
            round(avg_7, 8),
            round(multiple_7, 3) if multiple_7 is not None else "",
            spike_7,
            round(avg_14, 8),
            round(multiple_14, 3) if multiple_14 is not None else "",
            spike_14,
            round(price_change_pct, 3) if price_change_pct is not None else "",
            open_price if open_price is not None else "",
            high_price if high_price is not None else "",
            low_price if low_price is not None else "",
            close_price if close_price is not None else "",
        ]

    except Exception as exc:
        print(f"[ERROR] {pair}: {exc}")
        return None


def get_worksheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_file(
        str(CREDENTIALS_FILE),
        scopes=scopes,
    )

    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(SHEET_ID)

    try:
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=WORKSHEET_NAME,
            rows=2000,
            cols=len(HEADERS),
        )

    worksheet.update(
        range_name=f"A1:N1",
        values=[HEADERS],
    )

    return worksheet


def save_results(results):
    if not results:
        print("No 2x-or-higher daily volume spikes found.")
        return

    worksheet = get_worksheet()
    existing_rows = worksheet.get_all_values()

    # Build a date + pair index to prevent duplicates on reruns.
    existing_index = {}

    for row_number, row in enumerate(existing_rows[1:], start=2):
        if len(row) >= 2 and row[0] and row[1]:
            existing_index[(row[0], row[1])] = row_number

    new_rows = []
    updated_count = 0

    for result in results:
        key = (result[0], result[1])
        existing_row_number = existing_index.get(key)

        if existing_row_number:
            worksheet.update(
                range_name=f"A{existing_row_number}:N{existing_row_number}",
                values=[result],
            )
            updated_count += 1
        else:
            new_rows.append(result)

    if new_rows:
        worksheet.append_rows(
            new_rows,
            value_input_option="USER_ENTERED",
        )

    print(f"New spike rows added: {len(new_rows)}")
    print(f"Existing rows updated: {updated_count}")
    print(f"Worksheet: {WORKSHEET_NAME}")


def main():
    print("=" * 60)
    print("DAILY VOLUME SPIKE SCANNER — TESTING MODE")
    print(f"Run time (UTC): {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    pairs = CoinDCX().get_active_pairs()
    pairs = list(dict.fromkeys(pairs))

    print(f"Active futures pairs found: {len(pairs)}")

    results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(analyse_pair, pair): pair
            for pair in pairs
        }

        for future in as_completed(futures):
            completed += 1

            try:
                result = future.result()
                if result is not None:
                    results.append(result)
                    print(
                        f"[SPIKE] {result[1]} | "
                        f"Date={result[0]} | "
                        f"7D={result[4]}x ({result[5]}) | "
                        f"14D={result[7]}x ({result[8]})"
                    )
            except Exception as exc:
                print(f"[ERROR] {futures[future]}: {exc}")

            if completed % 50 == 0:
                print(f"Progress: {completed}/{len(pairs)}")

    # Sort results by date, then pair, before saving.
    results.sort(key=lambda row: (row[0], row[1]))

    save_results(results)

    print(f"Candidate rows found: {len(results)}")
    print("Research scan complete. No trades were executed.")


if __name__ == "__main__":
    main()
