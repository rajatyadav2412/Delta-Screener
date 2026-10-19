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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        # Read-only database on serverless platforms
        pass


def set_metadata(key: str, value: str):
    """Store or update a key-value pair in the metadata table."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_metadata(key: str) -> Optional[str]:
    """Retrieve a value by key from the metadata table."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


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

        # Update last_scan_time in metadata
        display_time = datetime.now(IST).strftime("%d %b, %I:%M %p IST")
        try:
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_scan_time', ?)", (display_time,))
        except Exception:
            pass

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
    """Get high-level summary counts and last scan time."""
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

    last_scan_time = None
    try:
        cursor.execute("SELECT value FROM metadata WHERE key = 'last_scan_time'")
        row = cursor.fetchone()
        if row and row[0]:
            last_scan_time = row[0]
    except Exception:
        pass

    if not last_scan_time:
        try:
            cursor.execute("SELECT detected_at FROM signals ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row and row[0]:
                raw = str(row[0]).strip()
                try:
                    parts = raw.split(" ")
                    if len(parts) >= 4:
                        dt = datetime.strptime(parts[0], "%Y-%m-%d")
                        time_part = f"{parts[1]} {parts[2]} IST"
                        last_scan_time = f"{dt.strftime('%d %b')}, {time_part}"
                    else:
                        last_scan_time = raw
                except Exception:
                    last_scan_time = raw
        except Exception:
            pass

    conn.close()
    return {
        "total_signals": total,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "unique_symbols": unique_symbols,
        "last_scan_time": last_scan_time,
    }


# Initialize DB on module import
init_db()
