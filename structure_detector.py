"""
Market Structure Detector — TradingView Pine Script Exact Replication
======================================================================
Length: 3 candles left / 3 candles right (leftLen=3, rightLen=3)
Macro Filter: 10 Days High / Low Rule

Logic:
1. Pivot High (PH): Candle whose High is strictly higher than 3 bars before
   and >= 3 bars after it.
2. Pivot Low (PL): Candle whose Low is strictly lower than 3 bars before
   and <= 3 bars after it.

Market Structure & Trend:
- Consecutive Pivot Highs:
    * Current PH > Previous PH -> HH (Higher High)
    * Current PH < Previous PH -> LH (Lower High)
- Consecutive Pivot Lows:
    * Current PL > Previous PL -> HL (Higher Low)
    * Current PL < Previous PL -> LL (Lower Low)

10-Day High / Low Rule for Clean CHoCH:
- Bearish CHoCH (Uptrend Reversal):
    Only indicated when the preceding Higher High (HH) price was GREATER
    than the 10-day high (exceeded the rolling 10-day highest price).
    Then, 4H candle closes strictly below the Higher Low (HL) low wick.
- Bullish CHoCH (Downtrend Reversal):
    Only indicated when the preceding Lower Low (LL) price was LOWER
    than the 10-day low (exceeded the rolling 10-day lowest price).
    Then, 4H candle closes strictly above the Lower High (LH) high wick.
"""

import time
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List, Dict


@dataclass
class PivotPoint:
    """Represents a pivot point detected via ta.pivothigh / ta.pivotlow."""
    bar_index: int           # Index where the pivot candle occurred
    confirm_index: int       # Index where pivot is confirmed (bar_index + right_len)
    pivot_type: str          # 'PH' (Pivot High) or 'PL' (Pivot Low)
    price: float             # Pivot level (high for PH, low for PL)
    candle_high: float       # Full high wick of the pivot candle
    candle_low: float        # Full low wick of the pivot candle
    candle_open: float       # Open of pivot candle
    candle_close: float      # Close of pivot candle
    label: str = ""          # 'HH', 'HL', 'LH', or 'LL'
    is_macro_level: bool = False  # True if HH > 10d high or LL < 10d low
    macro_price: float = 0.0      # The 10-day high/low price it exceeded
    time: Optional[object] = None

    def __repr__(self):
        t = str(self.time)[:16] if self.time else f"idx={self.bar_index}"
        macro_str = " (10D EXT)" if self.is_macro_level else ""
        return f"{self.label or self.pivot_type}({self.price:.4f}{macro_str}) @ {t}"


@dataclass
class StructureEvent:
    """Represents a BOS or CHoCH break."""
    bar_index: int           # Bar where the break occurred
    event_type: str          # 'BOS' or 'CHoCH'
    direction: int           # 1 = Bullish, -1 = Bearish
    level: float             # Level of the broken pivot
    broken_pivot: PivotPoint # The pivot point that was broken
    is_clean: bool           # True if candle closed beyond the entire wick
    candle_close: float      # Close price of the breaking candle
    macro_anchor: Optional[PivotPoint] = None  # The qualifying 10-day HH or LL
    time: Optional[object] = None

    @property
    def broken_swing(self):
        return self.broken_pivot

    @property
    def index(self):
        return self.bar_index

    @property
    def swing_point(self):
        return self.broken_pivot

    def __repr__(self):
        dir_str = "BULL ↑" if self.direction == 1 else "BEAR ↓"
        clean_str = " CLEAN ✓" if self.is_clean else " WICK"
        lbl = self.broken_pivot.label if self.broken_pivot else "PV"
        t = str(self.time)[:16] if self.time else f"idx={self.bar_index}"
        anchor_info = ""
        if self.macro_anchor:
            anchor_lbl = self.macro_anchor.label
            anchor_info = f" [from 10D {anchor_lbl}: {self.macro_anchor.price:.4f}]"
        return f"{self.event_type}({dir_str}{clean_str}) broke {lbl}({self.level:.4f}) @ {t}{anchor_info}"


@dataclass
class CHoCHAfterBOS:
    """A clean CHoCH that formed after a confirmed BOS."""
    bos_event: StructureEvent
    choch_event: StructureEvent
    bars_between: int
    symbol: str = ""

    def __repr__(self):
        bos_dir = "BULL ↑" if self.bos_event.direction == 1 else "BEAR ↓"
        choch_dir = "BULL ↑" if self.choch_event.direction == 1 else "BEAR ↓"
        anchor = ""
        if self.choch_event.macro_anchor:
            anchor = f" [10D {self.choch_event.macro_anchor.label}: {self.choch_event.macro_anchor.price:.4f}]"
        return (
            f"[{self.symbol}] BOS({bos_dir}) broke {self.bos_event.broken_pivot.label} -> "
            f"CHoCH({choch_dir} CLEAN ✓) broke {self.choch_event.broken_pivot.label}{anchor} | "
            f"{self.bars_between} bars apart"
        )


class TradingViewPivotDetector:
    """
    Implements TradingView's ta.pivothigh / ta.pivotlow logic (length=3)
    with the 10-day High / Low macro filter for high-probability CHoCH signals.
    """

    def __init__(
        self,
        left_len_h: int = 3,
        right_len_h: int = 3,
        left_len_l: int = 3,
        right_len_l: int = 3,
        swing_length: Optional[int] = None,
        macro_lookback_days: int = 10,
    ):
        if swing_length is not None:
            left_len_h = right_len_h = left_len_l = right_len_l = swing_length
        self.left_len_h = left_len_h
        self.right_len_h = right_len_h
        self.left_len_l = left_len_l
        self.right_len_l = right_len_l
        self.swing_length = left_len_h
        self.macro_lookback_days = macro_lookback_days

    # ──────────────────────────────────────────────────────────────
    # STEP 1: Exact ta.pivothigh and ta.pivotlow replication (length=3)
    # ──────────────────────────────────────────────────────────────
    def find_pivots(self, ohlc: pd.DataFrame) -> List[PivotPoint]:
        """
        Calculates Pivot Highs and Pivot Lows identical to TradingView Pine Script:
            ph = ta.pivothigh(3, 3)
            pl = ta.pivotlow(3, 3)
        """
        highs = ohlc["high"].values
        lows = ohlc["low"].values
        opens = ohlc["open"].values
        closes = ohlc["close"].values
        times = ohlc["time"].values if "time" in ohlc.columns else [None] * len(ohlc)
        n = len(ohlc)

        pivots: List[PivotPoint] = []

        # Find Pivot Highs
        for p in range(self.left_len_h, n - self.right_len_h):
            val = highs[p]
            if np.all(val > highs[p - self.left_len_h : p]) and np.all(
                val >= highs[p + 1 : p + self.right_len_h + 1]
            ):
                pivots.append(
                    PivotPoint(
                        bar_index=p,
                        confirm_index=p + self.right_len_h,
                        pivot_type="PH",
                        price=float(val),
                        candle_high=float(highs[p]),
                        candle_low=float(lows[p]),
                        candle_open=float(opens[p]),
                        candle_close=float(closes[p]),
                        time=times[p],
                    )
                )

        # Find Pivot Lows
        for p in range(self.left_len_l, n - self.right_len_l):
            val = lows[p]
            if np.all(val < lows[p - self.left_len_l : p]) and np.all(
                val <= lows[p + 1 : p + self.right_len_l + 1]
            ):
                pivots.append(
                    PivotPoint(
                        bar_index=p,
                        confirm_index=p + self.right_len_l,
                        pivot_type="PL",
                        price=float(val),
                        candle_high=float(highs[p]),
                        candle_low=float(lows[p]),
                        candle_open=float(opens[p]),
                        candle_close=float(closes[p]),
                        time=times[p],
                    )
                )

        pivots.sort(key=lambda x: x.bar_index)
        return pivots

    # ──────────────────────────────────────────────────────────────
    # STEP 2: Label HH, LH, HL, LL & 10-Day High/Low Filter
    # ──────────────────────────────────────────────────────────────
    def label_pivots(self, ohlc: pd.DataFrame, pivots: List[PivotPoint]) -> List[PivotPoint]:
        """
        1. Compare consecutive highs -> HH / LH
        2. Compare consecutive lows -> HL / LL
        3. Check 10-day High condition: HH price > highest high of prior 10 days
        4. Check 10-day Low condition: LL price < lowest low of prior 10 days
        """
        highs = ohlc["high"].values
        lows = ohlc["low"].values
        prev_ph: Optional[PivotPoint] = None
        prev_pl: Optional[PivotPoint] = None

        # Strictly 4H timeframe: 24h / 4h = 6 bars per day -> 10 days = exactly 60 bars
        lookback_bars = self.macro_lookback_days * 6  # 60 candles on 4H

        for p in pivots:
            idx = p.bar_index
            start_idx = max(0, idx - lookback_bars)

            if p.pivot_type == "PH":
                if prev_ph is None:
                    p.label = "HH"
                elif p.price > prev_ph.price:
                    p.label = "HH"
                else:
                    p.label = "LH"
                prev_ph = p

                # 10-Day High check: Was this HH price > 10-day high?
                if p.label == "HH" and idx >= self.left_len_h + 5:
                    if idx > start_idx:
                        ten_day_max = np.max(highs[start_idx:idx])
                        p.macro_price = float(ten_day_max)
                        p.is_macro_level = bool(p.price > ten_day_max)

            elif p.pivot_type == "PL":
                if prev_pl is None:
                    p.label = "HL"
                elif p.price > prev_pl.price:
                    p.label = "HL"
                else:
                    p.label = "LL"
                prev_pl = p

                # 10-Day Low check: Was this LL price < 10-day low?
                if p.label == "LL" and idx >= self.left_len_l + 5:
                    if idx > start_idx:
                        ten_day_min = np.min(lows[start_idx:idx])
                        p.macro_price = float(ten_day_min)
                        p.is_macro_level = bool(p.price < ten_day_min)

        return pivots

    # ──────────────────────────────────────────────────────────────
    # STEP 3 & 4: Detect Structure Breaks with 10-Day Filter
    # ──────────────────────────────────────────────────────────────
    def detect_structure(
        self, ohlc: pd.DataFrame, pivots: List[PivotPoint]
    ) -> List[StructureEvent]:
        """
        Detects BOS and Clean CHoCH.
        Enforces:
        - Bearish CHoCH: Clean close below the origin HL that formed strictly before the 10-day HH.
        - Bullish CHoCH: Clean close above the origin LH that formed strictly before the 10-day LL.
        """
        closes = ohlc["close"].values
        times = ohlc["time"].values if "time" in ohlc.columns else [None] * len(ohlc)
        n = len(ohlc)

        pivots_by_confirm: Dict[int, List[PivotPoint]] = {}
        for p in pivots:
            pivots_by_confirm.setdefault(p.confirm_index, []).append(p)

        trend = 0  # 1 = Uptrend, -1 = Downtrend, 0 = Undefined
        last_hh: Optional[PivotPoint] = None
        last_hl: Optional[PivotPoint] = None
        last_lh: Optional[PivotPoint] = None
        last_ll: Optional[PivotPoint] = None

        # Origin swing levels locked to 10-Day extremes:
        active_macro_hh: Optional[PivotPoint] = None
        origin_hl_for_hh: Optional[PivotPoint] = None

        active_macro_ll: Optional[PivotPoint] = None
        origin_lh_for_ll: Optional[PivotPoint] = None

        confirmed_hls: List[PivotPoint] = []
        confirmed_lhs: List[PivotPoint] = []

        broken_pivot_bars = set()
        events: List[StructureEvent] = []

        for i in range(n):
            time_val = times[i]
            close = closes[i]

            # Activate confirmed pivots
            if i in pivots_by_confirm:
                for p in pivots_by_confirm[i]:
                    if p.label == "HH":
                        last_hh = p
                        if last_hl is not None:
                            trend = 1
                        # If this HH broke the 10-day high, lock its origin HL before the HH:
                        if p.is_macro_level:
                            active_macro_hh = p
                            prev_hls = [x for x in confirmed_hls if x.bar_index < p.bar_index]
                            if prev_hls:
                                origin_hl_for_hh = prev_hls[-1]
                            elif last_hl is not None and last_hl.bar_index < p.bar_index:
                                origin_hl_for_hh = last_hl
                    elif p.label == "HL":
                        last_hl = p
                        confirmed_hls.append(p)
                        if last_hh is not None:
                            trend = 1
                    elif p.label == "LH":
                        last_lh = p
                        confirmed_lhs.append(p)
                        if last_ll is not None:
                            trend = -1
                    elif p.label == "LL":
                        last_ll = p
                        if last_lh is not None:
                            trend = -1
                        # If this LL broke the 10-day low, lock its origin LH before the LL:
                        if p.is_macro_level:
                            active_macro_ll = p
                            prev_lhs = [x for x in confirmed_lhs if x.bar_index < p.bar_index]
                            if prev_lhs:
                                origin_lh_for_ll = prev_lhs[-1]
                            elif last_lh is not None and last_lh.bar_index < p.bar_index:
                                origin_lh_for_ll = last_lh

            # ── UPTREND / BEARISH REVERSAL STATE ──
            if trend == 1 or active_macro_hh is not None:
                # Bullish BOS: Price closes above last confirmed HH
                if (
                    last_hh is not None
                    and last_hh.bar_index not in broken_pivot_bars
                    and close > last_hh.price
                ):
                    is_clean = close > last_hh.candle_high
                    events.append(
                        StructureEvent(
                            bar_index=i,
                            event_type="BOS",
                            direction=1,
                            level=last_hh.price,
                            broken_pivot=last_hh,
                            is_clean=is_clean,
                            candle_close=close,
                            time=time_val,
                        )
                    )
                    broken_pivot_bars.add(last_hh.bar_index)

                # Bearish CHoCH: Price CLOSES strictly below the Origin HL low wick
                # REQUIREMENT: Origin HL that occurred before the 10-day HH!
                target_hl = origin_hl_for_hh if origin_hl_for_hh is not None else last_hl
                target_hh = active_macro_hh if active_macro_hh is not None else last_hh

                if (
                    target_hl is not None
                    and target_hh is not None
                    and target_hh.is_macro_level
                    and target_hl.bar_index not in broken_pivot_bars
                    and close < target_hl.candle_low
                ):
                    events.append(
                        StructureEvent(
                            bar_index=i,
                            event_type="CHoCH",
                            direction=-1,
                            level=target_hl.price,
                            broken_pivot=target_hl,
                            is_clean=True,
                            candle_close=close,
                            macro_anchor=target_hh,
                            time=time_val,
                        )
                    )
                    broken_pivot_bars.add(target_hl.bar_index)
                    trend = -1
                    last_lh = target_hh
                    last_hh = None
                    last_hl = None
                    active_macro_hh = None
                    origin_hl_for_hh = None

            # ── DOWNTREND / BULLISH REVERSAL STATE ──
            elif trend == -1 or active_macro_ll is not None:
                # Bearish BOS: Price closes below last confirmed LL
                if (
                    last_ll is not None
                    and last_ll.bar_index not in broken_pivot_bars
                    and close < last_ll.price
                ):
                    is_clean = close < last_ll.candle_low
                    events.append(
                        StructureEvent(
                            bar_index=i,
                            event_type="BOS",
                            direction=-1,
                            level=last_ll.price,
                            broken_pivot=last_ll,
                            is_clean=is_clean,
                            candle_close=close,
                            time=time_val,
                        )
                    )
                    broken_pivot_bars.add(last_ll.bar_index)

                # Bullish CHoCH: Price CLOSES strictly above the Origin LH high wick
                # REQUIREMENT: Origin LH that occurred before the 10-day LL!
                target_lh = origin_lh_for_ll if origin_lh_for_ll is not None else last_lh
                target_ll = active_macro_ll if active_macro_ll is not None else last_ll

                if (
                    target_lh is not None
                    and target_ll is not None
                    and target_ll.is_macro_level
                    and target_lh.bar_index not in broken_pivot_bars
                    and close > target_lh.candle_high
                ):
                    events.append(
                        StructureEvent(
                            bar_index=i,
                            event_type="CHoCH",
                            direction=1,
                            level=target_lh.price,
                            broken_pivot=target_lh,
                            is_clean=True,
                            candle_close=close,
                            macro_anchor=target_ll,
                            time=time_val,
                        )
                    )
                    broken_pivot_bars.add(target_lh.bar_index)
                    trend = 1
                    last_hl = target_ll
                    last_lh = None
                    last_ll = None
                    active_macro_ll = None
                    origin_lh_for_ll = None

        return events

    # ──────────────────────────────────────────────────────────────
    # STEP 5: Find CHoCH after BOS setups
    # ──────────────────────────────────────────────────────────────
    def find_choch_after_bos(
        self, events: List[StructureEvent], max_bars: int = 200
    ) -> List[CHoCHAfterBOS]:
        """
        Finds validated CHoCH events that broke origin swing levels.
        Attaches the prior BOS when available.
        """
        signals: List[CHoCHAfterBOS] = []
        last_bos: Optional[StructureEvent] = None

        for ev in events:
            if ev.event_type == "BOS":
                last_bos = ev
            elif ev.event_type == "CHoCH" and ev.is_clean:
                if last_bos is not None and (0 < ev.bar_index - last_bos.bar_index <= max_bars):
                    signals.append(
                        CHoCHAfterBOS(
                            bos_event=last_bos,
                            choch_event=ev,
                            bars_between=ev.bar_index - last_bos.bar_index,
                        )
                    )
                else:
                    anchor = ev.macro_anchor
                    synthetic_bos = StructureEvent(
                        bar_index=anchor.bar_index if anchor else ev.bar_index,
                        event_type="BOS",
                        direction=-ev.direction,
                        level=anchor.price if anchor else ev.level,
                        broken_pivot=anchor if anchor else ev.broken_pivot,
                        is_clean=True,
                        candle_close=anchor.candle_close if anchor else ev.candle_close,
                        time=anchor.time if anchor else ev.time,
                    )
                    bars_diff = ev.bar_index - (anchor.bar_index if anchor else ev.bar_index)
                    signals.append(
                        CHoCHAfterBOS(
                            bos_event=synthetic_bos,
                            choch_event=ev,
                            bars_between=max(1, bars_diff),
                        )
                    )
                last_bos = None

        return signals

    # ──────────────────────────────────────────────────────────────
    # PUBLIC CONVENIENCE METHODS
    # ──────────────────────────────────────────────────────────────
    def analyze(self, ohlc: pd.DataFrame) -> dict:
        """
        Full analysis pipeline on OHLC dataframe.
        """
        min_required = max(self.left_len_h + self.right_len_h, self.left_len_l + self.right_len_l) + 10
        if len(ohlc) < min_required:
            return {
                "pivots": [],
                "events": [],
                "signals": [],
                "latest_signal": None,
            }

        pivots = self.find_pivots(ohlc)
        pivots = self.label_pivots(ohlc, pivots)
        events = self.detect_structure(ohlc, pivots)
        signals = self.find_choch_after_bos(events)

        return {
            "pivots": pivots,
            "events": events,
            "signals": signals,
            "latest_signal": signals[-1] if signals else None,
        }

    def has_recent_signal(
        self,
        ohlc: pd.DataFrame,
        lookback_bars: int = 2,
        require_closed: bool = True,
        timeframe_seconds: int = 14400,
    ) -> Optional[CHoCHAfterBOS]:
        """
        Checks if a clean CHoCH-after-BOS formed in the last `lookback_bars` closed candles.

        Args:
            ohlc: DataFrame with time, open, high, low, close
            lookback_bars: Number of recent closed bars to evaluate (default 2)
            require_closed: If True (default), strips the currently forming / unclosed candle
                           so signals only trigger on PROPER CANDLE CLOSES.
            timeframe_seconds: Bar length in seconds (14400 for 4H).
        """
        if ohlc is None or ohlc.empty:
            return None

        eval_ohlc = ohlc
        if require_closed and "time" in eval_ohlc.columns and len(eval_ohlc) > 0:
            try:
                last_time = eval_ohlc["time"].iloc[-1]
                if hasattr(last_time, "timestamp"):
                    last_epoch = last_time.timestamp()
                else:
                    last_epoch = pd.to_datetime(last_time).timestamp()
                # If current time is earlier than candle open time + duration, candle is still running!
                if time.time() < (last_epoch + timeframe_seconds):
                    eval_ohlc = eval_ohlc.iloc[:-1].copy()
            except Exception:
                pass

        result = self.analyze(eval_ohlc)
        if not result["signals"]:
            return None

        latest = result["signals"][-1]
        if latest.choch_event.bar_index >= (len(eval_ohlc) - lookback_bars):
            return latest
        return None


# Alias for backward compatibility
MarketStructureDetector = TradingViewPivotDetector
