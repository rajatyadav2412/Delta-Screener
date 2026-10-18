# Delta Exchange — BOS → CHoCH Alert System

Real-time scanner that detects **CHoCH (Change of Character) after BOS (Break of Structure)** on the **4H timeframe** using the Delta Exchange API.

## What it Does

```
BOS (Break of Structure)  →  CHoCH (Change of Character)
   Trend Confirmed              Reversal Signal!
```

The scanner identifies instruments where:
1. A **BOS** occurred — confirming the current trend direction
2. A **CHoCH** then formed — signaling a high-probability trend reversal

This is a powerful Smart Money Concepts (SMC) pattern used by institutional traders.

## Project Structure

```
delta exchange alert system/
├── scanner.py             # Main entry point — run this
├── delta_client.py        # Delta Exchange API client
├── structure_detector.py  # BOS & CHoCH detection engine
├── config.json            # Configuration (API key, settings)
├── requirements.txt       # Python dependencies
└── README.md              # This file
```

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API Key

Edit `config.json` and replace the API key:

```json
{
    "api_key": "YOUR_ACTUAL_DELTA_EXCHANGE_API_KEY",
    ...
}
```

### 3. Run the Scanner

```bash
# Continuous scanning (every 15 min)
python scanner.py

# Single scan
python scanner.py --once

# Override API key from command line
python scanner.py --api-key YOUR_KEY --once

# Custom timeframe and swing length
python scanner.py --timeframe 1h --swing-length 15
```

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `api_key` | - | Your Delta Exchange API key |
| `base_url` | `https://api.india.delta.exchange` | API base URL |
| `timeframe` | `4h` | Candle timeframe |
| `swing_length` | `10` | Bars left/right for swing detection |
| `close_break` | `true` | Require close (not just wick) to break level |
| `scan_interval_seconds` | `900` | Seconds between scans (15 min) |
| `contract_types` | `["perpetual_futures"]` | Which contract types to scan |
| `max_instruments` | `50` | Max instruments to scan per cycle |

## How the Detection Works

### Step 1: Swing Highs & Lows
Identifies significant pivot points where price reversed direction, using a rolling window approach.

### Step 2: BOS Detection
A **Break of Structure** occurs when price breaks a swing point **in the direction** of the current trend:
- Bullish BOS: Price closes above the last swing high during an uptrend
- Bearish BOS: Price closes below the last swing low during a downtrend

### Step 3: CHoCH Detection
A **Change of Character** occurs when price breaks a swing point **against** the current trend:
- Bullish CHoCH: Price was in a downtrend, then breaks above a swing high → reversal signal
- Bearish CHoCH: Price was in an uptrend, then breaks below a swing low → reversal signal

### Step 4: CHoCH After BOS (The Signal)
The scanner specifically looks for a CHoCH that forms **after** a confirmed BOS — this sequence is significant because:
- The BOS proves the prior trend was real and strong
- The subsequent CHoCH signals that smart money is reversing

## Credits

Market structure detection logic inspired by [smartmoneyconcepts](https://github.com/joshyattridge/smartmoneyconcepts).
