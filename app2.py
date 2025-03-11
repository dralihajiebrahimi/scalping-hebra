import os
import logging
import time
import threading
import websocket
import json
import requests
import random
from flask import Flask, render_template, jsonify
import ccxt
import mysql.connector
import pandas as pd
from ta.volatility import AverageTrueRange
from ta.momentum import RSIIndicator
from tradingview_ta import TA_Handler, Interval
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from random_user_agent.user_agent import UserAgent
from random_user_agent.params import SoftwareName, OperatingSystem
import ssl

# Configuration and Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# Environment Variables
API_KEY = os.getenv("KUCOIN_API_KEY", "67c9d31345e41a0001676a6f")
API_SECRET = os.getenv("KUCOIN_API_SECRET", "7b93d018-c606-40db-b8d0-b4bee166eef1")
API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "8529118#Ahe")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv('DB_NAME', 'kucoin_trading')

# Global Variables for WebSocket
order_books = {}
order_book_lock = threading.Lock()
subscribed_symbols = set()
ws = None

# Sentiment Analyzer
analyzer = SentimentIntensityAnalyzer()

### WebSocket Functions
def get_websocket_token():
    """Fetch a public WebSocket token and endpoint from KuCoin Futures."""
    url = "https://api-futures.kucoin.com/api/v1/bullet-public"
    try:
        response = requests.post(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data['code'] == "200000":
            token = data['data']['token']
            instance = data['data']['instanceServers'][0]
            endpoint = instance['endpoint']
            ping_interval = instance['pingInterval'] // 1000
            ping_timeout = instance['pingTimeout'] // 1000
            logging.info(f"WebSocket token fetched: {token[:10]}..., endpoint: {endpoint}")
            return token, endpoint, ping_interval, ping_timeout
        else:
            logging.error(f"Failed to fetch WebSocket token: {data}")
            return None, None, None, None
    except requests.RequestException as e:
        logging.error(f"Error fetching WebSocket token: {e}")
        return None, None, None, None

def on_message(ws, message):
    """Handle incoming WebSocket messages."""
    try:
        data = json.loads(message)
        if data.get('type') == 'welcome':
            logging.info("WebSocket connection established successfully.")
        elif 'topic' in data and data['topic'].startswith('/contractMarket/level2:'):
            symbol = data['topic'].split(':')[1]
            process_order_book(symbol, data['data'])
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding WebSocket message: {e}")
    except Exception as e:
        logging.error(f"Unexpected error in on_message: {e}")

def on_open(ws):
    logging.info("WebSocket connection opened")

def on_error(ws, error):
    logging.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logging.info("WebSocket closed")

def process_order_book(symbol, data):
    """Process order book updates from WebSocket."""
    with order_book_lock:
        if symbol not in order_books:
            order_books[symbol] = {'bids': {}, 'asks': {}}
        try:
            if 'change' in data:
                parts = data['change'].split(',')
                if len(parts) >= 2:
                    price = float(parts[0])
                    size = float(parts[1])
                    side = 'bids' if price < 0 else 'asks'
                    actual_price = abs(price)
                    if size == 0:
                        order_books[symbol][side].pop(actual_price, None)
                    else:
                        order_books[symbol][side][actual_price] = size
                    logging.debug(f"Order book updated for {symbol}: {side} @ {actual_price} = {size}")
            elif 'asks' in data and 'bids' in data:
                order_books[symbol]['asks'] = {float(p): float(s) for p, s in data['asks']}
                order_books[symbol]['bids'] = {float(p): float(s) for p, s in data['bids']}
                logging.info(f"Order book snapshot received for {symbol}")
            if order_books[symbol]['bids'] and order_books[symbol]['asks']:
                spread = calculate_spread(symbol)
                imbalance = calculate_imbalance(symbol)
                logging.info(f"{symbol}: Spread = {spread}, Imbalance = {imbalance}")
        except Exception as e:
            logging.error(f"Error processing order book for {symbol}: {e}")

def calculate_spread(symbol):
    """Calculate the bid-ask spread."""
    with order_book_lock:
        book = order_books.get(symbol, {})
        if not book['bids'] or not book['asks']:
            return float('inf')
        return min(book['asks'].keys()) - max(book['bids'].keys())

def calculate_imbalance(symbol):
    """Calculate order book imbalance."""
    with order_book_lock:
        book = order_books.get(symbol, {})
        total_bids = sum(book.get('bids', {}).values())
        total_asks = sum(book.get('asks', {}).values())
        total_volume = total_bids + total_asks
        return (total_bids - total_asks) / total_volume if total_volume > 0 else 0

### Exchange and Database Initialization
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
        logging.info("KuCoin Futures exchange initialized.")
        return exchange
    except Exception as e:
        logging.error(f"Error initializing KuCoin Futures: {e}")
        return None

def init_db_connection():
    """Establish MySQL connection."""
    try:
        conn = mysql.connector.connect(
            user=DB_USER, password=DB_PASSWORD, host=DB_HOST, database=DB_NAME,
            pool_name="trading_pool", pool_size=10, pool_reset_session=True, connection_timeout=30
        )
        if conn.is_connected():
            logging.info("Database connection established.")
            return conn
        raise mysql.connector.Error("Failed to connect to database")
    except mysql.connector.Error as e:
        logging.error(f"Database connection error: {e}")
        return None

### Utility Functions
def prepare_headers():
    """Prepare HTTP headers with random User-Agent."""
    user_agent_rotator = UserAgent(
        software_names=[SoftwareName.CHROME.value, SoftwareName.FIREFOX.value],
        operating_systems=[OperatingSystem.WINDOWS.value, OperatingSystem.LINUX.value],
        limit=100
    )
    return {"User-Agent": user_agent_rotator.get_random_user_agent()}

def select_top_5_pumping_coins(exchange):
    """Select top 5 coins with highest 1-hour price change."""
    try:
        markets = exchange.load_markets()
        futures_markets = [m for m in markets.values() if m['swap'] and m['settle'] == 'USDT' and m['active']]
        base_to_symbol = {market['base']: market['symbol'] for market in futures_markets}
        kucoin_bases = set(base_to_symbol.keys())

        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 10, "page": 1, "price_change_percentage": "1h"}
        response = requests.get(url, params=params, headers=prepare_headers(), timeout=10)
        response.raise_for_status()
        coins = response.json()

        available_coins = [coin for coin in coins if coin['symbol'].upper() in kucoin_bases]
        sorted_coins = sorted(available_coins, key=lambda x: x.get('price_change_percentage_1h', 0), reverse=True)
        top_5_symbols = [base_to_symbol[coin['symbol'].upper()] for coin in sorted_coins[:5]]
        logging.info(f"Top 5 pumping coins: {top_5_symbols}")
        return top_5_symbols
    except Exception as e:
        logging.error(f"Error selecting top 5 coins: {e}")
        return ['XBTUSDTM', 'ETHUSDTM', 'XRPUSDTM', 'ADAUSDTM', 'SOLUSDTM']

def get_current_price(exchange, symbol):
    """Fetch current price."""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['last'])
    except Exception as e:
        logging.error(f"Error fetching price for {symbol}: {e}")
        return None

def get_klines(exchange, symbol, interval='1m', limit=100):
    """Fetch OHLCV data."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=interval, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df.astype(float)
    except Exception as e:
        logging.error(f"Error fetching klines for {symbol}: {e}")
        return None

def calculate_indicators(df):
    """Calculate ATR and RSI."""
    try:
        atr = AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range().iloc[-1]
        rsi = RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        return {"atr": float(atr), "rsi": float(rsi)}
    except Exception as e:
        logging.error(f"Error calculating indicators: {e}")
        return {"atr": None, "rsi": None}

def get_market_condition(symbol="BTC/USDT:USDT"):
    """Determine market condition using TradingView TA."""
    intervals = [Interval.INTERVAL_1_MINUTE, Interval.INTERVAL_5_MINUTES]
    recommendations = []
    symbol = symbol.split('/')[0].replace('XBT', 'BTC') + "USDT"
    for interval in intervals:
        handler = TA_Handler(symbol=symbol, exchange="KUCOIN", screener="crypto", interval=interval)
        try:
            analysis = handler.get_analysis()
            recommendations.append(analysis.summary["RECOMMENDATION"].lower())
        except Exception as e:
            logging.error(f"Error fetching TA for {symbol}: {e}")
            recommendations.append("unknown")
    if "buy" in recommendations and recommendations.count("buy") == len(intervals):
        return "bullish"
    elif "sell" in recommendations and recommendations.count("sell") == len(intervals):
        return "bearish"
    elif "neutral" in recommendations and recommendations.count("neutral") == len(intervals):
        return "sideways"
    return "mixed"

def get_news_sentiment(symbol):
    """Analyze news sentiment using CryptoPanic API."""
    api_key = "3ae2bb01594b35468c10b3c1da4ddc2012661b36"
    base_currency = symbol.split('/')[0].replace('XBT', 'BTC')
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

### Trade and Order Logging
def log_trade(conn, strategy, symbol, trade_type, entry_price, quantity, multiplier, status="open", exit_price=None, profit_loss=None):
    """Log a trade to the database."""
    try:
        if not conn or not conn.is_connected():
            conn = init_db_connection()
        with conn.cursor() as cursor:
            query = """
                INSERT INTO trades (strategy, symbol, trade_type, entry_price, quantity, multiplier, status, exit_price, profit_loss, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """
            cursor.execute(query, (strategy, symbol, trade_type, entry_price, quantity, multiplier, status, exit_price, profit_loss))
            conn.commit()
            trade_id = cursor.lastrowid
            logging.info(f"Trade logged: ID={trade_id}, Symbol={symbol}")
            return trade_id
    except mysql.connector.Error as e:
        logging.error(f"Error logging trade: {e}")
        return None

def log_order(conn, order_id, trade_id, order_type, price, quantity, status="open"):
    """Log an order to the database."""
    try:
        with conn.cursor() as cursor:
            query = """
                INSERT INTO orders (order_id, trade_id, order_type, price, quantity, status, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """
            cursor.execute(query, (order_id, trade_id, order_type, price, quantity, status))
            conn.commit()
            logging.info(f"Order logged: OrderID={order_id}, TradeID={trade_id}")
    except mysql.connector.Error as e:
        logging.error(f"Error logging order: {e}")

def update_trade(conn, trade_id, status, exit_price=None):
    """Update trade status and calculate profit/loss."""
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT trade_type, entry_price, quantity, multiplier FROM trades WHERE trade_id = %s", (trade_id,))
            trade = cursor.fetchone()
            if not trade:
                logging.error(f"Trade {trade_id} not found.")
                return
            trade_type, entry_price, quantity, multiplier = trade
            profit_loss = ((exit_price - entry_price) if trade_type == 'buy' else (entry_price - exit_price)) * quantity * multiplier if exit_price else None
            query = """
                UPDATE trades SET status = %s, exit_price = %s, profit_loss = %s, timestamp = NOW()
                WHERE trade_id = %s
            """
            cursor.execute(query, (status, exit_price, profit_loss, trade_id))
            conn.commit()
            logging.info(f"Trade {trade_id} updated: Status={status}, P/L={profit_loss}")
    except mysql.connector.Error as e:
        logging.error(f"Error updating trade: {e}")

### Trading Strategies
def bullish_scalping(exchange, conn, symbol, investment_amount=10.0, leverage=10):
    """Execute bullish scalping trade with stop loss and take profit."""
    df = get_klines(exchange, symbol)
    if df is None or df.empty:
        return
    indicators = calculate_indicators(df)
    if not all(indicators.values()):
        return
    price = get_current_price(exchange, symbol)
    if not price:
        return
    try:
        market = exchange.market(symbol)
        multiplier = float(market['contractSize'])
        size = int((investment_amount * leverage) / (multiplier * price))
        exchange.set_leverage(leverage, symbol)
        buy_order = exchange.create_market_buy_order(symbol, size)
        trade_id = log_trade(conn, "bullish_scalping", symbol, "buy", price, size, multiplier)
        if trade_id:
            log_order(conn, buy_order['id'], trade_id, "market", price, size)
            sentiment = get_news_sentiment(symbol)
            take_profit_multiplier = 3 if sentiment == "positive" else 2
            target_price = price + (indicators['atr'] * take_profit_multiplier)
            stop_price = price - (indicators['atr'] * 1.5)
            tp_order = exchange.create_limit_sell_order(symbol, size, target_price, {'reduceOnly': True})
            log_order(conn, tp_order['id'], trade_id, "limit", target_price, size)
            sl_order = exchange.create_order(symbol, 'market', 'sell', size, None,
                                             {'stop': 'loss', 'stopPrice': stop_price, 'reduceOnly': True})
            log_order(conn, sl_order['id'], trade_id, "stop_market", stop_price, size)
    except ccxt.BaseError as e:
        logging.error(f"Error in bullish scalping for {symbol}: {e}")

def bearish_short_scalping(exchange, conn, symbol, investment_amount=10.0, leverage=10):
    """Execute bearish short scalping trade with stop loss and take profit."""
    df = get_klines(exchange, symbol)
    if df is None or df.empty:
        return
    indicators = calculate_indicators(df)
    if not all(indicators.values()):
        return
    price = get_current_price(exchange, symbol)
    if not price:
        return
    try:
        market = exchange.market(symbol)
        multiplier = float(market['contractSize'])
        size = int((investment_amount * leverage) / (multiplier * price))
        exchange.set_leverage(leverage, symbol)
        sell_order = exchange.create_market_sell_order(symbol, size)
        trade_id = log_trade(conn, "bearish_short_scalping", symbol, "sell", price, size, multiplier)
        if trade_id:
            log_order(conn, sell_order['id'], trade_id, "market", price, size)
            sentiment = get_news_sentiment(symbol)
            take_profit_multiplier = 2 if sentiment == "negative" else 1.5
            target_price = price - (indicators['atr'] * take_profit_multiplier)
            stop_price = price + (indicators['atr'] * 1.5)
            tp_order = exchange.create_limit_buy_order(symbol, size, target_price, {'reduceOnly': True})
            log_order(conn, tp_order['id'], trade_id, "limit", target_price, size)
            sl_order = exchange.create_order(symbol, 'market', 'buy', size, None,
                                             {'stop': 'loss', 'stopPrice': stop_price, 'reduceOnly': True})
            log_order(conn, sl_order['id'], trade_id, "stop_market", stop_price, size)
    except ccxt.BaseError as e:
        logging.error(f"Error in bearish short scalping for {symbol}: {e}")

def mean_reversion_trading(exchange, conn, symbol, investment_amount=10.0, leverage=10):
    """Execute mean reversion trade with stop loss and take profit."""
    df = get_klines(exchange, symbol)
    if df is None or df.empty:
        return
    indicators = calculate_indicators(df)
    if not all(indicators.values()):
        return
    price = get_current_price(exchange, symbol)
    if not price:
        return
    market = exchange.market(symbol)
    multiplier = float(market['contractSize'])
    size = int((investment_amount * leverage) / (multiplier * price))
    sentiment = get_news_sentiment(symbol)
    try:
        exchange.set_leverage(leverage, symbol)
        if indicators['rsi'] < 30 and sentiment != "negative":
            buy_order = exchange.create_market_buy_order(symbol, size)
            trade_id = log_trade(conn, "mean_reversion", symbol, "buy", price, size, multiplier)
            if trade_id:
                log_order(conn, buy_order['id'], trade_id, "market", price, size)
                target_price = price + (indicators['atr'] * 2)
                stop_price = price - (indicators['atr'] * 1.5)
                tp_order = exchange.create_limit_sell_order(symbol, size, target_price, {'reduceOnly': True})
                log_order(conn, tp_order['id'], trade_id, "limit", target_price, size)
                sl_order = exchange.create_order(symbol, 'market', 'sell', size, None,
                                                 {'stop': 'loss', 'stopPrice': stop_price, 'reduceOnly': True})
                log_order(conn, sl_order['id'], trade_id, "stop_market", stop_price, size)
        elif indicators['rsi'] > 70 and sentiment != "positive":
            sell_order = exchange.create_market_sell_order(symbol, size)
            trade_id = log_trade(conn, "mean_reversion", symbol, "sell", price, size, multiplier)
            if trade_id:
                log_order(conn, sell_order['id'], trade_id, "market", price, size)
                target_price = price - (indicators['atr'] * 2)
                stop_price = price + (indicators['atr'] * 1.5)
                tp_order = exchange.create_limit_buy_order(symbol, size, target_price, {'reduceOnly': True})
                log_order(conn, tp_order['id'], trade_id, "limit", target_price, size)
                sl_order = exchange.create_order(symbol, 'market', 'buy', size, None,
                                                 {'stop': 'loss', 'stopPrice': stop_price, 'reduceOnly': True})
                log_order(conn, sl_order['id'], trade_id, "stop_market", stop_price, size)
    except ccxt.BaseError as e:
        logging.error(f"Error in mean reversion for {symbol}: {e}")

def execute_trading_strategy(exchange, conn, symbol, market_condition, investment_amount=10.0, leverage=10):
    """Execute trading strategy based on market conditions."""
    logging.info(f"Executing strategy for {symbol} with condition: {market_condition}")
    spread = calculate_spread(symbol)
    imbalance = calculate_imbalance(symbol)
    current_price = get_current_price(exchange, symbol)
    spread_str = f"{spread:.2f}" if spread is not None else "N/A"
    logging.info(f"Spread: {spread_str}, Imbalance: {imbalance:.2f}, Price: {current_price or 'N/A'}")
    if spread is None or (current_price and spread > 0.005 * current_price):
        logging.info(f"Spread too wide for {symbol} ({spread_str}). Skipping.")
        return
    if imbalance > 0.2:
        bullish_scalping(exchange, conn, symbol, investment_amount, leverage)
    elif imbalance < -0.2:
        bearish_short_scalping(exchange, conn, symbol, investment_amount, leverage)
    elif market_condition == "bullish":
        bullish_scalping(exchange, conn, symbol, investment_amount, leverage)
    elif market_condition == "bearish":
        bearish_short_scalping(exchange, conn, symbol, investment_amount, leverage)
    elif market_condition == "sideways":
        mean_reversion_trading(exchange, conn, symbol, investment_amount, leverage)
    else:
        logging.info(f"Unknown/mixed condition for {symbol}. Skipping.")

### Trading Loop
def maintain_active_trading(exchange, conn):
    """Run trading loop with top 5 pumping coins."""
    global ws
    logging.info("Starting trading loop...")
    while True:
        try:
            if ws is None or not ws.sock or not ws.sock.connected:
                logging.warning("WebSocket not initialized or disconnected. Waiting for connection...")
                time.sleep(5)
                continue

            top_5_symbols = select_top_5_pumping_coins(exchange)
            current_subscribed = set(subscribed_symbols)
           
            for symbol in top_5_symbols:
                if symbol not in current_subscribed:
                    ws.send(json.dumps({
                        "id": str(random.randint(1000, 9999)),
                        "type": "subscribe",
                        "topic": f"/contractMarket/level2:{symbol}",
                        "response": True
                    }))
                    subscribed_symbols.add(symbol)
                    logging.info(f"Subscribed to {symbol}")
           
            for symbol in current_subscribed - set(top_5_symbols):
                ws.send(json.dumps({
                    "id": str(random.randint(1000, 9999)),
                    "type": "unsubscribe",
                    "topic": f"/contractMarket/level2:{symbol}",
                    "response": True
                }))
                subscribed_symbols.remove(symbol)
                logging.info(f"Unsubscribed from {symbol}")
           
            for symbol in top_5_symbols:
                market_condition = get_market_condition(symbol)
                execute_trading_strategy(exchange, conn, symbol, market_condition)
            time.sleep(30)
        except Exception as e:
            logging.error(f"Trading loop error: {e}", exc_info=True)
            time.sleep(60)

### Flask Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/trade_data', methods=['GET'])
def get_trade_data():
    """Return trade data for API."""
    conn = init_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        with conn.cursor(buffered=True) as cursor:
            cursor.execute(
                "SELECT trade_id, strategy, symbol, trade_type, entry_price, quantity, status "
                "FROM trades WHERE status = 'open'"
            )
            open_trades = [
                {"trade_id": t[0], "strategy": t[1], "symbol": t[2], "trade_type": t[3],
                 "entry_price": float(t[4]), "quantity": float(t[5]), "status": t[6]}
                for t in cursor.fetchall()
            ]

            cursor.execute(
                "SELECT trade_id, strategy, symbol, trade_type, entry_price, exit_price, quantity, profit_loss, status "
                "FROM trades WHERE status IN ('closed', 'stopped')"
            )
            closed_trades = [
                {"trade_id": t[0], "strategy": t[1], "symbol": t[2], "trade_type": t[3],
                 "entry_price": float(t[4]), "exit_price": float(t[5]) if t[5] else None,
                 "quantity": float(t[6]), "profit_loss": float(t[7]) if t[7] else None, "status": t[8]}
                for t in cursor.fetchall()
            ]

            cursor.execute("SELECT SUM(profit_loss) FROM trades WHERE DATE(timestamp) = CURRENT_DATE AND status IN ('closed', 'stopped')")
            daily_pnl = float(cursor.fetchone()[0] or 0.0)

            cursor.execute("SELECT COUNT(*) FROM trades WHERE status IN ('closed', 'stopped')")
            total_trades = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM trades WHERE status IN ('closed', 'stopped') AND profit_loss > 0")
            wins = cursor.fetchone()[0]
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

            cursor.execute("SELECT AVG(profit_loss) FROM trades WHERE status IN ('closed', 'stopped')")
            average_pnl = float(cursor.fetchone()[0] or 0.0)

        return jsonify({
            "open_trades": open_trades,
            "closed_trades": closed_trades,
            "daily_pnl": daily_pnl,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "average_pnl": average_pnl
        })

    except mysql.connector.Error as e:
        logging.error(f"Error fetching trade data: {e}")
        return jsonify({"error": "Database error"}), 500
    finally:
        if conn:
            conn.close()

### WebSocket Initialization
def start_websocket():
    """Start WebSocket with SSL verification disabled."""
    global ws
    logging.info("Initializing WebSocket thread...")
    while True:
        token, endpoint, ping_interval, ping_timeout = get_websocket_token()
        if not token or not endpoint:
            logging.error("Failed to get WebSocket token/endpoint. Retrying in 10 seconds...")
            time.sleep(10)
            continue
        ws_url = f"{endpoint}?token={token}&connectId={random.randint(1, 10000)}"
        logging.info(f"Connecting to WebSocket: {ws_url}")
       
        ws = websocket.WebSocketApp(
            ws_url,
            on_message=on_message,
            on_open=on_open,
            on_error=on_error,
            on_close=on_close
        )
       
        ws.run_forever(
            ping_interval=ping_interval or 18,
            ping_timeout=ping_timeout or 10,
            sslopt={"cert_reqs": ssl.CERT_NONE}
        )
        logging.warning("WebSocket connection lost. Reconnecting in 5 seconds...")
        time.sleep(5)

### Main Function
def main():
    """Main function to start the bot."""
    exchange = init_kucoin_client()
    conn = init_db_connection()
    if not exchange or not conn:
        logging.error("Initialization failed. Exiting.")
        return

    websocket_thread = threading.Thread(target=start_websocket, daemon=True)
    websocket_thread.start()
   
    time.sleep(5)
   
    trading_thread1 = threading.Thread(target=maintain_active_trading, args=(exchange, conn), daemon=True)
    trading_thread2 = threading.Thread(target=maintain_active_trading, args=(exchange, conn), daemon=True)
   
    trading_thread1.start()
    trading_thread2.start()
   
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True, use_reloader=False)

if __name__ == '__main__':
    main()