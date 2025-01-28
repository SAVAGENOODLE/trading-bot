import requests
import json
import sqlite3
import time
from datetime import datetime
import configparser
import telebot

# Load Configuration
config = configparser.ConfigParser()
config.read("config.ini")

# Constants
PUMPFUN_URL = config.get("API", "PUMPFUN_URL", fallback="https://pumpfunadvanced.com/api/migrated_coins")
RUGCHECK_URL = config.get("API", "RUGCHECK_URL", fallback="http://api.rugcheck.xyz/v1/check")
TWEETSCOUT_URL = config.get("API", "TWEETSCOUT_URL", fallback="http://api.tweetscout.io/v1/analyze")
TWEETSCOUT_API_KEY = config.get("API", "TWEETSCOUT_API_KEY", fallback="your_tweetscout_api_key")
TELEGRAM_BOT_TOKEN = config.get("TELEGRAM", "BOT_TOKEN", fallback="your_telegram_bot_token")
TELEGRAM_CHAT_ID = config.get("TELEGRAM", "CHAT_ID", fallback="your_telegram_chat_id")
BONKBOT_COMMAND_PREFIX = config.get("TELEGRAM", "BONKBOT_COMMAND_PREFIX", fallback="/bonk")
DATABASE_NAME = config.get("DATABASE", "DATABASE_NAME", fallback="pumpfun_coins.db")
TABLE_NAME = config.get("DATABASE", "TABLE_NAME", fallback="migrated_coins")
TWITTER_TABLE_NAME = config.get("DATABASE", "TWITTER_TABLE_NAME", fallback="twitter_analysis")

# Load Filters and Blacklists
COIN_FILTERS = json.loads(config.get("FILTERS", "COIN_FILTERS", fallback="[]"))
COIN_BLACKLIST = json.loads(config.get("BLACKLISTS", "COIN_BLACKLIST", fallback="[]"))
DEV_BLACKLIST = json.loads(config.get("BLACKLISTS", "DEV_BLACKLIST", fallback="[]"))

# Initialize Telegram Bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Initialize SQLite Database
def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    # Table for migrated coins
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_address TEXT UNIQUE,
            name TEXT,
            symbol TEXT,
            migrated_at TEXT,
            initial_price REAL,
            current_price REAL,
            volume REAL,
            market_cap REAL,
            dev_address TEXT,
            rugcheck_status TEXT,
            supply_bundled INTEGER DEFAULT 0
        )
    """)

    # Table for Twitter analysis
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {TWITTER_TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE,
            twitter_handle TEXT,
            followers INTEGER,
            engagement_rate REAL,
            sentiment_score REAL,
            last_updated TEXT
        )
    """)

    conn.commit()
    conn.close()

# Fetch Data from PumpFun Advanced
def fetch_pumpfun_data():
    try:
        response = requests.get(PUMPFUN_URL)
        response.raise_for_status()
        return response.json()  # Assuming the API returns JSON data
    except requests.exceptions.RequestException as e:
        print(f"Error fetching PumpFun data: {e}")
        return None

# Check Token on RugCheck
def check_rugcheck(contract_address):
    try:
        params = {"contract_address": contract_address}
        response = requests.get(RUGCHECK_URL, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error checking contract {contract_address} on RugCheck: {e}")
        return None

# Fetch Twitter Data from TweetScout
def fetch_twitter_data(symbol, twitter_handle):
    try:
        headers = {"Authorization": f"Bearer {TWEETSCOUT_API_KEY}"}
        params = {"handle": twitter_handle}
        response = requests.get(TWEETSCOUT_URL, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Twitter data for {symbol}: {e}")
        return None

# Save Twitter Data to Database
def save_twitter_data(symbol, twitter_data):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    twitter_handle = twitter_data.get("handle")
    followers = twitter_data.get("followers", 0)
    engagement_rate = twitter_data.get("engagement_rate", 0.0)
    sentiment_score = twitter_data.get("sentiment_score", 0.0)
    last_updated = datetime.utcnow().isoformat()

    cursor.execute(f"""
        INSERT OR REPLACE INTO {TWITTER_TABLE_NAME} (
            symbol, twitter_handle, followers, engagement_rate, sentiment_score, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (symbol, twitter_handle, followers, engagement_rate, sentiment_score, last_updated))

    conn.commit()
    conn.close()

# Apply RugCheck Results
def apply_rugcheck_results(coin):
    contract_address = coin.get("contract_address")
    rugcheck_data = check_rugcheck(contract_address)

    if rugcheck_data:
        rugcheck_status = rugcheck_data.get("status", "Unknown")
        supply_bundled = rugcheck_data.get("supply_bundled", False)

        # Update coin data with RugCheck results
        coin["rugcheck_status"] = rugcheck_status
        coin["supply_bundled"] = int(supply_bundled)

        # Blacklist if status is not "Good" or supply is bundled
        if rugcheck_status != "Good" or supply_bundled:
            symbol = coin.get("symbol")
            dev_address = coin.get("dev_address")

            if symbol and symbol not in COIN_BLACKLIST:
                COIN_BLACKLIST.append(symbol)
                print(f"Added {symbol} to COIN_BLACKLIST.")

            if dev_address and dev_address not in DEV_BLACKLIST:
                DEV_BLACKLIST.append(dev_address)
                print(f"Added {dev_address} to DEV_BLACKLIST.")

    return coin

# Parse and Save Data
def parse_and_save_data(data):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    for coin in data:
        # Apply RugCheck results
        coin = apply_rugcheck_results(coin)

        # Skip blacklisted coins
        if coin.get("symbol") in COIN_BLACKLIST or coin.get("dev_address") in DEV_BLACKLIST:
            continue

        contract_address = coin.get("contract_address")
        name = coin.get("name")
        symbol = coin.get("symbol")
        migrated_at = coin.get("migrated_at", datetime.utcnow().isoformat())
        initial_price = coin.get("initial_price", 0.0)
        current_price = coin.get("current_price", 0.0)
        volume = coin.get("volume", 0.0)
        market_cap = coin.get("market_cap", 0.0)
        dev_address = coin.get("dev_address", "")
        rugcheck_status = coin.get("rugcheck_status", "Unknown")
        supply_bundled = coin.get("supply_bundled", 0)

        # Insert or ignore if the coin already exists
        cursor.execute(f"""
            INSERT OR IGNORE INTO {TABLE_NAME} (
                contract_address, name, symbol, migrated_at, initial_price, current_price, volume, market_cap, dev_address, rugcheck_status, supply_bundled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (contract_address, name, symbol, migrated_at, initial_price, current_price, volume, market_cap, dev_address, rugcheck_status, supply_bundled))

    conn.commit()
    conn.close()

# Execute Trade via BonkBot
def execute_trade(symbol, action):
    command = f"{BONKBOT_COMMAND_PREFIX} {action} {symbol}"
    bot.send_message(TELEGRAM_CHAT_ID, command)
    print(f"Sent {action} command for {symbol} to BonkBot.")

# Send Telegram Notification
def send_notification(message):
    bot.send_message(TELEGRAM_CHAT_ID, message)
    print(f"Sent notification: {message}")

# Main Bot Loop
def main():
    init_db()

    while True:
        print("Fetching PumpFun data...")
        pumpfun_data = fetch_pumpfun_data()
        if pumpfun_data:
            parse_and_save_data(pumpfun_data)

            # Example: Execute trades for selected tokens
            for coin in pumpfun_data:
                symbol = coin.get("symbol")
                if symbol and coin.get("rugcheck_status") == "Good":
                    execute_trade(symbol, "buy")
                    send_notification(f"Buy order placed for {symbol}.")

        else:
            print("No PumpFun data fetched. Retrying...")

        # Wait for 5 minutes before the next run
        time.sleep(300)

if __name__ == "__main__":
    main()