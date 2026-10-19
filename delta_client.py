"""
Delta Exchange API Client
Handles fetching instruments and OHLC candle data from Delta Exchange.
"""

import time
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


class DeltaExchangeClient:
    """Client for interacting with Delta Exchange REST API."""

    def __init__(self, api_key: str, base_url: str = "https://api.india.delta.exchange"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "api-key": self.api_key,
            "Content-Type": "application/json",
        })

    def fetch_instruments(self, contract_types: Optional[list] = None) -> list:
        """
        Fetch all available trading instruments/products.

        Args:
            contract_types: Filter by contract types e.g. ["perpetual_futures"]

        Returns:
            List of instrument dicts with keys: id, symbol, contract_type, etc.
        """
        endpoint = f"{self.base_url}/v2/products"

        try:
            response = self.session.get(endpoint, timeout=30)
            response.raise_for_status()
            data = response.json()
            products = data.get("result", [])

            # Filter by contract type if specified
            if contract_types:
                products = [
                    p for p in products
                    if p.get("contract_type") in contract_types
                ]

            # Filter only active/live products
            products = [
                p for p in products
                if p.get("state", "").lower() == "live"
            ]

            return products

        except requests.exceptions.RequestException as e:
            print(f"  [ERROR] Failed to fetch instruments: {e}")
            return []

    def fetch_candles(
        self,
        symbol: str,
        resolution: str = "4h",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch OHLC candle data for a given symbol.

        Args:
            symbol: Trading pair symbol e.g. "BTCUSD"
            resolution: Candle timeframe - "1m","5m","15m","1h","4h","1d" etc.
            start_time: Start epoch timestamp in seconds
            end_time: End epoch timestamp in seconds

        Returns:
            DataFrame with columns: time, open, high, low, close, volume
        """
        endpoint = f"{self.base_url}/v2/history/candles"

        params = {
            "symbol": symbol,
            "resolution": resolution,
        }

        if start_time:
            params["start"] = start_time
        if end_time:
            params["end"] = end_time

        try:
            response = self.session.get(endpoint, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            candles = data.get("result", [])
        except requests.exceptions.RequestException as e:
            if "india" in self.base_url:
                try:
                    fb_res = self.session.get("https://api.delta.exchange/v2/history/candles", params=params, timeout=8)
                    candles = fb_res.json().get("result", []) if fb_res.ok else []
                except Exception:
                    candles = []
            else:
                candles = []
            if not candles:
                print(f"  [ERROR] Failed to fetch candles for {symbol}: {e}")
                return pd.DataFrame()

        if not candles:
            return pd.DataFrame()

        # Delta Exchange returns candles as dicts with keys:
        # close, high, low, open, time, volume
        df = pd.DataFrame(candles)

        # Ensure required columns exist
        required = ["time", "open", "high", "low", "close"]
        if not all(col in df.columns for col in required):
            return pd.DataFrame()

        # Sort chronologically (API may return newest first)
        df = df.sort_values("time").reset_index(drop=True)

        # Convert to numeric
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["time"] = pd.to_datetime(df["time"], unit="s").dt.tz_localize("UTC").dt.tz_convert(IST)

        return df

    def fetch_candles_paginated(
        self,
        symbol: str,
        resolution: str = "4h",
        num_candles: int = 500,
    ) -> pd.DataFrame:
        """
        Fetch enough candles by paginating backward from current time.
        Delta Exchange limits to ~2000 candles per request.

        Args:
            symbol: Trading pair symbol
            resolution: Candle timeframe
            num_candles: Approx number of candles to fetch

        Returns:
            DataFrame with OHLC data
        """
        # Map resolution to seconds for pagination
        resolution_map = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
            "1d": 86400, "7d": 604800, "1w": 604800,
        }

        interval_seconds = resolution_map.get(resolution, 14400)  # Default 4h
        end_time = int(time.time())
        start_time = end_time - (num_candles * interval_seconds)

        return self.fetch_candles(
            symbol=symbol,
            resolution=resolution,
            start_time=start_time,
            end_time=end_time,
        )
