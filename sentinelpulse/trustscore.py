"""Deterministic agent trust scoring + service routing (pure logic, caller-fed data)."""


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def trust_score(candidates: list) -> dict:
    """Score candidate ASPs. Each candidate: {name, sales?, rating?, online?, listedAt?}.
    rating expected 0-100 (marketplace feedbackRate)."""
    rows = []
    for c in candidates:
        sales = _num(c.get("sales"))
        rating = _num(c.get("rating"))
        online = c.get("online", 1)
        score = 0.0
        factors = []
        # rating up to 40
        score += min(rating, 100) * 0.40
        factors.append(f"rating {rating}/100 -> {min(rating, 100) * 0.40:.1f}")
        # sales up to 30 (log-ish)
        sales_component = min(30.0, (sales ** 0.5) * 2.0)
        score += sales_component
        factors.append(f"sales {sales} -> {sales_component:.1f}")
        # online 0/1 -> 20
        score += 20.0 if online in (1, "1", True) else 0.0
        factors.append(f"online -> {20.0 if online in (1, '1', True) else 0.0}")
        # listedAt recency -> 10 (newer = 0, older = up to 10; caller may pass days_since)
        days = _num(c.get("days_listed"))
        recency = max(0.0, 10.0 - days * 0.2)
        score += recency
        factors.append(f"days_listed {days} -> {recency:.1f}")
        flags = []
        if rating == 0:
            flags.append("no_rating_yet")
        if sales == 0:
            flags.append("zero_sales")
        if online not in (1, "1", True):
            flags.append("offline")
        rows.append({
            "name": c.get("name"),
            "score": round(min(100.0, score), 1),
            "factors": factors,
            "risk_flags": flags,
        })
    rows.sort(key=lambda r: -r["score"])
    return {
        "ranked": rows,
        "formula": "score = rating*0.40 + sqrt(sales)*2 (cap 30) + online*20 + recency(10)",
        "note": "Deterministic; caller supplies data. Verifiable and re-runnable.",
        "disclaimer": "Informational only, not investment advice.",
    }


def route_pick(task: str, budget: float, candidates: list, capabilities: list = None) -> dict:
    """Rank candidate services for a task. Each candidate:
    {name, fee?, capabilities?: [..], trust?: 0-100}."""
    caps = capabilities or []
    tl = task.lower()
    rows = []
    for c in candidates:
        fee = _num(c.get("fee"))
        trust = _num(c.get("trust"), 50)
        own_caps = [x.lower() for x in (c.get("capabilities") or [])]
        # capability match: keyword overlap
        overlap = sum(1 for k in caps if any(k.lower() in oc for oc in own_caps))
        if caps:
            match = overlap / len(caps)
        else:
            match = 0.5
        price_fit = 1.0 if fee <= budget else max(0.0, 1.0 - (fee - budget) / max(budget, 0.01))
        score = match * 45 + price_fit * 30 + (trust / 100) * 25
        rows.append({
            "name": c.get("name"),
            "score": round(min(100.0, score), 1),
            "capability_match": round(match, 2),
            "price_fit": round(price_fit, 2),
            "fee": fee,
            "within_budget": fee <= budget,
        })
    rows.sort(key=lambda r: -r["score"])
    if not rows:
        return {"ranked": [], "recommendation": None, "fallback": []}
    rec = rows[0]
    return {
        "task": task,
        "budget": budget,
        "ranked": rows,
        "recommendation": rec,
        "alternatives": rows[1:],
        "fallback": [r["name"] for r in rows[1:4]],
        "formula": "score = capability_match*45 + price_fit*30 + trust*25",
        "evidence_receipt": {"task": task, "budget": budget, "candidates_count": len(rows)},
        "disclaimer": "Informational only, not investment advice.",
    }
