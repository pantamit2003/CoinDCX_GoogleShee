"""
test_telegram.py

Sirf ye check karne ke liye ki Telegram bot sahi se message bhej pa
raha hai ya nahi. Chalao aur apne phone pe dekho message aaya ya nahi.
"""

from notifications.telegram_bot import send_telegram_message

if __name__ == "__main__":
    success = send_telegram_message("Hello from Python! Telegram bot working hai.")
    if success:
        print("Message bhej diya — apna Telegram phone pe check karo.")
    else:
        print("Message bhejne mein fail hua — upar error dekho.")