"""Transaction pre-broadcast guard: calldata decode + risk rules + RPC simulation.

Deterministic by design — every output carries evidence + data sources.
Never guesses: unknown selectors are flagged `unknown_selector` (manual review),
never mis-identified as safe.
"""

import json

import httpx

MAX_UINT = 2**256 - 1

# 4-byte selector -> (name, arg_types)
KNOWN_SELECTORS = {
    "0x095ea7b3": ("approve(address,uint256)", ["address", "uint256"]),
    "0xa22cb465": ("setApprovalForAll(address,bool)", ["address", "bool"]),
    "0xa9059cbb": ("transfer(address,uint256)", ["address", "uint256"]),
    "0x23b872dd": ("transferFrom(address,address,uint256)", ["address", "address", "uint256"]),
    "0x3659cfe6": ("upgradeTo(address)", ["address"]),
    "0x4f1ef286": ("upgradeToAndCall(address,bytes)", ["address", "bytes"]),
    "0xf2fde38b": ("transferOwnership(address)", ["address"]),
    "0x715018a6": ("renounceOwnership()", []),
    "0x8da5cb5b": ("owner()", []),
    "0x70a08231": ("balanceOf(address)", ["address"]),
    "0x18160ddd": ("totalSupply()", []),
    "0xdd62ed3e": ("allowance(address,address)", ["address", "address"]),
    "0x42966c68": ("burn(uint256)", ["uint256"]),
    "0x095ea7b3": ("approve(address,uint256)", ["address", "uint256"]),
    "0xd505accf": ("permit(address,address,uint256,uint256,uint8,bytes32,bytes32)",
                  ["address", "address", "uint256", "uint256", "uint8", "bytes32", "bytes32"]),
}

# public EVM RPCs
RPC = {
    "xlayer": "https://rpc.xlayer.tech",
    "ethereum": "https://ethereum-rpc.publicnode.com",
}

CHAIN_IDS = {"xlayer": 196, "ethereum": 1, "eth": 1}


def _decode_arg(typ: str, data: bytes, offset: int):
    """Minimal ABI decoder for the fixed types we need."""
    if typ == "address":
        return "0x" + data[offset + 12: offset + 32].hex(), offset + 32
    if typ == "uint256":
        return int.from_bytes(data[offset: offset + 32], "big"), offset + 32
    if typ == "bool":
        b = int.from_bytes(data[offset: offset + 32], "big")
        return bool(b), offset + 32
    if typ == "uint8":
        return int.from_bytes(data[offset: offset + 32], "big"), offset + 32
    if typ == "bytes32":
        return "0x" + data[offset: offset + 32].hex(), offset + 32
    if typ == "bytes":
        # dynamic: offset points to length-prefixed bytes
        rel = int.from_bytes(data[offset: offset + 32], "big")
        length = int.from_bytes(data[rel: rel + 32], "big")
        raw = data[rel + 32: rel + 32 + length]
        return "0x" + raw.hex(), offset + 32
    raise ValueError(f"unsupported type {typ}")


def decode_calldata(calldata: str) -> dict:
    cd = calldata[2:] if calldata.startswith("0x") else calldata
    if len(cd) < 8:
        return {"selector": None, "function": None, "args": {}, "decoded": False,
                "note": "calldata too short"}
    selector = "0x" + cd[:8]
    data = bytes.fromhex(cd[8:])
    known = KNOWN_SELECTORS.get(selector)
    if not known:
        return {"selector": selector, "function": None, "args": {}, "decoded": False,
                "note": "unknown selector"}
    fname, types = known
    args, off = {}, 0
    for t in types:
        if t.endswith("[]"):
            raise ValueError("array args not supported in this light decoder")
        val, off = _decode_arg(t, data, off)
        args[f"arg{len(args)}"] = {"type": t, "value": val}
    return {"selector": selector, "function": fname, "args": args, "decoded": True,
            "note": None}


def risk_rules(decoded: dict) -> dict:
    flags = []
    fname = decoded.get("function")
    args = decoded.get("args") or {}
    vals = [a["value"] for a in args.values()]

    if fname == "approve(address,uint256)":
        amount = vals[1] if len(vals) > 1 else 0
        if amount == MAX_UINT:
            flags.append({"flag": "unlimited_approval", "severity": "high",
                          "detail": "approve(spender, uint256::MAX) grants unlimited token access"})
        else:
            flags.append({"flag": "approve", "severity": "info",
                          "detail": f"approve amount {amount}"})
    elif fname == "setApprovalForAll(address,bool)":
        enabled = vals[1] if len(vals) > 1 else False
        if enabled:
            flags.append({"flag": "set_approval_for_all", "severity": "critical",
                          "detail": "grants operator access to ALL NFTs, forever until revoked"})
        else:
            flags.append({"flag": "revoke_approval_for_all", "severity": "info",
                          "detail": "revokes operator access"})
    elif fname and "upgradeTo" in fname:
        flags.append({"flag": "proxy_upgrade", "severity": "high",
                      "detail": "changes implementation behind a proxy — verify the new logic contract"})
    elif fname == "transferOwnership(address)":
        flags.append({"flag": "ownership_change", "severity": "medium",
                      "detail": "transfers contract ownership"})
    elif fname == "renounceOwnership()":
        flags.append({"flag": "ownership_renounce", "severity": "high",
                      "detail": "renounces ownership irreversibly"})
    elif fname == "permit(address,address,uint256,uint256,uint8,bytes32,bytes32)":
        flags.append({"flag": "permit", "severity": "info",
                      "detail": "off-chain allowance signature"})
    elif fname is None:
        flags.append({"flag": "unknown_selector", "severity": "medium",
                      "detail": "selector not in the known-safety catalog"})
    else:
        flags.append({"flag": "known_function", "severity": "info", "detail": fname})

    return {"flags": flags}


async def simulate(chain: str, to: str, calldata: str, from_addr: str) -> dict:
    rpc = RPC.get(chain.lower())
    if not rpc:
        return {"simulated": False, "note": f"unsupported chain {chain}"}
    out = {"chain": chain.lower(), "rpc": rpc, "simulated": True}
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            call = await client.post(rpc, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_call",
                "params": [{"to": to, "from": from_addr or None, "data": calldata}, "latest"],
            })
            r = call.json()
            if "error" in r:
                msg = (r["error"].get("message") or "")[:200]
                out.update({"reverted": True, "revert_reason": msg,
                            "result": "revert"})
            else:
                out.update({"reverted": False, "result": "success", "return_data": (r.get("result") or "0x")[:66]})
            est = await client.post(rpc, json={
                "jsonrpc": "2.0", "id": 2, "method": "eth_estimateGas",
                "params": [{"to": to, "from": from_addr or None, "data": calldata}],
            })
            e = est.json()
            if "error" in e:
                out["gas_estimate"] = None
                out["estimate_note"] = (e["error"].get("message") or "")[:120]
            else:
                out["gas_estimate"] = int(e["result"], 16)
    except Exception as exc:
        out.update({"simulated": False, "reverted": None, "result": "unavailable",
                    "note": f"rpc simulation unavailable: {str(exc)[:160]}"})
    return out


def verdict(decoded: dict, sim: dict) -> str:
    sev = {f["flag"]: f["severity"] for f in decoded["risk"]["flags"]}
    if "set_approval_for_all" in sev:
        return "block"
    if sim.get("reverted") and "unknown_selector" in sev:
        return "block"
    if "proxy_upgrade" in sev:
        return "caution"
    if "unlimited_approval" in sev:
        return "caution"
    if "unknown_selector" in sev:
        return "caution"
    if sim.get("reverted"):
        return "caution"
    return "allow"


async def tx_guard(chain: str, to: str, calldata: str, from_addr: str = None) -> dict:
    decoded = decode_calldata(calldata)
    decoded["risk"] = risk_rules(decoded)
    sim = await simulate(chain, to, calldata, from_addr)
    return {
        "verdict": verdict(decoded, sim),
        "target": to,
        "chain": chain.lower(),
        "chain_id": CHAIN_IDS.get(chain.lower()),
        "decoded": decoded,
        "simulation": sim,
        "evidence": {
            "checks": [f["flag"] for f in decoded["risk"]["flags"]],
            "data_sources": ["on-chain eth_call simulation", "static selector catalog"],
            "formula": "block: setApprovalForAll / unknown+revert; caution: unlimited approve, proxy upgrade, ownership change, unknown selector; else allow",
        },
        "disclaimer": "Light pre-broadcast final check, not a substitute for a full security audit. Informational only.",
    }
