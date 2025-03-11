
import os
import logging
import time
import json
import requests
import random
from decimal import Decimal
import ccxt
import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from random_user_agent.user_agent import UserAgent
from random_user_agent.params import SoftwareName, OperatingSystem
from ta.volatility import AverageTrueRange
from ta.momentum import RSIIndicator
from tradingview_ta import TA_Handler, Interval
from database import log_trade, log_order, update_trade

# Configuration and Setup
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG for detailed logging
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
# Environment Variables
analyzer = SentimentIntensityAnalyzer()
API_KEY = os.getenv("KUCOIN_API_KEY", "67c9d31345e41a0001676a6f")
API_SECRET = os.getenv("KUCOIN_API_SECRET", "7b93d018-c606-40db-b8d0-b4bee166eef1")
API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "8529118#Ahe")
import os
import logging
import time
import json
import requests
import ccxt
import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from random_user_agent.user_agent import UserAgent
from random_user_agent.params import SoftwareName, OperatingSystem
from ta.volatility import AverageTrueRange
from ta.momentum import RSIIndicator
from tradingview_ta import TA_Handler, Interval

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

API_KEY = os.getenv("KUCOIN_API_KEY", "67c9d31345e41a0001676a6f")
API_SECRET = os.getenv("KUCOIN_API_SECRET", "7b93d018-c606-40db-b8d0-b4bee166eef1")
API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "8529118#Ahe")
analyzer = SentimentIntensityAnalyzer()

def prepare_headers():
    """Generate random User-Agent headers."""
    ua = UserAgent(software_names=[SoftwareName.CHROME.value, SoftwareName.FIREFOX.value],
                   operating_systems=[OperatingSystem.WINDOWS.value, OperatingSystem.LINUX.value])
    return {"User-Agent": ua.get_random_user_agent()}

def init_kucoin_client():
    """Initialize KuCoin Futures client."""
    try:
        exchange = ccxt.kucoinfutures({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'password': API_PASSPHRASE,
            'enableRateLimit': True,
        })
        exchange.load_markets()
        logging.info(f"Loaded {len(exchange.markets)} markets.")
        return exchange
    except Exception as e:
        logging.error(f"Error initializing KuCoin: {e}")
        return None

def select_top_5_pumping_coins(exchange):
    """Select top 5 coins with highest 1h price change."""
    try:
        markets = exchange.markets
        futures_markets = [m for m in markets.values() if m['swap'] and m['settle'] == 'USDT' and m['active']]
        base_to_symbol = {m['base']: m['symbol'] for m in futures_markets}
        kucoin_bases = set(base_to_symbol.keys())
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 30, "page": 1, "price_change_percentage": "1h"}
        response = requests.get(url, params=params, headers=prepare_headers(), timeout=10)
        response.raise_for_status()
        coins = response.json()
        coingecko_to_kucoin = {'BTC': 'XBT'}
        available_coins = [coin for coin in coins if coingecko_to_kucoin.get(coin['symbol'].upper(), coin['symbol'].upper()) in kucoin_bases]
        sorted_coins = sorted(available_coins, key=lambda x: x.get('price_change_percentage_1h_in_currency', 0), reverse=True)
        top_5_symbols = [base_to_symbol[coingecko_to_kucoin.get(coin['symbol'].upper(), coin['symbol'].upper())] for coin in sorted_coins[:5]]
        logging.info(f"Top 5 pumping coins: {top_5_symbols}")
        return top_5_symbols
    except Exception as e:
        logging.error(f"Error selecting top coins: {e}")
        return ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'XRP/USDT:USDT', 'BNB/USDT:USDT', 'SOL/USDT:USDT']

def transform_symbol_for_ta(symbol):
    """Transform symbol for TradingView TA."""
    return symbol.split(':')[0].replace('XBT', 'BTC').replace('/', '')

def get_base_currency(symbol):
    """Extract base currency from symbol."""
    return symbol.split('/')[0].replace('XBT', 'BTC')

def get_current_price(exchange, symbol):
    """Fetch current price with retry."""
    for attempt in range(3):
        try:
            ticker = exchange.fetch_ticker(symbol)
            return float(ticker['last'])
        except ccxt.NetworkError as e:
            logging.warning(f"Network error fetching price for {symbol}: {e}. Retrying...")
            time.sleep(2 ** attempt)
    logging.error(f"Failed to fetch price for {symbol} after 3 attempts.")
    return None

def get_klines(exchange, symbol, interval='1m', limit=100):
    """Fetch OHLCV data with retry."""
    for attempt in range(3):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=interval, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df.astype(float)
        except ccxt.NetworkError as e:
            logging.warning(f"Network error fetching klines for {symbol}: {e}. Retrying...")
            time.sleep(2 ** attempt)
    logging.error(f"Failed to fetch klines for {symbol} after 3 attempts.")
    return None

def calculate_indicators(df):
    """Calculate ATR and RSI."""
    try:
        atr = AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range().iloc[-1]
        rsi = RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        return {"atr": float(atr), "rsi": float(rsi)}
    except Exception as e:
        logging.error(f"Error calculating indicators: {e}")
        return None

def get_market_condition(symbol):
    """Determine market condition using TradingView TA."""
    intervals = [Interval.INTERVAL_1_MINUTE, Interval.INTERVAL_5_MINUTES]
    recommendations = []
    symbol_ta = transform_symbol_for_ta(symbol)
    for interval in intervals:
        handler = TA_Handler(symbol=symbol_ta, exchange="KUCOIN", screener="crypto", interval=interval)
        try:
            analysis = handler.get_analysis()
            recommendations.append(analysis.summary["RECOMMENDATION"].lower())
        except Exception as e:
            logging.error(f"Error fetching TA for {symbol_ta}: {e}")
            recommendations.append("unknown")
    if recommendations.count("buy") == len(intervals):
        return "bullish"
    elif recommendations.count("sell") == len(intervals):
        return "bearish"
    elif recommendations.count("neutral") == len(intervals):
        return "sideways"
    return "mixed"

def get_news_sentiment(symbol):
    """Analyze news sentiment using CryptoPanic API."""
    api_key = "3ae2bb01594b35468c10b3c1da4ddc2012661b36"
    base_currency = get_base_currency(symbol)
    url = f"https://cryptopanic.com/api/v1/posts/?auth_token={api_key}&currencies={base_currency}&kind=news"
    try:
        response = requests.get(url, headers=prepare_headers(), timeout=10)
        response.raise_for_status()
        posts = response.json().get("results", [])
        if not posts:
            return "neutral"
        sentiment_score = sum(
            analyzer.polarity_scores(post["title"])["compound"] +
            (post.get("votes", {}).get("positive", 0) - post.get("votes", {}).get("negative", 0)) * 0.1
            for post in posts[:5]
        )
        return "positive" if sentiment_score > 0.5 else "negative" if sentiment_score < -0.5 else "neutral"
    except Exception as e:
        logging.error(f"Error fetching news sentiment for {symbol}: {e}")
        return "neutral"
