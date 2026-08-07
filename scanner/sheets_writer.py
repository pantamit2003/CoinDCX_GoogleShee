"""
scanner/sheets_writer.py

Layer 10 — Output: writes the ranking leaderboard to Google Sheets.

Uses your existing config.py (SHEET_ID, CREDENTIALS_FILE) and the
gspread + google-auth libraries already in requirements.txt.

WHY A SEPARATE MODULE:
Keeps "how we present/store results" completely separate from "how we
calculate them" (Layers 1-8). This means later you could add a second
output — e.g. a Telegram alert, or a local CSV log — without touching
any of the scoring/ranking logic at all.
"""

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

import config


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class SheetsWriter:

    def __init__(self, sheet_id=None, credentials_file=None, worksheet_name="Leaderboard"):
        self.sheet_id = sheet_id or config.SHEET_ID
        self.credentials_file = credentials_file or config.CREDENTIALS_FILE
        self.worksheet_name = worksheet_name
        self._client = None
        self._sheet = None

    # -----------------------------------------------------------------
    def _connect(self):
        if self._client is None:
            creds = Credentials.from_service_account_file(
                str(self.credentials_file), scopes=SCOPES
            )
            self._client = gspread.authorize(creds)

        if self._sheet is None:
            spreadsheet = self._client.open_by_key(self.sheet_id)
            try:
                self._sheet = spreadsheet.worksheet(self.worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                self._sheet = spreadsheet.add_worksheet(
                    title=self.worksheet_name, rows=200, cols=20
                )

        return self._sheet

    # -----------------------------------------------------------------
    def write(self, leaderboard: pd.DataFrame):
        sheet = self._connect()

        # Google Sheets can't handle NaN / NaT — convert to empty strings
        clean = leaderboard.fillna("")

        # Round numeric columns for a cleaner sheet
        for col in ["Confidence", "Trend_Score", "Momentum_Score",
                    "Volume_Score", "Breakout_Score", "RS_Score",
                    "Risk_Reward", "Expected_Move_Percent"]:
            if col in clean.columns:
                clean[col] = clean[col].apply(
                    lambda v: round(v, 2) if isinstance(v, (int, float)) else v
                )

        headers = clean.columns.tolist()
        rows = clean.values.tolist()

        sheet.clear()
        sheet.update([headers] + rows)

        print(f"Google Sheet updated: {len(rows)} coins written to '{self.worksheet_name}' tab.")


# ---------- Helper Function (matches your existing convention) ----------
def write_leaderboard(leaderboard, **kwargs):
    SheetsWriter(**kwargs).write(leaderboard)