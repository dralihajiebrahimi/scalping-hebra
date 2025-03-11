import os
import logging
import time
import threading
import websocket
import json
from flask import Flask, render_template, jsonify
from kucoin import Client
from kucoin import KucoinAPIException
import mysql.connector
import pandas as pd
import requests
from ta.volatility import AverageTrueRange
from ta.momentum import RSIIndicator
from tradingview_ta import TA_Handler, Interval
from random_user_agent.user_agent import UserAgent
from random_user_agent.params import SoftwareName, OperatingSystem
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Configuration and Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# Load environment variables (replace with your actual credentials)
API_KEY = os.getenv("KUCOIN_API_KEY", "66c6f4f5c50d1f000104648f")
API_SECRET = os.getenv("KUCOIN_API_SECRET", "06b11a68-0745-4512-8a49-b1eb22ef6151")
API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "8529118$Ahe")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1:3306")
DB_NAME = os.getenv('DB_NAME', 'kucoin_trading')

# Initialize Sentiment Analyzer
analyzer = SentimentIntensityAnalyzer()

### Initialization Functions

def init_kucoin_client():
    """Initialize the KuCoin client with API credentials."""
    try:
        client = Client(API_KEY, API_SECRET, API_PASSPHRASE)
        logging.info("KuCoin client initialized successfully.")
        return client
    except KucoinAPIException as e:
        logging.error(f"Error initializing KuCoin client: {e}")
        return None

def init_db_connection():
    """Initialize the MySQL database connection."""
    try:
        conn = mysql.connector.connect(
            user=DB_USER, password=DB_PASSWORD, host=DB_HOST, database=DB_NAME
        )
        logging.info("Database connection established.")
        return conn
    except mysql.connector.Error as err:
        logging.error(f"Error connecting to database: {err}")
        return None

### Utility Functions

def prepare_headers():
    """Prepare HTTP headers with a random User-Agent."""
    software_names = [SoftwareName.CHROME.value, SoftwareName.FIREFOX.value]
    operating_systems = [OperatingSystem.WINDOWS.value, OperatingSystem.LINUX.value]
    user_agent_rotator = UserAgent(software_names=software_names, operating_systems=operating_systems, limit=100)
    return {"User-Agent": user_agent_rotator.get_random_user_agent()}

# Global order book storage
order_book = {'bids': {}, 'asks': {}}
order_book_lock = threading.Lock()  # For thread safety

# WebSocket handlers
def on_message(ws, message):
    data = json.loads(message)
    if 'topic' in data and data['topic'] == '/market/level2:BTC-USDT':
        process_order_book(data['data'])

def on_error(ws, error):
    logging.error(f"WebSocket error: {error}")

def on_close(ws):
    logging.info("WebSocket connection closed")

def on_open(ws):
    logging.info("WebSocket connection opened")
    ws.send(json.dumps({
        "type": "subscribe",
        "topic": "/market/level2:BTC-USDT"  # Example: BTC-USDT pair
    }))

# Process order book updates
def process_order_book(data):
    global order_book
    with order_book_lock:
        if data['type'] == 'snapshot':
            order_book['bids'] = {float(price): float(size) for price, size in data['bids']}
            order_book['asks'] = {float(price): float(size) for price, size in data['asks']}
        elif data['type'] == 'update':
            for side in ['bids', 'asks']:
                if side in data:
                    for price, size in data[side]:
                        price = float(price)
                        size = float(size)
                        if size == 0:
                            order_book[side].pop(price, None)
                        else:
                            order_book[side][price] = size

# Calculate order book metrics
def calculate_spread():
    with order_book_lock:
        if not order_book['bids'] or not order_book['asks']:
            return None
        return min(order_book['asks'].keys()) - max(order_book['bids'].keys())

def calculate_imbalance():
    with order_book_lock:
        total_bids = sum(order_book['bids'].values())
        total_asks = sum(order_book['asks'].values())
        total = total_bids + total_asks
        if total == 0:
            return 0
        return (total_bids - total_asks) / total
    

def get_current_price(client, symbol):
    """Fetch the current price of a symbol from KuCoin."""
    try:
        ticker = client.get_ticker(symbol)
        return float(ticker['price'])
    except KucoinAPIException as e:
        logging.error(f"Error getting price for {symbol}: {e}")
        return None

def get_klines(client, symbol, interval='1min', limit=100):
    """Fetch historical klines data for technical analysis."""
    try:
        klines = client.get_kline_data(symbol, interval, limit=limit)
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'close', 'high', 'low', 'volume', 'turnover'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
        df = df.astype(float)
        return df
    except KucoinAPIException as e:
        logging.error(f"Error fetching klines for {symbol}: {e}")
        return None

### Indicator Calculation Functions

def calculate_indicators(df):
    """Calculate ATR and RSI indicators from klines data."""
    atr = AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range().iloc[-1]
    rsi = RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
    return {"atr": atr, "rsi": rsi}

### Market Condition and Sentiment Analysis Functions

def get_market_condition(symbol="BTC-USDT"):
    """Determine market condition using TradingView TA across multiple time frames."""
    intervals = [Interval.INTERVAL_1_MINUTE, Interval.INTERVAL_5_MINUTES]
    recommendations = []
    for interval in intervals:
        handler = TA_Handler(symbol=symbol, exchange="KUCOIN", screener="crypto", interval=interval)
        try:
            analysis = handler.get_analysis()
            recommendation = analysis.summary["RECOMMENDATION"].lower()
            recommendations.append(recommendation)
        except Exception as e:
            logging.error(f"Error fetching market condition for {symbol} at {interval}: {e}")
            recommendations.append("unknown")
    
    # Confirm trend if both intervals align
    if "buy" in recommendations[0] and "buy" in recommendations[1]:
        return "bullish"
    elif "sell" in recommendations[0] and "sell" in recommendations[1]:
        return "bearish"
    elif "neutral" in recommendations[0] and "neutral" in recommendations[1]:
        return "sideways"
    else:
        return "mixed"

def get_news_sentiment(symbol):
    """Analyze news sentiment using CryptoPanic API and vaderSentiment."""
    api_key = "3ae2bb01594b35468c10b3c1da4ddc2012661b36"
    url = f"https://cryptopanic.com/api/v1/posts/?auth_token={api_key}&currencies={symbol.split('-')[0]}&kind=news"
    try:
        response = requests.get(url, headers=prepare_headers(), timeout=10)
        data = response.json()
        posts = data.get("results", [])
        if not posts:
            return "neutral"
        
        sentiment_score = 0
        for post in posts[:5]:  # Analyze last 5 news items
            title_sentiment = analyzer.polarity_scores(post["title"])["compound"]
            sentiment_score += title_sentiment
            votes = post.get("votes", {})
            sentiment_score += (votes.get("positive", 0) - votes.get("negative", 0)) * 0.1  # Weighted vote impact
        
        if sentiment_score > 0.5:
            return "positive"
        elif sentiment_score < -0.5:
            return "negative"
        else:
            return "neutral"
    except Exception as e:
        logging.error(f"Error fetching news sentiment for {symbol}: {e}")
        return "neutral"

### Coin Selection Functions

def get_coingecko_coins(retries=3):
    """Fetch coin data from CoinGecko with retry mechanism."""
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 50, "page": 1}
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=prepare_headers(), params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching CoinGecko data (attempt {attempt+1}/{retries}): {e}")
            time.sleep(5)
    return []

def get_kucoin_symbols(client):
    """Fetch available trading symbols from KuCoin."""
    try:
        symbols = client.get_symbols()
        return {s['symbol'] for s in symbols}
    except KucoinAPIException as e:
        logging.error(f"Error fetching KuCoin symbols: {e}")
        return set()

def select_top_5_for_trading(client):
    """Select top 5 coins based on 24-hour change, liquidity, and market cap."""
    coins = get_coingecko_coins()
    kucoin_symbols = get_kucoin_symbols(client)
    matching_coins = [
        coin for coin in coins 
        if f"{coin['symbol'].upper()}-USDT" in kucoin_symbols and coin['price_change_percentage_24h'] > 0
    ]
    
    if not matching_coins:
        logging.warning("No coins with positive 24-hour change found.")
        return []
    
    # Filter for liquidity (volume > $1M)
    matching_coins = [coin for coin in matching_coins if coin['total_volume'] > 1000000]
    if not matching_coins:
        logging.warning("No liquid coins with positive 24-hour change found.")
        return []
    
    # Normalized scoring
    max_market_cap = max(coin['market_cap'] for coin in matching_coins)
    max_volume = max(coin['total_volume'] for coin in matching_coins)
    for coin in matching_coins:
        mc_score = coin['market_cap'] / max_market_cap if max_market_cap else 0
        vol_score = coin['total_volume'] / max_volume if max_volume else 0
        coin['score'] = 0.6 * mc_score + 0.4 * vol_score
    
    top_5 = sorted(matching_coins, key=lambda x: x['score'], reverse=True)[:5]
    logging.info("Top 5 coins selected for trading:")
    for coin in top_5:
        logging.info(f"{coin['symbol'].upper()}-USDT: Score={coin['score']:.4f}")
    return [f"{coin['symbol'].upper()}-USDT" for coin in top_5]

### Trade Execution and Monitoring Functions

def log_trade(conn, strategy, symbol, trade_type, entry_price, quantity, status="open", exit_price=None, profit_loss=None):
    """Log a trade into the database."""
    try:
        cursor = conn.cursor()
        query = """
            INSERT INTO trades (
                strategy, symbol, trade_type, entry_price, quantity, status, exit_price, profit_loss, timestamp
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (strategy, symbol, trade_type, entry_price, quantity, status, exit_price, profit_loss))
        conn.commit()
        trade_id = cursor.lastrowid
        logging.info(f"Trade logged: ID={trade_id}, Symbol={symbol}, Strategy={strategy}")
        return trade_id
    except mysql.connector.Error as err:
        logging.error(f"Error logging trade: {err}")
        return None

def log_order(conn, order_id, trade_id, order_type, price, quantity, status="open"):
    """Log an order into the database."""
    try:
        cursor = conn.cursor()
        query = """
            INSERT INTO orders (
                order_id, trade_id, order_type, price, quantity, status, timestamp
            ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (order_id, trade_id, order_type, price, quantity, status))
        conn.commit()
        logging.info(f"Order logged: OrderID={order_id}, TradeID={trade_id}")
    except mysql.connector.Error as err:
        logging.error(f"Error logging order: {err}")

def update_trade(conn, trade_id, status, exit_price=None, profit_loss=None):
    """Update trade status in the database."""
    try:
        cursor = conn.cursor()
        query = """
            UPDATE trades 
            SET status = %s, exit_price = %s, profit_loss = %s, timestamp = NOW()
            WHERE trade_id = %s
        """
        cursor.execute(query, (status, exit_price, profit_loss, trade_id))
        conn.commit()
        logging.info(f"Trade {trade_id} updated: Status={status}, ExitPrice={exit_price}, P/L={profit_loss}")
    except mysql.connector.Error as err:
        logging.error(f"Error updating trade: {err}")

### Strategy Functions

def bullish_scalping(client, conn, symbol, investment_amount=10.0):
    """Execute a bullish scalping strategy with dynamic parameters."""
    df = get_klines(client, symbol)
    if df is None:
        return
    indicators = calculate_indicators(df)
    price = get_current_price(client, symbol)
    if not price:
        return
    quantity = investment_amount / price
    try:
        buy_order = client.create_market_order(symbol=symbol, side='buy', size=str(quantity))
        trade_id = log_trade(conn, "bullish_scalping", symbol, "buy", price, quantity)
        log_order(conn, buy_order['orderId'], trade_id, "market", price, quantity)
        
        # Dynamic take-profit and stop-loss based on ATR and sentiment
        sentiment = get_news_sentiment(symbol)
        take_profit_multiplier = 3 if sentiment == "positive" else 2
        target_price = price + (indicators['atr'] * take_profit_multiplier)
        stop_price = price - (indicators['atr'] * 1.5)
        
        sell_order = client.create_limit_order(symbol=symbol, side='sell', price=str(target_price), size=str(quantity))
        log_order(conn, sell_order['orderId'], trade_id, "limit", target_price, quantity)
        
        # Simplified monitoring (replace with WebSocket for real-time updates)
        time.sleep(10)
        current_price = get_current_price(client, symbol)
        if current_price >= target_price:
            profit_loss = (target_price - price) * quantity
            update_trade(conn, trade_id, "closed", target_price, profit_loss)
        elif current_price <= stop_price:
            profit_loss = (stop_price - price) * quantity
            update_trade(conn, trade_id, "stopped", stop_price, profit_loss)
    except KucoinAPIException as e:
        logging.error(f"Error in bullish scalping for {symbol}: {e}")

def bearish_short_scalping(client, conn, symbol, investment_amount=10.0):
    """Execute a bearish short scalping strategy with dynamic parameters."""
    df = get_klines(client, symbol)
    if df is None:
        return
    indicators = calculate_indicators(df)
    price = get_current_price(client, symbol)
    if not price:
        return
    quantity = investment_amount / price
    try:
        sell_order = client.create_market_order(symbol=symbol, side='sell', size=str(quantity))
        trade_id = log_trade(conn, "bearish_short_scalping", symbol, "sell", price, quantity)
        log_order(conn, sell_order['orderId'], trade_id, "market", price, quantity)
        
        # Dynamic take-profit and stop-loss based on ATR and sentiment
        sentiment = get_news_sentiment(symbol)
        take_profit_multiplier = 2 if sentiment == "negative" else 1.5
        target_price = price - (indicators['atr'] * take_profit_multiplier)
        stop_price = price + (indicators['atr'] * 1.5)
        
        buy_order = client.create_limit_order(symbol=symbol, side='buy', price=str(target_price), size=str(quantity))
        log_order(conn, buy_order['orderId'], trade_id, "limit", target_price, quantity)
        
        # Simplified monitoring
        time.sleep(10)
        current_price = get_current_price(client, symbol)
        if current_price <= target_price:
            profit_loss = (price - target_price) * quantity
            update_trade(conn, trade_id, "closed", target_price, profit_loss)
        elif current_price >= stop_price:
            profit_loss = (price - stop_price) * quantity
            update_trade(conn, trade_id, "stopped", stop_price, profit_loss)
    except KucoinAPIException as e:
        logging.error(f"Error in bearish short scalping for {symbol}: {e}")

def mean_reversion_trading(client, conn, symbol, investment_amount=10.0):
    """Execute a mean reversion strategy with dynamic parameters."""
    df = get_klines(client, symbol)
    if df is None:
        return
    indicators = calculate_indicators(df)
    price = get_current_price(client, symbol)
    if not price:
        return
    quantity = investment_amount / price
    sentiment = get_news_sentiment(symbol)
    
    if indicators['rsi'] < 30 and sentiment != "negative":  # Oversold, avoid negative sentiment
        try:
            buy_order = client.create_market_order(symbol=symbol, side='buy', size=str(quantity))
            trade_id = log_trade(conn, "mean_reversion", symbol, "buy", price, quantity)
            log_order(conn, buy_order['orderId'], trade_id, "market", price, quantity)
            target_price = price + (indicators['atr'] * 2)
            stop_price = price - (indicators['atr'] * 1.5)
            sell_order = client.create_limit_order(symbol=symbol, side='sell', price=str(target_price), size=str(quantity))
            log_order(conn, sell_order['orderId'], trade_id, "limit", target_price, quantity)
            
            time.sleep(10)
            current_price = get_current_price(client, symbol)
            if current_price >= target_price:
                profit_loss = (target_price - price) * quantity
                update_trade(conn, trade_id, "closed", target_price, profit_loss)
            elif current_price <= stop_price:
                profit_loss = (stop_price - price) * quantity
                update_trade(conn, trade_id, "stopped", stop_price, profit_loss)
        except KucoinAPIException as e:
            logging.error(f"Error in mean reversion buy for {symbol}: {e}")
    elif indicators['rsi'] > 70 and sentiment != "positive":  # Overbought, avoid positive sentiment
        try:
            sell_order = client.create_market_order(symbol=symbol, side='sell', size=str(quantity))
            trade_id = log_trade(conn, "mean_reversion", symbol, "sell", price, quantity)
            log_order(conn, sell_order['orderId'], trade_id, "market", price, quantity)
            target_price = price - (indicators['atr'] * 2)
            stop_price = price + (indicators['atr'] * 1.5)
            buy_order = client.create_limit_order(symbol=symbol, side='buy', price=str(target_price), size=str(quantity))
            log_order(conn, buy_order['orderId'], trade_id, "limit", target_price, quantity)
            
            time.sleep(10)
            current_price = get_current_price(client, symbol)
            if current_price <= target_price:
                profit_loss = (price - target_price) * quantity
                update_trade(conn, trade_id, "closed", target_price, profit_loss)
            elif current_price >= stop_price:
                profit_loss = (price - stop_price) * quantity
                update_trade(conn, trade_id, "stopped", stop_price, profit_loss)
        except KucoinAPIException as e:
            logging.error(f"Error in mean reversion sell for {symbol}: {e}")

def execute_trading_strategy(client, conn, symbol, market_condition, investment_amount=10.0):
    """Execute the appropriate trading strategy based on market condition."""
    # Example trading strategy with order book analysis
    spread = calculate_spread()
    imbalance = calculate_imbalance()
    current_price = get_current_price(client, symbol)

    # Check liquidity via spread
    if spread is None or spread > 0.005 * current_price:  # 0.5% threshold
        logging.info(f"Spread too wide for {symbol} ({spread:.2f}). Skipping trade.")
        return

    # Use order imbalance as a signal
    if imbalance > 0.2:  # Strong buy pressure
        logging.info(f"Detected buy imbalance ({imbalance:.2f}). Executing long trade.")
        client.create_market_order(symbol, Client.SIDE_BUY, funds=investment_amount)
    elif imbalance < -0.2:  # Strong sell pressure
        logging.info(f"Detected sell imbalance ({imbalance:.2f}). Executing short trade.")
        # Add short logic if applicable (e.g., futures trading)
    else:
        logging.info(f"No significant imbalance ({imbalance:.2f}). Holding position.")

    if market_condition == "bullish":
        bullish_scalping(client, conn, symbol, investment_amount)
    elif market_condition == "bearish":
        bearish_short_scalping(client, conn, symbol, investment_amount)
    elif market_condition == "sideways":
        mean_reversion_trading(client, conn, symbol, investment_amount)
    else:
        logging.info(f"Unknown or mixed market condition for {symbol}. Skipping.")

def maintain_active_trading(client, conn):
    """Continuously trade on the top 5 selected coins."""
    while True:
        top_5_symbols = select_top_5_for_trading(client)
        market_condition = get_market_condition("BTC-USDT")
        logging.info(f"Current market condition: {market_condition}")
        for symbol in top_5_symbols:
            execute_trading_strategy(client, conn, symbol, market_condition)
        time.sleep(30)

### Flask Routes

@app.route('/')
def index():
    """Render the index page."""
    return render_template('index.html')

@app.route('/api/trade_data', methods=['GET'])
def get_trade_data():
    """Fetch real-time trade data for the dashboard."""
    conn = init_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT trade_id, strategy, symbol, trade_type, entry_price, quantity, status 
            FROM trades 
            WHERE status = 'open'
        """)
        open_trades = cursor.fetchall()
        
        cursor.execute("""
            SELECT trade_id, strategy, symbol, trade_type, entry_price, exit_price, quantity, profit_loss, status 
            FROM trades 
            WHERE status IN ('closed', 'stopped')
        """)
        closed_trades = cursor.fetchall()
        
        cursor.execute("""
            SELECT SUM(profit_loss) 
            FROM trades 
            WHERE DATE(timestamp) = CURRENT_DATE AND status IN ('closed', 'stopped')
        """)
        daily_pnl = cursor.fetchone()[0] or 0.0
        
        cursor.execute("""
            SELECT COUNT(*) 
            FROM trades 
            WHERE status IN ('closed', 'stopped')
        """)
        total_trades = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) 
            FROM trades 
            WHERE status IN ('closed', 'stopped') AND profit_loss > 0
        """)
        winning_trades = cursor.fetchone()[0]
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        cursor.execute("""
            SELECT AVG(profit_loss) 
            FROM trades 
            WHERE status IN ('closed', 'stopped')
        """)
        average_pnl = cursor.fetchone()[0] or 0.0
    except mysql.connector.Error as err:
        logging.error(f"Error fetching trade data: {err}")
        return jsonify({"error": "Error fetching trade data"}), 500
    finally:
        conn.close()

    open_trades_json = [
        {"trade_id": t[0], "strategy": t[1], "symbol": t[2], "trade_type": t[3], 
         "entry_price": t[4], "quantity": t[5], "status": t[6]} 
        for t in open_trades
    ]
    closed_trades_json = [
        {"trade_id": t[0], "strategy": t[1], "symbol": t[2], "trade_type": t[3], 
         "entry_price": t[4], "exit_price": t[5], "quantity": t[6], "profit_loss": t[7], "status": t[8]} 
        for t in closed_trades
    ]
    
    return jsonify({
        "open_trades": open_trades_json,
        "closed_trades": closed_trades_json,
        "daily_pnl": daily_pnl,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "average_pnl": average_pnl
    })

# Start WebSocket in a separate thread
def start_websocket():
    """Run the WebSocket connection in a separate thread."""
    ws = websocket.WebSocketApp("wss://push.kucoin.com/endpoint",
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    ws.on_open = on_open
    ws.run_forever()

### Main Function

def main():
    """Main entry point to start the trading bot and Flask app."""
    client = init_kucoin_client()
    conn = init_db_connection()
    if not client or not conn:
        logging.error("Failed to initialize client or database. Exiting.")
        return
    # Start WebSocket thread
    websocket_thread = threading.Thread(target=start_websocket)
    websocket_thread.daemon = True  # Stops with main thread
    websocket_thread.start()

    # Wait for initial order book data
    time.sleep(5)

    trading_thread = threading.Thread(target=maintain_active_trading, args=(client, conn))
    trading_thread.start()

    app.run(debug=True, host="0.0.0.0")

if __name__ == '__main__':
    main()