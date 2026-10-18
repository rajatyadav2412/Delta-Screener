"""
SQLite Database Storage for Historical CHoCH Setups
===================================================
Tracks every signal detected over time so traders can see
exact history: when, what instrument, price, broken level, and 10-day anchor.
"""

import sqlite3
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

DB_PATH = os.path.join(os.path.dirname(__file__), "signals_history.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the historical signals table and indexes."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unique_key TEXT UNIQUE,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                direction INTEGER NOT NULL,
                candle_time TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                broken_pivot_label TEXT,
                broken_wick_level REAL,
                macro_anchor_label TEXT,
                macro_anchor_price REAL,
                bos_type TEXT,
                bos_time TEXT,
                price_at_signal REAL,
                current_price REAL,
                timeframe TEXT DEFAULT '4h',
                bars_gap INTEGER
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON signals(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_candle_time ON signals(candle_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signal_type ON signals(signal_type)")
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        # Read-only database on serverless platforms
        pass


def save_signal(
    symbol: str,
    signal_type: str,
    direction: int,
    candle_time: str,
    broken_pivot_label: str,
    broken_wick_level: float,
    macro_anchor_label: str,
    macro_anchor_price: float,
    bos_type: str,
    bos_time: str,
    price_at_signal: float,
    current_price: float,
    bars_gap: int = 0,
    timeframe: str = "4h"
) -> bool:
    """
    Save a detected signal. Returns True if new, False if already existed.
    """
    unique_key = f"{symbol}|{signal_type}|{candle_time}|{broken_wick_level:.6f}"
    detected_at = datetime.now(IST).strftime("%Y-%m-%d %I:%M:%S %p IST")

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO signals (
                unique_key, symbol, signal_type, direction, candle_time,
                detected_at, broken_pivot_label, broken_wick_level,
                macro_anchor_label, macro_anchor_price, bos_type, bos_time,
                price_at_signal, current_price, timeframe, bars_gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            unique_key, symbol, signal_type, direction, candle_time,
            detected_at, broken_pivot_label, broken_wick_level,
            macro_anchor_label, macro_anchor_price, bos_type, bos_time,
            price_at_signal, current_price, timeframe, bars_gap
        ))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        # Already recorded
        return False
    except sqlite3.OperationalError:
        # Read-only database on serverless platforms
        return False


def get_history(
    limit: int = 100,
    offset: int = 0,
    symbol_filter: Optional[str] = None,
    type_filter: Optional[str] = None
) -> List[Dict]:
    """Retrieve historical signals with optional filtering and pagination."""
    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT * FROM signals WHERE 1=1"
    params = []

    if symbol_filter:
        query += " AND symbol LIKE ?"
        params.append(f"%{symbol_filter.upper()}%")

    if type_filter and type_filter != "ALL":
        query += " AND signal_type = ?"
        params.append(type_filter)

    query += " ORDER BY candle_time DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_stats() -> Dict:
    """Get high-level summary counts."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM signals")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM signals WHERE direction = 1")
    bullish = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM signals WHERE direction = -1")
    bearish = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT symbol) FROM signals")
    unique_symbols = cursor.fetchone()[0]

    conn.close()
    return {
        "total_signals": total,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "unique_symbols": unique_symbols,
    }


# Initialize DB on module import
init_db()
