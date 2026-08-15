"""Deterministic risk math: Kelly sizing, risk-of-ruin, liquidation price.
Every result carries its formula + inputs so the caller can re-run and verify."""


def kelly_fraction(win_rate: float, payoff_ratio: float) -> dict:
    """Full Kelly: f* = W - (1-W)/R. W=win prob, R=avg win/avg loss."""
    w = min(max(win_rate, 0.0), 1.0)
    if payoff_ratio <= 0:
        raise ValueError("payoff_ratio must be > 0")
    f = w - (1.0 - w) / payoff_ratio
    return {
        "kelly_full": round(f, 4),
        "kelly_half": round(f / 2, 4),
        "kelly_quarter": round(f / 4, 4),
        "edge_per_unit": round(w * payoff_ratio - (1 - w), 4),
        "formula": "f* = W - (1-W)/R; edge = W*R - (1-W)",
        "inputs": {"win_rate": w, "payoff_ratio": payoff_ratio},
    }


def risk_of_ruin(win_rate: float, payoff_ratio: float, risk_pct: float) -> dict:
    """Fixed-fractional risk-of-ruin approximation.
    units = 1/risk_pct (bankroll divided into equal risk units);
    RoR ~= ((1-A)/(1+A))^units with A = edge/(W*R + (1-W))."""
    w = min(max(win_rate, 0.0), 1.0)
    if not (0 < risk_pct < 1):
        raise ValueError("risk_pct must be in (0,1), e.g. 0.02 for 2%")
    edge = w * payoff_ratio - (1 - w)
    units = 1.0 / risk_pct
    if edge <= 0:
        ror = 1.0
    else:
        a = edge / (w * payoff_ratio + (1 - w))
        a = min(a, 0.999999)
        ror = ((1 - a) / (1 + a)) ** units
    return {
        "risk_of_ruin": round(ror, 6),
        "risk_units": round(units, 1),
        "verdict": "ruin almost certain" if ror > 0.5 else ("elevated" if ror > 0.1 else "contained"),
        "formula": "RoR ~= ((1-A)/(1+A))^units, A = edge/(W*R+(1-W)), units = 1/risk_pct",
        "inputs": {"win_rate": w, "payoff_ratio": payoff_ratio, "risk_pct": risk_pct},
    }


def position_size(bankroll: float, win_rate: float, payoff_ratio: float,
                  risk_pct: float = 0.02, kelly_mode: str = "quarter") -> dict:
    k = kelly_fraction(win_rate, payoff_ratio)
    ror = risk_of_ruin(win_rate, payoff_ratio, risk_pct)
    fmap = {"full": k["kelly_full"], "half": k["kelly_half"], "quarter": k["kelly_quarter"]}
    f = fmap.get(kelly_mode, k["kelly_quarter"])
    kelly_notional = max(0.0, bankroll * f)
    risk_notional = bankroll * risk_pct
    # conservative: cap kelly-sized position so a full stop-out loses at most risk cap
    suggested = min(kelly_notional, risk_notional / 0.02) if kelly_notional > 0 else 0.0
    return {
        "bankroll": bankroll,
        "suggested_notional": round(suggested, 2),
        "max_risk_amount": round(risk_notional, 2),
        "kelly": k,
        "risk_of_ruin": ror,
        "note": "suggested_notional = min(kelly_notional, risk-capped notional); stop-loss placement determines true risk",
        "disclaimer": "Informational only, not investment advice.",
    }


def liquidation_price(entry: float, leverage: float, side: str,
                      mmr: float = 0.005) -> dict:
    """Isolated-margin liquidation estimate.
    long:  liq = entry * (1 - 1/lev + mmr)
    short: liq = entry * (1 + 1/lev - mmr)
    mmr = maintenance margin rate (default 0.5%, tier-dependent in production)."""
    if leverage < 1:
        raise ValueError("leverage must be >= 1")
    if side not in ("long", "short"):
        raise ValueError("side must be long or short")
    if side == "long":
        liq = entry * (1 - 1.0 / leverage + mmr)
        dist = (entry - liq) / entry
    else:
        liq = entry * (1 + 1.0 / leverage - mmr)
        dist = (liq - entry) / entry
    if liq <= 0:
        liq = 0.0
        dist = 1.0
    return {
        "side": side,
        "entry": entry,
        "leverage": leverage,
        "maintenance_margin_rate": mmr,
        "liquidation_price": round(liq, 6),
        "distance_pct": round(dist * 100, 2),
        "safety": "danger zone (<5%)" if dist < 0.05 else ("caution (<15%)" if dist < 0.15 else "comfortable"),
        "formula": "long: entry*(1-1/lev+mmr); short: entry*(1+1/lev-mmr)",
        "disclaimer": "Estimate for isolated margin; cross margin and tiered MMR differ. Informational only.",
    }
