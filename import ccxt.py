import ccxt
import time
import numpy as np
import pandas as pd
import os
import ctypes
import psutil
import sys
from datetime import datetime, timedelta
from colorama import init, Fore, Style
import warnings
import mysql.connector
from mysql.connector import Error
import argparse
import requests
import pytz
import time
import hmac
import hashlib
import base64
import requests
import json
import random
import string
import math
import random
from kavenegar import KavenegarAPI, APIException, HTTPException
import psutil
import win32gui
from kucoin_functions import (
    safe_request,
    get_position_details,
    get_wallet_balance,
    )
import json

from kucoin_functions import (
    generate_headers,
    safe_request,
    positions_status,
    get_wallet_balance,
    get_klines,
    get_symbol_info,
    get_order_list,
    cancel_order,
    generate_client_oid,
    start_trading,
    calculate_leverage,
    positions_statuz,
    get_kucoin_active_symbols,
    get_order_list,
    set_order_params,
    get_current_price,
    send_sms_otp,
    get_order_details,
    get_filled_list,
    get_position_details,
    get_ticker,

)
import os
import psutil
import subprocess
import win32gui
import win32process


# Function to send an OTP SMS
api_key_sms = "6D5461396F694E753267644269584B6956736F4155486A6E4E3665545A753352424E765959686D6E6C34773D"
receptor = "09022920101"
template = "defacto-buysell"


import time
from kucoin_futures.client import Trade
import subprocess

import psutil
from pywinauto import Desktop

def find_windows_by_title(title_pattern):
    """
    Finds windows by title pattern using pywinauto.
    Args:
        title_pattern (str): Partial or full window title to search for.
    Returns:
        list: A list of tuples containing (PID, window title) for matching windows.
    """
    matching_windows = []
    try:
        windows = Desktop(backend="uia").windows()
        for window in windows:
            if title_pattern in window.window_text():
                try:
                    pid = window.process_id()
                    matching_windows.append((pid, window.window_text()))
                except Exception as e:
                    print(f"Error retrieving PID for window '{window.window_text()}': {e}")
    except Exception as e:
        print(f"Error accessing windows: {e}")
    return matching_windows

def close_other_cmd_instances(title_pattern):
    """
    Closes all CMD processes related to trading, identified by their window titles.
    Args:
        title_pattern (str): Partial or full window title to search for.
    """
    # Find matching windows
    matching_windows = find_windows_by_title(title_pattern)
    if not matching_windows:
        print("No matching CMD processes found.")
        return

    print("Found matching windows:")
    for pid, title in matching_windows:
        print(f"PID: {pid}, Title: {title}")

    # Kill matching CMD processes
    for pid, title in matching_windows:
        try:
            process = psutil.Process(pid)
            print(f"Terminating process with PID: {pid}, Title: {title}")
            process.terminate()
            process.wait(timeout=5)  # Wait for process termination
            print(f"Successfully terminated process with PID: {pid}")
        except psutil.NoSuchProcess:
            print(f"Process with PID {pid} does not exist (already terminated).")
        except psutil.AccessDenied:
            print(f"Access denied to terminate process with PID {pid}.")
        except psutil.TimeoutExpired:
            print(f"Timeout while waiting for process {pid} to terminate.")
        except Exception as e:
            print(f"Error terminating process with PID {pid}: {e}")

def save_trading_record(data):
    """
    Save a trading record to the MySQL database.

    Args:
    data (dict): A dictionary containing the trading record data.
    """
    query = """
    INSERT INTO trading_ontime
    (qid, entrying_price, leverage, target_stop_pct, target_profit_pct, order_type, 
     opentime, xgbooti, take_profit, stop_loss, symbolku, position_info, type_of_exit, closetime, pnl, pxl, stop_loss_prices, take_profit_prices, current_price, orders_ids, contract_size)
    VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """
    
    try:
        conn = retry_mysql_connection()
        # Create a cursor
        cursor = conn.cursor()

        # Execute the query with values from the data dictionary
        cursor.execute(query, (
            data.get("qid"),
            data.get("entrying_price"),
            data.get("leverage"),
            data.get("target_stop_pct"),
            data.get("target_profit_pct"),
            data.get("order_type"),
            data.get("opentime"),
            json.dumps(data.get("xgbooti")),  # Convert to JSON string if necessary
            data.get("take_profit"),
            data.get("stop_loss"),
            data.get("symbolku"),
            json.dumps(data.get("position_info")),  # Convert to JSON string if necessary
            data.get("type_of_exit"),
            data.get("closetime"),
            data.get("pnl"),
            data.get("pxl"), 
            data.get("stop_loss_prices"), 
            data.get("take_profit_prices"),
            data.get("current_price"),
            data.get("orders_ids"),
            data.get("contract_size")
        ))

        # Commit the transaction
        conn.commit()
        print(f"Record added successfully with ID: {cursor.lastrowid}")
    
    except Error as e:
        print(f"Error while saving trading record: {e}")

import subprocess
# Retry mechanism for HTTP requests
def retry_request(func, *args, retries=3, delay=5, **kwargs):
    attempt = 0
    while attempt < retries:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"Error in {func.__name__}: {e}. Retrying {attempt + 1}/{retries}...")
            attempt += 1
            time.sleep(delay)
    raise Exception(f"Failed to retrieve data after {retries} attempts.")

# Retry MySQL connection
def retry_mysql_connection(retries=3, backoff=5):
    for i in range(retries):
        try:
            print(f"Attempting to connect to MySQL (Attempt {i + 1}/{retries})")
            conn = mysql.connector.connect(
                host='localhost',       # Typically 'localhost' or an IP address
                port=3306,
                user='root',           # Your MySQL username
                password='',           # Your MySQL password
                database='nexus'       # Your database name
            )
            if conn.is_connected():
                print("MySQL connection established successfully.")
                return conn
        except Error as e:
            print(f"Connection failed: {e}. Retrying in {backoff} seconds...")
            time.sleep(backoff)
    print("All retry attempts failed for MySQL connection.")
    return None

def safe_request(method, url, headers=None, data=None, params=None, retries=3):
    for i in range(retries):
        try:
            print(f"Attempting request {method} {url} (Attempt {i+1}/{retries})")
            response = requests.request(method, url, headers=headers, data=data, params=params, timeout=10)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}. Retrying...")
            time.sleep(5)  # Sleep before retrying
    return None

# API Credentials
API_KEY = os.getenv("KUCOIN_API_KEY", "67c9d31345e41a0001676a6f")
API_SECRET = os.getenv("KUCOIN_API_SECRET", "7b93d018-c606-40db-b8d0-b4bee166eef1")
API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "8529118#Ahe")

# Initialization Functions

try:
    exchange = ccxt.kucoinfutures({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'password': API_PASSPHRASE,
        'enableRateLimit': True,
    })
    exchange.load_markets()
    global id_to_symbol
    id_to_symbol = {market['id']: symbol for symbol, market in exchange.markets.items()}

except Exception as e:
    print("Can't do with exchange")


# Function to generate headers for API requests
def generate_headers(method, url, data=None):
    now = int(time.time() * 1000)
    path = url.replace("https://api-futures.kucoin.com", "")  # Remove the base URL to get the request path

    # Prepare the string to sign
    str_to_sign = f"{now}{method.upper()}{path}"
    
    # If there is data (for POST/PUT requests), include it in the string to sign
    if data:
        str_to_sign += json.dumps(data)
    
    # Signature
    signature = base64.b64encode(
        hmac.new(API_SECRET.encode('utf-8'), str_to_sign.encode('utf-8'), hashlib.sha256).digest()
    ).decode('utf-8')

    # Passphrase (if required by your API setup)
    passphrase = base64.b64encode(
        hmac.new(API_SECRET.encode('utf-8'), API_SECRET.encode('utf-8'), hashlib.sha256).digest()
    ).decode('utf-8')
    
    headers = {
        "KC-API-KEY": API_KEY,
        "KC-API-SIGN": signature,
        "KC-API-TIMESTAMP": str(now),
        "KC-API-PASSPHRASE": API_PASSPHRASE,
        "KC-API-KEY-VERSION": "2",  # Version 2 is typically required
    }

    if method != "GET":
        headers["Content-Type"] = "application/json"

    return headers

def close_position(symbol):
    # Retrieve current position details
    position = get_position_details(symbol)
    if not position or not position.get('isOpen'):
        print(f"No open position found for {symbol}.")
        return None

    # Determine the side to place the order
    side = 'buy' if position['currentQty'] < 0 else 'sell'
    size = abs(position['currentQty'])
    leverage = position['leverage']
    # Prepare the order data
    order_data = {
        "clientOid": generate_client_oid(),
        "side": side,
        "symbol": symbol,
        "type": "market",
        "size": size,
        "reduceOnly": True,
        "leverage": leverage  # Include the leverage parameter
    }

    # Send the order request
    url = "https://api-futures.kucoin.com/api/v1/orders"
    headers = generate_headers("POST", url, order_data)
    response = safe_request("POST", url, headers=headers, data=json.dumps(order_data))

    if response:
        try:
            order_response = response.json()
            if order_response.get("code") == "200000":
                print(f"Successfully closed position for {symbol}. Order ID: {order_response['data']['orderId']}")
            else:
                print(f"Failed to close position: {order_response.get('msg', 'Unknown error')}")
            return order_response
        except (ValueError, KeyError) as e:
            print(f"Error parsing response: {e}")
            return None
    else:
        print("Failed to close position after retries.")
        return None


def cancel_all_orders(symbol):
    try:
        response = exchange.cancel_all_limit_order(symbol)
        print(f"Successfully canceled all orders for {symbol}.")
        return response
    except Exception as e:
        print(f"Failed to cancel orders: {e}")
        return None
    
def cancel_all_stop_orders(symbol):
    try:
        response = exchange.cancel_all_stop_order(symbol)
        print(f"Successfully canceled all stop orders for {symbol}.")
        return response
    except Exception as e:
        print(f"Failed to cancel stop orders: {e}")
        return None
    
def terminate_all_positions_and_orders(symbol):
    cancel_all_orders(symbol)
    cancel_all_stop_orders(symbol)
    close_position(symbol)



def send_sms_otp(quantity, coin, side):
    try:
        rnd = int(random.randint(1000, 2000))
        nowing = datetime.now()
        mdate = nowing.strftime("%H:%M:%S")
        api = KavenegarAPI(api_key_sms)
        params = {
            'receptor': receptor,
            'template': template,
            'token': quantity,
            'token2': coin,
            'token3': side,
            'token10': mdate,
            'token20': rnd,
            'type': 'sms',  # sms vs call
        }
        response = api.verify_lookup(params)
        print(response)
    except APIException as e:
        print(e)
    except HTTPException as e:
        print(e)

# Initialize colorama and suppress warnings
init()
warnings.filterwarnings("ignore", category=FutureWarning, module="ta.trend")

# Define the timezone for Tehran
tehran_tz = pytz.timezone('Asia/Tehran')





def fetch_klines(exchange, symbol, timeframe='1m', retries=3):
    while retries > 0:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe)
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            return df.set_index('time')
        except Exception as e:
            print(f"Error fetching data: {e}, retrying...")
            retries -= 1
    raise RuntimeError(f"Failed to fetch market data for {symbol}")

def get_contract_size(exchange, amount_usdt, symbol, leverage):
    try:
        amount_usdt = float(amount_usdt)
        leverage = float(leverage)

        market = exchange.fetch_markets()
        market_info = next((m for m in market if m['symbol'] == symbol), None)
        if market_info:
            multiplier = market_info.get('contractSize', 1)
            mark_price = float(exchange.fetch_ticker(symbol)['last'])
            contract_size = (amount_usdt * leverage) / (multiplier * mark_price)
            return contract_size
        else:
            print(f"Failed to retrieve market information for symbol: {symbol}")
            return None
    except Exception as e:
        print(f"Error in get_contract_size: {e}")
        return None

def display_position_info(stop_loss, take_profit, symbol, order_type, entry_price, current_price, position_size, leverage, trailing_stop_pct, trailing_profit_pct):
    # Convert all inputs to float
    entry_price = float(entry_price)
    current_price = float(current_price)
    stop_loss = float(stop_loss)
    take_profit = float(take_profit)
    
    # Line length represents the "border" of the price range
    line_length = 60  # Total length of the visual line

    # Calculate relative positions on the line for stop-loss, entry, current price, and take-profit
    if take_profit != stop_loss:  # Prevent division by zero
        if order_type == 'buy':
            entry_pos = int((entry_price - stop_loss) / (take_profit - stop_loss) * line_length)
            price_pos = int((current_price - stop_loss) / (take_profit - stop_loss) * line_length)
        else:  # sell orders
            entry_pos = int((take_profit - entry_price) / (take_profit - stop_loss) * line_length)
            price_pos = int((take_profit - current_price) / (take_profit - stop_loss) * line_length)
    else:
        # Handle case where stop_loss and take_profit are the same
        entry_pos = price_pos = 0

    # Set boundaries for stop-loss and take-profit
    stop_loss_pos = 0 if order_type == 'buy' else line_length - 1
    take_profit_pos = line_length - 1 if order_type == 'buy' else 0

    # Ensure all positions stay within bounds of the line
    entry_pos = max(0, min(line_length - 1, entry_pos))
    price_pos = max(0, min(line_length - 1, price_pos))

    # Generate the line with dynamic pricing positions
    line = ['-'] * line_length
    line[price_pos] = '*'  # Current price position
    line[entry_pos] = Fore.YELLOW + '|' + Style.RESET_ALL  # Entry price position
    line[stop_loss_pos] = Fore.RED + '|' + Style.RESET_ALL  # Stop-loss position
    line[take_profit_pos] = Fore.GREEN + '|' + Style.RESET_ALL  # Take-profit position

    # Print the visual representation
    print(f"FOR THE {order_type.upper()} ORDER OF {symbol}, POSITION SIZE: {position_size}, LEVERAGE: {leverage}x")
    print(''.join(line))

def create_main(exchange, symbol, side, leverage, contract_size):
    try:
        main_order = exchange.create_order(
            symbol,
            type='market',
            side=side,
            amount=contract_size,
            params={
                'leverage': leverage
            }
        )
        return main_order
    except ccxt.BaseError as e:
        print(f"Error placing order for {symbol}: {e}")
        return None

def calculate_closetime(tehran_time_open, closing_timeframe):
        timeframe_deltas = {
            "15m": timedelta(minutes=15),
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "12h": timedelta(hours=12),
            "1d": timedelta(days=1),
            "36h": timedelta(hours=36)
        }
        
        # Validate the closing timeframe
        if closing_timeframe not in timeframe_deltas:
            raise ValueError(f"Invalid closing timeframe: {closing_timeframe}. Choose from {list(timeframe_deltas.keys())}")
        
        # Calculate closetime
        return tehran_time_open + timeframe_deltas[closing_timeframe]



def log_trade_details(qid, symbol, entry_price, lev, order_data, stop_loss, take_profit, order_id):
    log_data = {
        "Query id": qid,
        "Contract": symbol,
        "Entry Price": entry_price,
        "Amount": order_data['amount'],
        "Order Price": order_data['price'],
        "Stop Price": stop_loss,
        "Take Profit Price": take_profit,
        "Leverage": lev,
        "Order ID": order_id
    }
    print(log_data)



def set_multiple_stop_loss_take_profit(exchange, symbol, side, leverage, contract_size, entry_price, stop_loss_pct, take_profit_pct):
    levels = 4
    levels_stp = 2
    stop_loss_prices = []
    take_profit_prices = []
    orders_ids = []
    try:
        # Set the stop-loss orders
        for i in range(1, levels_stp + 1):
            level_stop_price = entry_price * (1 - (i * (stop_loss_pct / 100))) if side == 'buy' else entry_price * (1 + (i * (stop_loss_pct / 100)))
            stop_loss_amount = max(float(contract_size / levels_stp), 1)  # Ensure minimum contract size is 1
            stop_loss_order = exchange.create_order(
                symbol,
                type='limit',
                side='sell' if side == 'buy' else 'buy',
                amount=stop_loss_amount,
                price=level_stop_price,
                params={
                    'leverage': leverage,
                    'stop': 'down' if side == 'buy' else 'up',
                    'stopPriceType': 'TP',
                    'stopPrice': level_stop_price
                }
            )
            orders_ids.append(stop_loss_order['id'])
            stop_loss_prices.append(level_stop_price)
            print(f"Stop-loss order {i} placed successfully! SL: {level_stop_price}")
            time.sleep(3)

        # Set the take-profit orders
        for i in range(1, levels + 1):
            level_take_profit_price = entry_price * (1 + (i * (take_profit_pct / 100))) if side == 'buy' else entry_price * (1 - (i * (take_profit_pct / 100)))
            take_profit_amount = max(float(contract_size / levels), 1)  # Ensure minimum contract size is 1
            take_profit_order = exchange.create_order(
                symbol,
                type='limit',
                side='sell' if side == 'buy' else 'buy',
                amount=take_profit_amount,
                price=level_take_profit_price,
                params={
                    'leverage': leverage,
                    'takeProfitPrice': level_take_profit_price,
                }
            )
            orders_ids.append(take_profit_order['id'])
            take_profit_prices.append(level_take_profit_price)
            print(f"Take-profit order {i} placed successfully! TP: {level_take_profit_price}")
            time.sleep(3)

        return stop_loss_prices, take_profit_prices, orders_ids
    except ccxt.BaseError as e:
        print(f"Error placing orders for {symbol}: {e}")
        return None, None

def calculate_usdt_profit(data):
    """
    Calculate the total profit (realised + unrealised) in USDT.

    Parameters:
    data (dict): A dictionary containing position data, including realisedPnl, unrealisedPnl, and markPrice.

    Returns:
    dict: A dictionary containing realised PnL in USDT, unrealised PnL in USDT, and total PnL in USDT.
    """
    # Extract required values
    realised_pnl_xbt = data.get("realisedPnl", 0)
    unrealised_pnl_xbt = data.get("unrealisedPnl", 0)
    mark_price = data.get("markPrice", 0)

    # Ensure mark price is valid
    if mark_price <= 0:
        raise ValueError("Invalid mark price. Must be greater than 0.")

    # Convert XBT to USDT
    realised_pnl_usdt = realised_pnl_xbt * mark_price
    unrealised_pnl_usdt = unrealised_pnl_xbt * mark_price

    # Calculate total PnL in USDT
    total_pnl_usdt = realised_pnl_usdt + unrealised_pnl_usdt

    return {
        "realised_pnl_usdt": realised_pnl_usdt,
        "unrealised_pnl_usdt": unrealised_pnl_usdt,
        "total_pnl_usdt": total_pnl_usdt
    }

def get_order_book(symbol):
    return exchange.fetch_order_book(symbol)

def place_limit_order(symbol, side, amount, price):
    return exchange.create_limit_order(symbol, side, amount, price)

def cancel_order(order_id, symbol):
    return exchange.cancel_order(order_id, symbol)

def order_filled(order_id, symbol):
    order = exchange.fetch_order(order_id, symbol)
    return order['status'] == 'closed'

def entertain_order_book(symbol, amount, side='buy', price_tolerance=0.01):
    """
    Place a limit order and adjust it dynamically based on the order book.

    :param symbol: Trading pair symbol, e.g., 'BTC/USDT'
    :param amount: Amount to trade
    :param side: 'buy' or 'sell'
    :param price_tolerance: Tolerance for price adjustment, default is 1% (0.01)
    """
    order_book = get_order_book(symbol)
    price = order_book['bids'][0][0] if side == 'buy' else order_book['asks'][0][0]
    order = place_limit_order(symbol, side, amount, price)

    print(f"Placed {side} order with ID {order['id']} at price {price}")

    while not order_filled(order['id'], symbol):
        
        time.sleep(10)  # Wait for 10 seconds before checking again
        order_book = get_order_book(symbol)
        new_price = order_book['bids'][0][0] if side == 'buy' else order_book['asks'][0][0]

        # Check if the price exceeds the tolerance
        if side == 'buy' and new_price > price * (1 + price_tolerance):
            print(f"Cancelling order {order['id']} due to price increase. New price: {new_price}")
            cancel_order(order['id'], symbol)
            price = new_price
            order = place_limit_order(symbol, side, amount, price)
            print(f"Replaced with new order {order['id']} at price {price}")
        elif side == 'sell' and new_price < price * (1 - price_tolerance):
            print(f"Cancelling order {order['id']} due to price drop. New price: {new_price}")
            cancel_order(order['id'], symbol)
            price = new_price
            order = place_limit_order(symbol, side, amount, price)
            print(f"Replaced with new order {order['id']} at price {price}")

    print(f"Order {order['id']} filled at price {price}")


#### Cancelling all related orders
import psutil
from pywinauto import Desktop


def karbit(qid, symbol, order_type, leverage, closetime, entry_price, amount, target_profit_pct, trailing_stop_pct, trailing_profit_pct):
    
    def place_and_retry_order_kucoin(exchange, symbol, side, total_amount, start_price, leverage, retries=3):
        """
        Place a limit order on KuCoin, retrying with decreasing percentages (-1%, -2%, -3%) 
        if the order is not fully filled. Ensure the entire amount is filled.
        
        Args:
            exchange: The exchange object for making API calls.
            symbol: The trading pair symbol (e.g., 'BTC/USDT').
            side: 'buy' or 'sell'.
            total_amount: Total amount to trade.
            start_price: Initial price for the limit order.
            leverage: Leverage to use for the order.
            retries: Number of retries with decreasing price (default is 3).
        """
        remaining_amount = total_amount  # Track the remaining amount to fill
        current_price = start_price  # Initialize with the starting price
        retries=3
        for retry in range(1, retries + 1):
            # Place the limit order
            order = exchange.create_order(
                symbol=symbol,
                type='limit',
                side=side,
                amount=remaining_amount,
                price=current_price,
                params={
                    'leverage': leverage,
                }
            )
            print(f"Placed order at {current_price:.2f} USDT with amount {remaining_amount:.6f} ({retry}/{retries})")

            # Check the order status
            while True:
                order_status = exchange.fetch_order(order['id'], symbol)
                filled_amount = float(order_status['filled'])
                status = order_status['status']

                if status == 'closed':  # Order fully filled
                    print(f"Order fully filled at {current_price:.2f}.")
                    return True

                elif status == 'canceled':  # Order was canceled
                    print(f"Order at {current_price:.2f} was canceled. Retrying...")
                    break

                elif status in ['open', 'partially_filled']:  # Order is still active
                    if filled_amount > 0:
                        print(f"Partially filled {filled_amount:.6f} at {current_price:.2f}.")
                        remaining_amount -= filled_amount
                        exchange.cancel_order(order['id'], symbol)
                        break

            # Decrease price by 1% for the next retry
            current_price *= 0.99  # Reduce by 1%
            if retry == retries:
                print("Max retries reached. Order could not be fully filled.")
                return False

        return False

    if entry_price is None:
        print("Error: entry_price cannot be None.")
        return

    try:
        entry_price = float(entry_price)
    except ValueError:
        print("Error: entry_price must be a valid number.")
        return

    contract_size = math.ceil(get_contract_size(exchange, amount, symbol, leverage))
    if not contract_size:
        print("Failed to calculate contract size.")
        return

    side = 'buy' if order_type.lower() == 'buy' else 'sell'

    ticker = exchange.fetch_ticker(symbol)
    entrying_price = float(ticker['last'])
    leverage = float(leverage)
    target_stop_pct = float(2.5)
    target_profit_pct = float(5)

    if order_type == 'buy':
        stop_loss = entrying_price * (1 - (target_stop_pct / 100))
        take_profit = entrying_price * (1 + (target_profit_pct / 100))
    elif order_type == 'sell':
        stop_loss = entrying_price * (1 + (target_stop_pct / 100))
        take_profit = entrying_price * (1 - (target_profit_pct / 100))

    tehran_time_open = datetime.now(tehran_tz)
    opentime = tehran_time_open.strftime('%Y-%m-%d %H:%M:%S')

    stopalltime = calculate_closetime(tehran_time_open, closetime)
    main_order = create_main(exchange, symbol, side, leverage, contract_size)
    xgbooti = main_order['id']

    if main_order and main_order['id']:
        order_id = main_order['id']
        print(f"Main order placed successfully! Order ID: {order_id}")
        print(Fore.YELLOW + "TP: " + str(take_profit) + " SL: " + str(stop_loss) + Style.RESET_ALL)

        take_profit_pct = int(target_profit_pct)
        stop_loss_pct = int(target_stop_pct)

        # Set multiple stop-loss and take-profit orders
        # Place the stop-loss and take-profit orders
        stop_loss_prices, take_profit_prices, orders_ids = set_multiple_stop_loss_take_profit(exchange, symbol, side, leverage, contract_size, entry_price, stop_loss_pct, take_profit_pct)

        if stop_loss_prices and take_profit_prices:
            print(Fore.RED + f"Stop-loss prices: {stop_loss_prices}" + Style.RESET_ALL)
            print(Fore.GREEN + f"Take-profit prices: {take_profit_prices}" + Style.RESET_ALL)

        opentime = datetime.now()  # Example current time
        
        current_price = 0
        pnl = 0
        pxl = 0

        type_of_exit = ""
        ticker = exchange.fetch_ticker(symbol)
        safety_margin = ticker['last']
        while True:
            ### Ensure that position is still exist or not
            symbolku = symbol.replace("/USDT:USDT", "USDTM").replace("BTC", "XBT")
            position_info = get_position_details(symbolku)
            if position_info['isOpen'] != False:
                try:
                    ticker = exchange.fetch_ticker(symbol)
                    current_price = ticker['last']
                    tehran_time = datetime.now(tehran_tz)
                    if current_price:
                        position_info = get_position_details(symbolku)
                        profit = calculate_usdt_profit(position_info)
                        #realised_pnl_usdt = profit["realised_pnl_usdt"]
                        #unrealised_pnl_usdt = profit["unrealised_pnl_usdt"]
                        total_pnl_usdt = profit["total_pnl_usdt"]
                        
                        leverage = float(position_info['leverage']) # Leverage multiplier

                        # Calculate Profit or Loss (PnL) with leverage
                        pnl = (current_price - entry_price) * leverage
                        
                        # Calculate Percentage Change (PxL) with leverage
                        if entry_price != 0:  # Avoid division by zero
                            pxl = ((current_price - entry_price) / entry_price) * leverage
                        else:
                            pxl = 0  # Handle division by zero appropriately

                        # Define colors
                        GREEN = '\033[92m'  # Bright Green
                        RED = '\033[91m'    # Bright Red
                        RESET = '\033[0m'   # Reset to default color

                        # Determine the color for PnL and PxL
                        pnl_color = GREEN if pnl > 0 else RED
                        pxl_color = GREEN if pxl > 0 else RED

                        # Print the results with appropriate colors
                        print(f"PnL: {pnl_color}{pnl:.2f}{RESET}")
                        print(f"Percentage Change: {pxl_color}{pxl:.2f}%{RESET}")
                        display_position_info(stop_loss, take_profit, symbol, order_type, entry_price, current_price, contract_size, leverage, trailing_stop_pct, trailing_profit_pct)
                        
                        if (len(take_profit_prices) == 0 or len(stop_loss_prices) == 0):
                            print("It seems no targets to reach")
                            break
                        
                        if opentime > (opentime + timedelta(minutes=30)):
                            position_info = get_position_details(symbolku)
                            terminate_all_positions_and_orders(position_info['symbol'])
                            print("Position ended with 30+ time")
                            #terminate_process()
                            break

                        
                        ### PUMP AND DUMP PROTECTION 5%
                        if ((float(safety_margin) / float(current_price)) > 1.05) or ((float(safety_margin) / float(current_price)) < 0.95):
                            terminate_all_positions_and_orders(position_info['symbol'])
                            print("PUMP AND DUMP HAPPENS, GET RID OF THIS ORDER")
                            break
                        #if float(pxl) > int(5): 
                        #    entertain_order_book(symbol, amount, side='buy', price_tolerance=0.01)

                        #if float(pxl) < float(-3):
                        #    entertain_order_book(symbol, amount, side='buy', price_tolerance=0.01)

                        
                        orders = []  # List to store details of executed orders

                        if side == 'buy':
                            if float(current_price) >= float(min(take_profit_prices)):
                                print(f"Take profit reached at {current_price}. Closing buy order.")
                                closetime = tehran_time.strftime('%Y-%m-%d %H:%M:%S')
                                type_of_exit = "Takeprofit"

                                # Log the closed order
                                orders.append({
                                    "side": "buy",
                                    "exit_price": current_price,
                                    "exit_type": type_of_exit,
                                    "close_time": closetime
                                })

                                print("Position closed at take profit")
                                take_profit_prices.remove(min(take_profit_prices))
                                continue

                            elif float(current_price) <= float(max(stop_loss_prices)):
                                print(f"Stop loss triggered at {current_price}. Closing buy order.")
                                closetime = tehran_time.strftime('%Y-%m-%d %H:%M:%S')
                                type_of_exit = "Stoploss"
                                
                                # Log the closed order
                                orders.append({
                                    "side": "buy",
                                    "exit_price": current_price,
                                    "exit_type": type_of_exit,
                                    "close_time": closetime
                                })

                                print("Position closed at stop loss")
                                stop_loss_prices.remove(max(stop_loss_prices))
                                continue

                        elif side == 'sell':  # For sell orders
                            if float(current_price) <= float(min(take_profit_prices)):
                                print(f"Take profit reached at {current_price}. Closing sell order.")
                                closetime = tehran_time.strftime('%Y-%m-%d %H:%M:%S')
                                type_of_exit = "Takeprofit"

                                # Log the closed order
                                orders.append({
                                    "side": "sell",
                                    "exit_price": current_price,
                                    "exit_type": type_of_exit,
                                    "close_time": closetime
                                })

                                print("Position closed at take profit")
                                take_profit_prices.remove(min(take_profit_prices))
                                continue

                            elif float(current_price) >= float(max(stop_loss_prices)):
                                print(f"Stop loss triggered at {current_price}. Closing sell order.")
                                closetime = tehran_time.strftime('%Y-%m-%d %H:%M:%S')
                                type_of_exit = "Stoploss"
                                
                                # Log the closed order
                                orders.append({
                                    "side": "sell",
                                    "exit_price": current_price,
                                    "exit_type": type_of_exit,
                                    "close_time": closetime
                                })

                                print("Position closed at stop loss")
                                stop_loss_prices.remove(max(stop_loss_prices))
                                continue
                        
                        
                    safety_margin = current_price
                    time.sleep(20)

                except (ValueError, TypeError) as e:
                    print(f"Error calculating position details: {e}")
                    continue
                except ccxt.BaseError as e:
                    print(f"Error fetching ticker for {symbol}: {e}")
                    continue
            else:
                print("The position is no longer active now or closed by user")
                break



            # Find matching windows
        log_trade_details(qid, symbol, entry_price, leverage, main_order, stop_loss, take_profit, main_order['id'])
        data = {
        "qid": str(qid),
        "entrying_price": str(entrying_price),
        "leverage": str(leverage),
        "target_stop_pct": str(target_stop_pct),
        "target_profit_pct": str(target_profit_pct),
        "order_type": str(order_type),
        "opentime": str(opentime),
        "xgbooti": str(json.dumps(xgbooti)),
        "pnl" : str(pnl),
        "pxl" : str(pxl),
        "stop_loss_prices" : str(stop_loss_prices),
        "take_profit_prices" : str(take_profit_prices),
        "current_price" : str(current_price),
        "orders_ids" : str(orders_ids),
        "take_profit": str(take_profit),
        "stop_loss": str(stop_loss),
        "symbolku": str(symbolku),
        "position_info": str(json.dumps(position_info)),
        "type_of_exit": str(type_of_exit),
        "closetime": str(closetime),
        "contract_size" : str(contract_size),
        }
        save_trading_record(data)
        smsquant = round(abs(float(total_pnl_usdt)), 2)
        sendsms = send_sms_otp(smsquant, symbolku, type_of_exit)
        print(sendsms)

        # Find matching windows
        matching_windows = find_windows_by_title("AVAX")
        if not matching_windows:
            print("No matching CMD processes found.")
            return

        print("Found matching windows:")
        for pid, title in matching_windows:
            print(f"PID: {pid}, Title: {title}")

        # Kill matching CMD processes
        for pid, title in matching_windows:
            try:
                process = psutil.Process(pid)
                print(f"Terminating process with PID: {pid}, Title: {title}")
                process.terminate()
                process.wait(timeout=5)  # Wait for process termination
                print(f"Successfully terminated process with PID: {pid}")
            except psutil.NoSuchProcess:
                print(f"Process with PID {pid} does not exist (already terminated).")
            except psutil.AccessDenied:
                print(f"Access denied to terminate process with PID {pid}.")
            except psutil.TimeoutExpired:
                print(f"Timeout while waiting for process {pid} to terminate.")
            except Exception as e:
                print(f"Error terminating process with PID {pid}: {e}")

        
        #terminate_all_positions_and_orders(position_info['symbol'])
    
    

def main(qid, symbol, entry_price, ordertype, deflev, closetime, amount, target_profit_pct, trailing_stop_pct, trailing_profit_pct):
    karbit(qid, symbol, ordertype, deflev, closetime, entry_price, amount, target_profit_pct, trailing_stop_pct, trailing_profit_pct)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run trading script with specified parameters.")
    parser.add_argument("--qid", type=str, required=True, help="The id of the database for the trade.")
    parser.add_argument("--symbol", required=True, help="The trading symbol (e.g., BTCUSDT).")
    parser.add_argument("--entry_price", type=str, required=True, help="The entry price for the trade.")
    parser.add_argument("--ordertype", type=str, required=True, help="Technical analysis input (buy or sell).")
    parser.add_argument("--deflev", type=str, required=True, help="The leverage for the trade.")
    parser.add_argument("--closetime", type=str, required=True, help="The closetime for the trade.")
    parser.add_argument("--amount", type=str, required=True, help="The amount for the trade.")
    parser.add_argument("--target_profit_pct", type=str, required=True, help="The target profit percentage.")
    parser.add_argument("--trailing_stop_pct", type=str, required=True, help="The trailing stop percentage.")
    parser.add_argument("--trailing_profit_pct", type=str, required=True, help="The trailing take-profit percentage.")
    
    args = parser.parse_args()

    main(args.qid, args.symbol, args.entry_price, args.ordertype, args.deflev, args.closetime, args.amount, args.target_profit_pct, args.trailing_stop_pct, args.trailing_profit_pct)