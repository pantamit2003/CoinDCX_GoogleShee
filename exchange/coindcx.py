import requests


BASE_URL = "https://public.coindcx.com"


class CoinDCX:

    def __init__(self):
        self.base_url = BASE_URL

    # --------------------------------
    # Get Futures Current Prices
    # --------------------------------
    def get_futures_prices(self):

        url = f"{self.base_url}/market_data/v3/current_prices/futures/rt"

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        response.raise_for_status()

        return response.json()

    # --------------------------------
    # Get Active Futures Pairs
    # --------------------------------
    def get_active_pairs(self):

        data = self.get_futures_prices()

        prices = data.get("prices", {})

        return list(prices.keys())


# ==================================================
# Shortcut Functions
# Purane modules ke compatibility ke liye
# ==================================================

def get_futures_prices():
    return CoinDCX().get_futures_prices()


def get_active_pairs():
    return CoinDCX().get_active_pairs()
