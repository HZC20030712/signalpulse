"""Funding-rate arbitrage scanner on OKX public data (no auth)."""

import asyncio

import httpx

OKX_BASE = "https://www.okx.com"


async def _get(client, path, params=None):
    r = await client.get(OKX_BASE + path, params=params, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("code") != "0":
        raise RuntimeError(d.get("msg"))
    return d.get("data") or []


async def scan_funding(top: int = 8) -> dict:
    """Top positive/negative funding rates among liquid USDT-margined swaps."""
    async with httpx.AsyncClient() as client:
        tickers = await _get(client, "/api/v5/market/tickers", {"instType": "SWAP"})
        usdt = [
            t for t in tickers
            if t["instId"].endswith("-USDT-SWAP") and float(t.get("volCcy24h") or 0) > 5_000_000
        ]
        usdt.sort(key=lambda t: -float(t["volCcy24h"]))
        candidates = usdt[:40]

        async def fr(inst):
            try:
                rows = await _get(client, "/api/v5/public/funding-rate", {"instId": inst})
                if rows:
                    return rows[0]
            except Exception:
                return None

        results = await asyncio.gather(*[fr(t["instId"]) for t in candidates])

    rows = []
    last_px = {t["instId"]: float(t["last"]) for t in candidates}
    for r in results:
        if not r:
            continue
        rate = float(r["fundingRate"])
        rows.append({
            "inst": r["instId"],
            "funding_rate_pct": round(rate * 100, 4),
            "annualized_pct": round(rate * 3 * 365 * 100, 1),
            "last": last_px.get(r["instId"]),
            "next_funding_time": r.get("nextFundingTime"),
        })
    rows.sort(key=lambda x: -x["funding_rate_pct"])
    pos = [r for r in rows if r["funding_rate_pct"] > 0.01][:top]
    neg = [r for r in rows if r["funding_rate_pct"] < -0.01][-top:][::-1]
    for r in pos:
        r["arb_idea"] = "short perp + long spot earns funding (longs pay shorts)"
    for r in neg:
        r["arb_idea"] = "long perp + short spot earns funding (shorts pay longs)"
    return {
        "positive_funding_top": pos,
        "negative_funding_top": neg,
        "scanned": len(rows),
        "note": "Funding settles every 8h on OKX; annualized = rate x 3 x 365. Basis risk and fees apply.",
        "disclaimer": "Informational only, not investment advice.",
    }
