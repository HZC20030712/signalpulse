"""Signal engine: combines EMA trend, MACD, RSI, ATR, momentum and volume
into a single direction + confidence call. Distilled from the market-proven
per-call signal API pattern, extended with volume-flow weighting."""

from indicators import atr, ema, macd, rsi, sma

MODEL_VERSION = "signalpulse-1.0.0"


def compute_signal(pair: str, timeframe: str, candles: dict) -> dict:
    closes = candles["closes"]
    highs = candles["highs"]
    lows = candles["lows"]
    volumes = candles["volumes"]

    last = closes[-1]
    e12 = ema(closes, 12)[-1]
    e26 = ema(closes, 26)[-1]
    macd_line, macd_sig, macd_hist = macd(closes)
    r = rsi(closes, 14)
    a = atr(highs, lows, closes, 14)
    vol_sma = sma(volumes, 20) or 0.0
    vol_ratio = (volumes[-1] / vol_sma) if vol_sma else 1.0
    mom5 = (last - closes[-6]) / closes[-6] if len(closes) > 6 else 0.0

    factors = []
    score = 0.0

    trend_gap = (e12 - e26) / last
    trend_score = max(-1.0, min(1.0, trend_gap * 400))  # 0.25% gap -> full weight
    score += trend_score * 0.30
    factors.append(
        f"EMA12 {'>' if e12 > e26 else '<='} EMA26 ({'uptrend' if e12 > e26 else 'downtrend'}, gap {trend_gap * 100:.2f}%)"
    )

    hist_norm = max(-1.0, min(1.0, (macd_hist / last) * 800))
    score += hist_norm * 0.20
    factors.append(f"MACD histogram {'positive' if macd_hist > 0 else 'negative'} ({macd_hist:.4f})")

    rsi_score = max(-1.0, min(1.0, (r - 50.0) / 25.0))
    if r >= 75:
        rsi_score = -0.5  # overbought fade risk
        factors.append(f"RSI14 {r:.1f} (overbought, fade risk)")
    elif r <= 25:
        rsi_score = 0.5  # oversold bounce
        factors.append(f"RSI14 {r:.1f} (oversold, bounce zone)")
    else:
        factors.append(f"RSI14 {r:.1f} ({'bullish' if r > 50 else 'bearish'} zone)")
    score += rsi_score * 0.20

    mom_score = max(-1.0, min(1.0, mom5 * 60))
    score += mom_score * 0.15
    factors.append(f"5-bar momentum {mom5 * 100:+.2f}%")

    flow_score = 0.0
    if vol_ratio > 1.3:
        flow_score = 0.6 if mom5 > 0 else -0.6
        factors.append(f"volume surge {vol_ratio:.1f}x 20-bar avg, confirming {'up' if mom5 > 0 else 'down'} move")
    elif vol_ratio < 0.6:
        flow_score = -0.2 * (1 if score > 0 else -1)
        factors.append(f"volume thin {vol_ratio:.1f}x avg, weak conviction")
    else:
        factors.append(f"volume normal {vol_ratio:.1f}x avg")
    score += flow_score * 0.15

    threshold = 0.15
    if score > threshold:
        direction = "long"
    elif score < -threshold:
        direction = "short"
    else:
        direction = "flat"

    confidence = round(min(0.95, abs(score) * 1.6 + (0.1 if vol_ratio > 1.3 else 0.0)), 2)

    if direction == "flat" or a is None:
        entry, stop, targets = None, None, []
    else:
        entry = last
        if direction == "long":
            stop = round(entry - 1.5 * a, 6)
            targets = [round(entry + 1.5 * a, 6), round(entry + 3.0 * a, 6)]
        else:
            stop = round(entry + 1.5 * a, 6)
            targets = [round(entry - 1.5 * a, 6), round(entry - 3.0 * a, 6)]

    return {
        "pair": pair.upper(),
        "timeframe": timeframe.lower(),
        "direction": direction,
        "confidence": confidence,
        "entry": entry,
        "stop_loss": stop,
        "targets": targets,
        "atr14": round(a, 6) if a else None,
        "rsi14": round(r, 2) if r is not None else None,
        "rationale": factors,
        "model_version": MODEL_VERSION,
        "disclaimer": "Informational only, not investment advice.",
    }


async def market_pulse(fetch_candles) -> dict:
    out = {"assets": {}, "regime": None}
    scores = []
    for pair in ("BTC-USD", "ETH-USD", "SOL-USD"):
        try:
            c1h = await fetch_candles(pair, "1h", 120)
            closes = c1h["closes"]
            chg_1h = (closes[-1] - closes[-2]) / closes[-2]
            chg_24h = (closes[-1] - closes[-25]) / closes[-25] if len(closes) > 25 else 0.0
            r = rsi(closes, 14)
            a = atr(c1h["highs"], c1h["lows"], closes, 14)
            atr_pct = (a / closes[-1] * 100) if a else None
            out["assets"][pair] = {
                "last": closes[-1],
                "change_1h_pct": round(chg_1h * 100, 2),
                "change_24h_pct": round(chg_24h * 100, 2),
                "rsi14": round(r, 1) if r is not None else None,
                "atr_pct": round(atr_pct, 3) if atr_pct else None,
            }
            scores.append(chg_24h)
        except Exception as e:  # keep pulse resilient
            out["assets"][pair] = {"error": str(e)}
    if scores:
        avg = sum(scores) / len(scores)
        out["regime"] = "risk-on" if avg > 0.005 else ("risk-off" if avg < -0.005 else "chop")
    out["disclaimer"] = "Informational only, not investment advice."
    return out
