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
import pandas as pd
from ta.volatility import AverageTrueRange
from ta.momentum import RSIIndicator
from tradingview_ta import TA_Handler, Interval
import ssl
from database import log_trade, log_order, update_trade
from trade import prepare_headers, select_top_5_pumping_coins, transform_symbol_for_ta, get_base_currency, get_current_price, get_klines, calculate_indicators, get_market_condition, get_news_sentiment

API_KEY = os.getenv("KUCOIN_API_KEY", "67c9d31345e41a0001676a6f")
API_SECRET = os.getenv("KUCOIN_API_SECRET", "7b93d018-c606-40db-b8d0-b4bee166eef1")
API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "8529118#Ahe")

# Helper Functions
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
        global id_to_symbol
        id_to_symbol = {market['id']: symbol for symbol, market in exchange.markets.items()}
        logging.info(f"Symbol mappings: {id_to_symbol}")
        return exchange
    except Exception as e:
        logging.error(f"Error initializing KuCoin: {e}")
        return None


import logging
import ccxt
from trade import get_klines, calculate_indicators, get_current_price, get_news_sentiment
from database import log_trade, log_order

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def bullish_scalping(exchange, conn, symbol, investment_amount=10.0, leverage=10):
    """Execute a bullish scalping strategy."""
    df = get_klines(exchange, symbol)
    if df is None or df.empty:
        logging.warning(f"No kline data for {symbol}. Skipping bullish scalping.")
        return
    indicators = calculate_indicators(df)
    if not indicators:
        logging.warning(f"Failed to calculate indicators for {symbol}.")
        return
    price = get_current_price(exchange, symbol)
    if not price:
        logging.warning(f"No current price for {symbol}.")
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
            tp_multiplier = 3 if sentiment == "positive" else 2
            target_price = price + (indicators['atr'] * tp_multiplier)
            stop_price = price - (indicators['atr'] * 1.5)
            tp_order = exchange.create_limit_sell_order(symbol, size, target_price, {'reduceOnly': True})
            log_order(conn, tp_order['id'], trade_id, "limit", target_price, size)
            sl_order = exchange.create_order(symbol, 'market', 'sell', size, None,
                                             {'stop': 'loss', 'stopPrice': stop_price, 'reduceOnly': True})
            log_order(conn, sl_order['id'], trade_id, "stop_market", stop_price, size)
            logging.info(f"Bullish scalping trade opened for {symbol}: Price={price}, Size={size}")
    except ccxt.BaseError as e:
        logging.error(f"Error in bullish scalping for {symbol}: {e}")

def bearish_short_scalping(exchange, conn, symbol, investment_amount=10.0, leverage=10):
    """Execute a bearish short scalping strategy."""
    df = get_klines(exchange, symbol)
    if df is None or df.empty:
        logging.warning(f"No kline data for {symbol}. Skipping bearish scalping.")
        return
    indicators = calculate_indicators(df)
    if not indicators:
        logging.warning(f"Failed to calculate indicators for {symbol}.")
        return
    price = get_current_price(exchange, symbol)
    if not price:
        logging.warning(f"No current price for {symbol}.")
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
            tp_multiplier = 2 if sentiment == "negative" else 1.5
            target_price = price - (indicators['atr'] * tp_multiplier)
            stop_price = price + (indicators['atr'] * 1.5)
            tp_order = exchange.create_limit_buy_order(symbol, size, target_price, {'reduceOnly': True})
            log_order(conn, tp_order['id'], trade_id, "limit", target_price, size)
            sl_order = exchange.create_order(symbol, 'market', 'buy', size, None,
                                             {'stop': 'loss', 'stopPrice': stop_price, 'reduceOnly': True})
            log_order(conn, sl_order['id'], trade_id, "stop_market", stop_price, size)
            logging.info(f"Bearish short scalping trade opened for {symbol}: Price={price}, Size={size}")
    except ccxt.BaseError as e:
        logging.error(f"Error in bearish short scalping for {symbol}: {e}")

def mean_reversion_trading(exchange, conn, symbol, investment_amount=10.0, leverage=10):
    """Execute a mean reversion strategy."""
    df = get_klines(exchange, symbol)
    if df is None or df.empty:
        logging.warning(f"No kline data for {symbol}. Skipping mean reversion.")
        return
    indicators = calculate_indicators(df)
    if not indicators:
        logging.warning(f"Failed to calculate indicators for {symbol}.")
        return
    price = get_current_price(exchange, symbol)
    if not price:
        logging.warning(f"No current price for {symbol}.")
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
                logging.info(f"Mean reversion buy trade opened for {symbol}: Price={price}, Size={size}")
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
                logging.info(f"Mean reversion sell trade opened for {symbol}: Price={price}, Size={size}")
    except ccxt.BaseError as e:
        logging.error(f"Error in mean reversion for {symbol}: {e}")