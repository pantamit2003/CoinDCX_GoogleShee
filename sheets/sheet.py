import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from config import SHEET_ID, CREDENTIALS_FILE


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


creds = Credentials.from_service_account_file(
    str(CREDENTIALS_FILE),
    scopes=SCOPES
)

client = gspread.authorize(creds)

sheet = client.open_by_key(SHEET_ID).sheet1


def clear_sheet():
    """
    Clear complete sheet.
    """
    sheet.clear()


def upload_dataframe(df: pd.DataFrame):
    """
    Upload Pandas DataFrame to Google Sheet.
    """

    clear_sheet()

    rows = [df.columns.tolist()] + df.values.tolist()

    sheet.update(rows)

    print(f"✅ Uploaded {len(df)} rows to Google Sheet.")


def append_row(row):
    """
    Append single row.
    """

    sheet.append_row(row)

    print("✅ Row Added")


def read_sheet():
    """
    Read entire sheet.
    """

    return sheet.get_all_records()