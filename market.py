"""Market data from OKX public v5 API (no auth required)."""

import httpx

OKX_BASE = "https://www.okx.com"

PAIRS = {
    "BTC-USD": "BTC-USDT",
    "ETH-USD": "ETH-USDT",
    "SOL-USD": "SOL-USDT",
    "XRP-USD": "XRP-USDT",
    "DOGE-USD": "DOGE-USDT",
    "BNB-USD": "BNB-USDT",
    "ADA-USD": "ADA-USDT",
    "AVAX-USD": "AVAX-USDT",
    "LINK-USD": "LINK-USDT",
    "TON-USD": "TON-USDT",
}

TIMEFRAMES = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
}


async def fetch_candles(pair: str, timeframe: str, limit: int = 200) -> dict:
    """Return {opens, highs, lows, closes, volumes} oldest-first."""
    inst = PAIRS.get(pair.upper())
    bar = TIMEFRAMES.get(timeframe.lower())
    if not inst:
        raise ValueError(f"unsupported pair: {pair}; supported: {', '.join(PAIRS)}")
    if not bar:
        raise ValueError(f"unsupported timeframe: {timeframe}; supported: {', '.join(TIMEFRAMES)}")
    params = {"instId": inst, "bar": bar, "limit": str(limit)}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{OKX_BASE}/api/v5/market/candles", params=params)
        r.raise_for_status()
        payload = r.json()
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX API error: {payload.get('msg')}")
    rows = payload.get("data") or []
    if len(rows) < 60:
        raise RuntimeError(f"not enough candles for {inst} {bar}: got {len(rows)}")
    rows = list(reversed(rows))  # API returns newest first
    return {
        "opens": [float(x[1]) for x in rows],
        "highs": [float(x[2]) for x in rows],
        "lows": [float(x[3]) for x in rows],
        "closes": [float(x[4]) for x in rows],
        "volumes": [float(x[5]) for x in rows],
    }


async def fetch_ticker(inst: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{OKX_BASE}/api/v5/market/ticker", params={"instId": inst})
        r.raise_for_status()
        payload = r.json()
    if payload.get("code") != "0" or not payload.get("data"):
        raise RuntimeError(f"ticker error for {inst}")
    return payload["data"][0]
