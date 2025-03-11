#!/usr/bin/env python3

import os
import sys
import time
import json
import random
import argparse
import logging
from datetime import datetime, timedelta
import re
from pytz import timezone

import ccxt
import mysql.connector
from mysql.connector import Error
from kavenegar import KavenegarAPI, APIException, HTTPException
import pandas as pd
import psutil
from colorama import init, Fore, Style
from strategies import bullish_scalping, bearish_short_scalping
# Initialize colorama for colored terminal output
init()

# --- Constants and Configuration ---
API_KEY = os.getenv("KUCOIN_API_KEY", "67c9d31345e41a0001676a6f")
API_SECRET = os.getenv("KUCOIN_API_SECRET", "7b93d018-c606-40db-b8d0-b4bee166eef1")
API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "8529118#Ahe")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "nexus")
SMS_API_KEY = os.getenv("SMS_API_KEY", "6D5461396F694E753267644269584B6956736F4155486A6E4E3665545A753352424E765959686D6C34773D")
SMS_RECEPTOR = os.getenv("SMS_RECEPTOR", "09022920101")
SMS_TEMPLATE = os.getenv("SMS_TEMPLATE", "defacto-buysell")

# Timezone configuration
TEHRAN_TZ = timezone('Asia/Tehran')
# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def parse_closetime(closetime_str: str) -> datetime:
    """
    Parse the closetime string.
    Accepts absolute datetime format '%Y-%m-%d %H:%M:%S'
    or relative time formats like '30m' (30 minutes), '1h' (1 hour), or '1d' (1 day).
    """
    try:
        # Try absolute datetime format first
        return datetime.strptime(closetime_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TEHRAN_TZ)
    except ValueError:
        # Check for relative time format (e.g., '30m', '1h', '1d')
        match = re.match(r"(\d+)([mhd])", closetime_str)
        if match:
            value, unit = match.groups()
            value = int(value)
            if unit == 'm':
                delta = timedelta(minutes=value)
            elif unit == 'h':
                delta = timedelta(hours=value)
            elif unit == 'd':
                delta = timedelta(days=value)
            else:
                raise ValueError("Invalid time unit in closetime.")
            return datetime.now(TEHRAN_TZ) + delta
        else:
            raise ValueError("Invalid closetime format. Use '%Y-%m-%d %H:%M:%S' or a relative format like '30m'.")

def retry_mysql_connection(retries: int = 3, backoff: int = 5) -> mysql.connector.connection.MySQLConnection:
    """Establish a MySQL connection with retries."""
    for i in range(retries):
        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME
            )
            if conn.is_connected():
                logger.info("MySQL connection established.")
                return conn
        except Error as e:
            logger.warning(f"MySQL connection failed: {e}. Retrying in {backoff}s...")
            time.sleep(backoff)
    logger.error("Failed to connect to MySQL after retries.")
    return None

def save_trading_record(data: dict) -> None:
    """Save trading record to MySQL database."""
    query = """
    INSERT INTO trading_ontime
    (qid, entrying_price, leverage, target_stop_pct, target_profit_pct, order_type,
     opentime, xgbooti, take_profit, stop_loss, symbolku, position_info, type_of_exit,
     closetime, pnl, pxl, stop_loss_prices, take_profit_prices, current_price, orders_ids, contract_size)
    VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    conn = None
    try:
        conn = retry_mysql_connection()
        if not conn:
            logger.error("No database connection available.")
            return
        with conn.cursor() as cursor:
            cursor.execute(query, (
                data.get("qid"), data.get("entrying_price"), data.get("leverage"),
                data.get("target_stop_pct"), data.get("target_profit_pct"), data.get("order_type"),
                data.get("opentime"), json.dumps(data.get("xgbooti")), data.get("take_profit"),
                data.get("stop_loss"), data.get("symbolku"), json.dumps(data.get("position_info")),
                data.get("type_of_exit"), data.get("closetime"), data.get("pnl"), data.get("pxl"),
                data.get("stop_loss_prices"), data.get("take_profit_prices"), data.get("current_price"),
                data.get("orders_ids"), data.get("contract_size")
            ))
            conn.commit()
            logger.info(f"Trade record saved with ID: {cursor.lastrowid}")
    except Error as e:
        logger.error(f"Error saving trade record: {e}")
    finally:
        if conn and conn.is_connected():
            conn.close()

def send_sms_otp(quantity: str, coin: str, side: str) -> None:
    """Send SMS notification via Kavenegar API."""
    try:
        rnd = random.randint(1000, 2000)
        now = datetime.now(TEHRAN_TZ).strftime("%H:%M:%S")
        api = KavenegarAPI(SMS_API_KEY)
        params = {
            'receptor': SMS_RECEPTOR,
            'template': SMS_TEMPLATE,
            'token': quantity,
            'token2': coin,
            'token3': side,
            'token10': now,
            'token20': rnd,
            'type': 'sms'
        }
        response = api.verify_lookup(params)
        logger.info(f"SMS sent successfully: {response}")
    except (APIException, HTTPException) as e:
        logger.error(f"Error sending SMS: {e}")

# --- Exchange Setup ---
try:
    exchange = ccxt.kucoinfutures({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'password': API_PASSPHRASE,
        'enableRateLimit': True,
    })
    exchange.load_markets()
    logger.info("Exchange initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize exchange: {e}")
    sys.exit(1)

# --- Trading Functions ---

def execute_trade(exchange, conn, symbol, amount, ordertype):
    """Execute trade via strategy functions (simplified)."""
    try:
        if ordertype == "buy":
            bullish_scalping(exchange, conn, symbol, investment_amount=amount)
        elif ordertype == "sell":
            bearish_short_scalping(exchange, conn, symbol, investment_amount=amount)
        # Note: 'side' ordertype is not handled in this context
    except Exception as e:
        logging.error(f"Error executing trade for {symbol}: {e}")

def close_position(symbol: str) -> dict:
    """Close an open position."""
    try:
        position = exchange.fetch_position(symbol)
        if not position or position['contracts'] == 0:
            logger.info(f"No open position for {symbol}")
            return None
        side = 'sell' if position['side'] == 'long' else 'buy'
        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side=side,
            amount=position['contracts'],
            params={'reduceOnly': True}
        )
        logger.info(f"Position closed for {symbol}: {order.get('id')}")
        return order
    except ccxt.BaseError as e:
        logger.error(f"Error closing position for {symbol}: {e}")
        return None

def karbit(qid: str, symbol: str, ordertype: str, leverage: str, closetime: str, entry_price: str, 
           amount: str, target_profit_pct: str, trailing_stop_pct: str, trailing_profit_pct: str) -> None:
    """Execute the main trading strategy using execute_trade and monitor position."""
    try:
        # Convert inputs to appropriate types
        leverage = float(leverage)
        entry_price = float(entry_price)
        amount_usdt = float(amount)
        target_profit_pct = float(target_profit_pct)
        trailing_stop_pct = float(trailing_stop_pct)
        
        # Parse closetime
        closetime_dt = parse_closetime(closetime)
        
        # Validate symbol
        if symbol not in exchange.markets:
            logger.error(f"Invalid symbol: {symbol}")
            return
        
        # Establish database connection
        conn = retry_mysql_connection()
        if not conn:
            logger.error("Failed to establish database connection.")
            return
        
        # Execute the trade using the provided execute_trade function
        execute_trade(exchange, conn, symbol, amount_usdt, ordertype)
        
        # Monitor the position until closetime or until closed by strategy
        start_time = datetime.now(TEHRAN_TZ)
        while True:
            position = exchange.fetch_position(symbol)
            if not position or position['contracts'] == 0:
                logger.info(f"Position closed for {symbol} (likely by strategy SL/TP)")
                break
            if datetime.now(TEHRAN_TZ) > closetime_dt:
                close_position(symbol)
                logger.info(f"Position closed for {symbol} at closetime")
                break
            time.sleep(5)  # Avoid overwhelming the API
        
        # Fetch final position details and current price
        position = exchange.fetch_position(symbol)  # May be empty if closed
        current_price = float(exchange.fetch_ticker(symbol)['last'])
        
        # Prepare trade data for logging
        trade_data = {
            "qid": qid,
            "entrying_price": entry_price,
            "leverage": leverage,
            "target_stop_pct": trailing_stop_pct,
            "target_profit_pct": target_profit_pct,
            "order_type": ordertype,
            "opentime": start_time.strftime('%Y-%m-%d %H:%M:%S'),
            "xgbooti": {},
            "take_profit": target_profit_pct,
            "stop_loss": trailing_stop_pct,
            "symbolku": symbol,
            "position_info": position if position else {},
            "type_of_exit": "time_based" if datetime.now(TEHRAN_TZ) > closetime_dt else "strategy",
            "closetime": datetime.now(TEHRAN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
            "pnl": position.get('unrealizedPnl', 0) if position else 0,
            "pxl": 0,
            "stop_loss_prices": None,  # Set by strategy functions
            "take_profit_prices": None,  # Set by strategy functions
            "current_price": current_price,
            "orders_ids": None,  # Could be enhanced if execute_trade returns order IDs
            "contract_size": position.get('contracts', 0) if position else 0
        }
        save_trading_record(trade_data)
        
        # Send SMS notification
        contract_size = position.get('contracts', 0) if position else amount_usdt  # Fallback to amount
        send_sms_otp(str(contract_size), symbol.split('/')[0], ordertype)
        
    except Exception as e:
        logger.error(f"Error in karbit: {e}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            conn.close()

def main() -> None:
    """Parse command-line arguments and start trading."""
    parser = argparse.ArgumentParser(description="Automated trading script for KuCoin Futures.")
    parser.add_argument("--qid", type=str, required=True, help="Database trade ID")
    parser.add_argument("--symbol", type=str, required=True, help="Trading symbol (e.g., BTC/USDT)")
    parser.add_argument("--entry_price", type=str, required=True, help="Entry price")
    parser.add_argument("--ordertype", type=str, required=True, choices=['buy', 'sell'], help="Order type")
    parser.add_argument("--deflev", type=str, required=True, help="Leverage")
    parser.add_argument("--closetime", type=str, required=True, help="Close time (YYYY-MM-DD HH:MM:SS or relative like '30m')")
    parser.add_argument("--amount", type=str, required=True, help="Amount in USDT")
    parser.add_argument("--target_profit_pct", type=str, required=True, help="Target profit percentage")
    parser.add_argument("--trailing_stop_pct", type=str, required=True, help="Trailing stop percentage")
    parser.add_argument("--trailing_profit_pct", type=str, required=True, help="Trailing profit percentage (currently unused)")

    args = parser.parse_args()

    karbit(
        args.qid, args.symbol, args.ordertype, args.deflev, args.closetime,
        args.entry_price, args.amount, args.target_profit_pct,
        args.trailing_stop_pct, args.trailing_profit_pct
    )

if __name__ == "__main__":
    main()