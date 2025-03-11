import os
import logging
import time
import mysql.connector

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv('DB_NAME', 'kucoin_trading')

def init_db_connection():
    """Initialize database connection with retry."""
    for attempt in range(3):
        try:
            conn = mysql.connector.connect(
                user=DB_USER, password=DB_PASSWORD, host=DB_HOST, database=DB_NAME,
                pool_name="trading_pool", pool_size=10, pool_reset_session=True, connection_timeout=30
            )
            if conn.is_connected():
                logging.info("Database connection established.")
                return conn
            raise mysql.connector.Error("Connection failed")
        except mysql.connector.Error as e:
            logging.warning(f"DB connection error (attempt {attempt + 1}): {e}")
            time.sleep(2 ** attempt)
    logging.error("Failed to connect to DB after 3 attempts.")
    return None

def log_trade(conn, strategy, symbol, trade_type, entry_price, quantity, multiplier, status="open", exit_price=None, profit_loss=None, top_bid=None, top_ask=None, spread=None, imbalance=None):
    """Log trade to database with optional order book data."""
    if not conn or not conn.is_connected():
        conn = init_db_connection()
    if not conn:
        logging.error("No database connection available.")
        return None
    try:
        with conn.cursor() as cursor:
            # Note: Table must include columns for top_bid, top_ask, spread, imbalance if used
            query = """
                INSERT INTO trades (strategy, symbol, trade_type, entry_price, quantity, multiplier, status, exit_price, profit_loss, top_bid, top_ask, spread, imbalance, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """
            cursor.execute(query, (strategy, symbol, trade_type, entry_price, quantity, multiplier, status, exit_price, profit_loss, top_bid, top_ask, spread, imbalance))
            conn.commit()
            trade_id = cursor.lastrowid
            logging.debug(f"Logged trade {trade_id} for {symbol}: Bid={top_bid}, Ask={top_ask}, Spread={spread}, Imbalance={imbalance}")
            return trade_id
    except mysql.connector.Error as e:
        logging.error(f"Error logging trade: {e}")
        return None

def log_order(conn, order_id, trade_id, order_type, price, quantity, status="open"):
    """Log order to database."""
    if not conn or not conn.is_connected():
        conn = init_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cursor:
            query = """
                INSERT INTO orders (order_id, trade_id, order_type, price, quantity, status, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """
            cursor.execute(query, (order_id, trade_id, order_type, price, quantity, status))
            conn.commit()
            logging.debug(f"Logged order {order_id} for trade {trade_id}")
    except mysql.connector.Error as e:
        logging.error(f"Error logging order: {e}")

def update_trade(conn, trade_id, status, exit_price=None):
    """Update trade status and calculate profit/loss."""
    if not conn or not conn.is_connected():
        conn = init_db_connection()
    if not conn:
        return
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
            logging.info(f"Updated trade {trade_id} to status {status}")
    except mysql.connector.Error as e:
        logging.error(f"Error updating trade: {e}")