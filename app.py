import os
import logging
import time
import threading
import websocket
import json
import requests
import random
from decimal import Decimal
from flask import Flask, jsonify, render_template
import ccxt
import mysql.connector
import subprocess
from trade import init_kucoin_client, select_top_5_pumping_coins, get_current_price, get_market_condition
from database import init_db_connection, log_trade, log_order, update_trade
from strategies import bullish_scalping, bearish_short_scalping, mean_reversion_trading

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# Global Variables
order_books = {}
order_book_lock = threading.Lock()
subscribed_symbols = set()
ws = None
id_to_symbol = {}
top_5_symbols = []

# WebSocket Functions
def get_websocket_token():
    """Fetch WebSocket token and endpoint."""
    url = "https://api-futures.kucoin.com/api/v1/bullet-public"
    for attempt in range(3):
        try:
            response = requests.post(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data['code'] == "200000":
                instance = data['data']['instanceServers'][0]
                return (data['data']['token'], instance['endpoint'], instance['pingInterval'] // 1000, instance['pingTimeout'] // 1000)
            logging.error(f"Failed to fetch WebSocket token: {data}")
            return None, None, None, None
        except requests.RequestException as e:
            logging.warning(f"Error fetching WebSocket token (attempt {attempt + 1}): {e}")
            time.sleep(2 ** attempt)
    return None, None, None, None

def on_message(ws, message):
    """Handle WebSocket messages."""
    try:
        data = json.loads(message)
        if data.get('type') == 'welcome':
            logging.info("WebSocket connection established.")
        elif data.get('type') == 'message' and 'topic' in data:
            symbol_id = data['topic'].split(':')[1]
            symbol = id_to_symbol.get(symbol_id)
            if symbol:
                logging.debug(f"Received update for {symbol}")
                process_order_book(symbol, data['data'])
            else:
                logging.warning(f"No mapping for symbol ID: {symbol_id}")
    except Exception as e:
        logging.error(f"Error in on_message: {e}")

def on_open(ws):
    """Handle WebSocket opening and resubscribe."""
    logging.info("WebSocket connected.")
    for symbol in top_5_symbols:
        symbol_id = exchange.markets[symbol]['id']
        ws.send(json.dumps({
            "id": str(random.randint(1000, 9999)),
            "type": "subscribe",
            "topic": f"/contractMarket/level2:{symbol_id}",
            "response": True
        }))
        logging.info(f"Resubscribed to {symbol} (ID: {symbol_id})")

def on_error(ws, error):
    logging.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logging.info(f"WebSocket closed: {close_status_code}, {close_msg}")

def process_order_book(symbol, data):
    """Process order book updates."""
    with order_book_lock:
        book = order_books.setdefault(symbol, {'bids': {}, 'asks': {}})
        if 'change' in data:
            price_str, side, quantity_str = data['change'].split(',')
            price = Decimal(price_str)
            quantity = Decimal(quantity_str)
            side_key = 'bids' if side == 'buy' else 'asks'
            if quantity > 0:
                book[side_key][price] = quantity
            else:
                book[side_key].pop(price, None)
            logging.debug(f"Processed update for {symbol}: {side} {price} {quantity}")
        elif 'bids' in data and 'asks' in data:
            book['bids'] = {Decimal(price): Decimal(qty) for price, qty, _ in data['bids'] if Decimal(qty) > 0}
            book['asks'] = {Decimal(price): Decimal(qty) for price, qty, _ in data['asks'] if Decimal(qty) > 0}
            logging.debug(f"Processed snapshot for {symbol}")

def calculate_spread_and_levels(symbol):
    """Calculate spread and top bid/ask prices."""
    with order_book_lock:
        book = order_books.get(symbol, {})
        bids, asks = book.get('bids', {}), book.get('asks', {})
        if not bids or not asks:
            return float('inf'), None, None
        top_bid = max(bids.keys())
        top_ask = min(asks.keys())
        return float(top_ask - top_bid), float(top_bid), float(top_ask)

def calculate_imbalance(symbol):
    """Calculate order book imbalance."""
    with order_book_lock:
        book = order_books.get(symbol, {})
        bids, asks = book.get('bids', {}), book.get('asks', {})
        total_bids = float(sum(bids.values(), Decimal('0')))
        total_asks = float(sum(asks.values(), Decimal('0')))
        total = total_bids + total_asks
        return (total_bids - total_asks) / total if total > 0 else 0.0

def detect_special_value(symbol):
    """Detect significant order book depth as a special value trigger."""
    with order_book_lock:
        book = order_books.get(symbol, {})
        bids, asks = book.get('bids', {}), book.get('asks', {})
        if not bids or not asks:
            return False, None
        total_bid_volume = sum(bids.values(), Decimal('0'))
        total_ask_volume = sum(asks.values(), Decimal('0'))
        # Trigger if top 5 levels on one side exceed 50% of total volume on that side
        top_bids = sorted(bids.items(), reverse=True)[:5]
        top_asks = sorted(asks.items())[:5]
        bid_depth = sum(qty for _, qty in top_bids) / total_bid_volume if total_bid_volume > 0 else 0
        ask_depth = sum(qty for _, qty in top_asks) / total_ask_volume if total_ask_volume > 0 else 0
        if bid_depth > 0.5:
            return True, "Strong bid wall detected"
        if ask_depth > 0.5:
            return True, "Strong ask wall detected"
        return False, None

def wait_for_order_book(symbol, timeout=10):
    """Wait for order book data."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        with order_book_lock:
            book = order_books.get(symbol, {})
            if book.get('bids') and book.get('asks'):
                return True
        time.sleep(0.5)
    logging.warning(f"Timeout waiting for order book data for {symbol}")
    return False

def get_usdt_futures_balance(exchange):
    """Fetch USDT balance."""
    try:
        balance = exchange.fetch_balance({'type': 'future'})
        usdt_balance = balance.get('USDT', {}).get('free', 0.0)
        return float(usdt_balance)
    except ccxt.BaseError as e:
        logging.error(f"Error fetching USDT balance: {e}")
        return 0.0

def calculate_min_cash_required(exchange, symbol):
    """Estimate minimum cash required."""
    try:
        market = exchange.market(symbol)
        price = get_current_price(exchange, symbol)
        if not price:
            return float('inf')
        lot_size = market.get('limits', {}).get('amount', {}).get('min', 1)
        initial_margin = 1 / market.get('leverage', {}).get('max', 10)
        return 6 * lot_size * price * initial_margin
    except Exception as e:
        logging.error(f"Error calculating min cash for {symbol}: {e}")
        return float('inf')

def execute_trade(exchange, conn, symbol, amount, ordertype, strategy):
    """Execute trade via strategy functions (simplified)."""
    try:
        if ordertype == "buy":
            bullish_scalping(exchange, conn, symbol, amount)
        elif ordertype == "sell":
            bearish_short_scalping(exchange, conn, symbol, amount)
        elif ordertype == "side":
            mean_reversion_trading(exchange, conn, symbol, amount)
    except Exception as e:
        logging.error(f"Error executing trade for {symbol}: {e}")

def execute_trading_strategy(exchange, conn, symbol, market_condition):
    """Execute trading strategy with enhanced logging."""
    if not wait_for_order_book(symbol):
        logging.info(f"No order book data for {symbol}. Skipping.")
        return
    spread, top_bid, top_ask = calculate_spread_and_levels(symbol)
    if spread == float('inf'):
        logging.info(f"Order book not ready for {symbol}. Skipping.")
        return
    price = get_current_price(exchange, symbol)
    if not price:
        return
    if spread > 0.01 * price:
        logging.info(f"Spread too wide for {symbol} ({spread:.2f}). Skipping.")
        return
    imbalance = calculate_imbalance(symbol)
    special_trigger, special_reason = detect_special_value(symbol)
    CASH_PERCENTAGE = 0.1
    cash = get_usdt_futures_balance(exchange)
    if cash <= 0:
        logging.warning(f"No USDT balance available for trading {symbol}.")
        return
    amount_max = cash * CASH_PERCENTAGE
    amount_min = calculate_min_cash_required(exchange, symbol)
    amount_promised = max(amount_max, amount_min)
    if cash < amount_min:
        logging.warning(f"Insufficient funds for {symbol}. Required: {amount_min}, Available: {cash}")
        return
    log_msg = (f"Executing strategy for {symbol}: Condition={market_condition}, "
               f"Top Bid={top_bid}, Top Ask={top_ask}, Spread={spread:.2f}, Imbalance={imbalance:.2f}")
    if special_trigger:
        log_msg += f", Special Trigger={special_reason}"
    logging.info(log_msg)
    strategy = None
    ordertype = None
    if special_trigger or imbalance > 0.1 or market_condition == "bullish":
        strategy = "bullish_scalping"
        ordertype = "buy"
    elif imbalance < -0.1 or market_condition == "bearish":
        strategy = "bearish_short_scalping"
        ordertype = "sell"
    elif market_condition == "sideways":
        strategy = "mean_reversion_trading"
        ordertype = "side"
    if strategy and ordertype:
        execute_trade(exchange, conn, symbol, amount_promised, ordertype, strategy)
        # Optionally log order book state to database
        # log_trade(conn, strategy, symbol, ordertype, price, amount_promised/price, 1, top_bid=top_bid, top_ask=top_ask, spread=spread, imbalance=imbalance)

def maintain_active_trading(exchange, conn):
    """Main trading loop."""
    global ws, top_5_symbols
    while True:
        try:
            if not ws or not ws.sock or not ws.sock.connected:
                time.sleep(5)
                continue
            top_5_symbols = select_top_5_pumping_coins(exchange)
            current_subscribed = set(subscribed_symbols)
            for symbol in set(top_5_symbols) - current_subscribed:
                symbol_id = exchange.markets[symbol]['id']
                ws.send(json.dumps({
                    "id": str(random.randint(1000, 9999)),
                    "type": "subscribe",
                    "topic": f"/contractMarket/level2:{symbol_id}",
                    "response": True
                }))
                subscribed_symbols.add(symbol)
                logging.info(f"Subscribed to {symbol} (ID: {symbol_id})")
            for symbol in current_subscribed - set(top_5_symbols):
                symbol_id = exchange.markets[symbol]['id']
                ws.send(json.dumps({
                    "id": str(random.randint(1000, 9999)),
                    "type": "unsubscribe",
                    "topic": f"/contractMarket/level2:{symbol_id}",
                    "response": True
                }))
                subscribed_symbols.remove(symbol)
                logging.info(f"Unsubscribed from {symbol} (ID: {symbol_id})")
            for symbol in top_5_symbols:
                market_condition = get_market_condition(symbol)
                execute_trading_strategy(exchange, conn, symbol, market_condition)
            time.sleep(30)
        except Exception as e:
            logging.error(f"Trading loop error: {e}")
            time.sleep(60)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/trade_data', methods=['GET'])
def get_trade_data():
    conn = init_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    try:
        with conn.cursor(buffered=True) as cursor:
            cursor.execute("SELECT trade_id, strategy, symbol, trade_type, entry_price, quantity, status FROM trades WHERE status = 'open'")
            open_trades = [{"trade_id": t[0], "strategy": t[1], "symbol": t[2], "trade_type": t[3], 
                            "entry_price": float(t[4]), "quantity": float(t[5]), "status": t[6]} for t in cursor.fetchall()]
            cursor.execute("SELECT trade_id, strategy, symbol, trade_type, entry_price, exit_price, quantity, profit_loss, status FROM trades WHERE status IN ('closed', 'stopped')")
            closed_trades = [{"trade_id": t[0], "strategy": t[1], "symbol": t[2], "trade_type": t[3], 
                              "entry_price": float(t[4]), "exit_price": float(t[5]) if t[5] else None, 
                              "quantity": float(t[6]), "profit_loss": float(t[7]) if t[7] else None, "status": t[8]} for t in cursor.fetchall()]
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
            "open_trades": open_trades, "closed_trades": closed_trades, "daily_pnl": daily_pnl,
            "total_trades": total_trades, "win_rate": win_rate, "average_pnl": average_pnl
        })
    except mysql.connector.Error as e:
        logging.error(f"Error fetching trade data: {e}")
        return jsonify({"error": "Database error"}), 500
    finally:
        conn.close()

def start_websocket():
    global ws
    while True:
        token, endpoint, ping_interval, ping_timeout = get_websocket_token()
        if not token or not endpoint:
            time.sleep(10)
            continue
        ws_url = f"{endpoint}?token={token}&connectId={random.randint(1, 10000)}"
        ws = websocket.WebSocketApp(ws_url, on_message=on_message, on_open=on_open, 
                                    on_error=on_error, on_close=on_close)
        ws.run_forever(ping_interval=ping_interval, ping_timeout=ping_timeout)
        logging.warning("WebSocket lost. Reconnecting in 5 seconds...")
        time.sleep(5)

def main():
    global exchange, conn, id_to_symbol
    exchange = init_kucoin_client()
    conn = init_db_connection()
    if not exchange or not conn:
        logging.error("Initialization failed.")
        return
    id_to_symbol = {market['id']: symbol for symbol, market in exchange.markets.items()}
    threading.Thread(target=start_websocket, daemon=True).start()
    time.sleep(5)
    threading.Thread(target=maintain_active_trading, args=(exchange, conn), daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True, use_reloader=False)

if __name__ == '__main__':
    main()