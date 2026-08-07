"""
notifications/telegram_bot.py

Simple helper: koi bhi text message tumhare Telegram bot se tumhare
phone pe bhej deta hai. Teeno scanner scripts ise use karenge jab
koi genuine signal mile.

HOW TO USE:
    from notifications.telegram_bot import send_telegram_message
    send_telegram_message("Hello from Python!")
"""

import requests

from telegram_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_telegram_message(text: str) -> bool:
    """
    Telegram pe message bhejta hai. Success pe True, fail pe False
    return karta hai (aur error print kar deta hai) — taaki agar
    Telegram fail bhi ho jaye, poora scanner script crash na ho.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(url, data=payload, timeout=15)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Telegram message bhejne mein error aaya: {e}")
        return False