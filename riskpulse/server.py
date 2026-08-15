"""RiskPulse — deterministic risk-math MCP endpoint (x402 v2, X Layer).

Distilled from the market-proven risk-toolkit pattern: every answer is
re-runnable and carries its formula + inputs. Paywall at tools/call layer.
Companion to SignalPulse (signal -> sizing -> risk chain).
"""

import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import riskmath
from fundingscan import scan_funding
from x402okx import (
    DEV_MODE,
    PAY_TO,
    FacilitatorError,
    OKXFacilitator,
    challenge_headers_and_body,
    decode_payment_signature,
    encode_payment_response,
)

APP_VERSION = "1.0.0"
RESOURCE_URL = os.environ.get("PUBLIC_URL", "http://localhost:8001") + "/mcp"

TOOLS = [
    {
        "name": "position_size",
        "description": "Position sizing from Kelly criterion + fixed-fractional cap, with risk-of-ruin estimate. Every output carries the formula and inputs for verification. Price: 0.01 USDT per call, settled on X Layer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bankroll": {"type": "number", "description": "Total capital in USDT"},
                "win_rate": {"type": "number", "description": "Historical win probability 0-1, e.g. 0.55"},
                "payoff_ratio": {"type": "number", "description": "Avg win / avg loss, e.g. 1.8"},
                "risk_pct": {"type": "number", "description": "Max risk per trade as fraction, default 0.02"},
                "kelly_mode": {"type": "string", "description": "full | half | quarter (default quarter)"},
            },
            "required": ["bankroll", "win_rate", "payoff_ratio"],
        },
        "price": 0.01,
    },
    {
        "name": "liquidation_gate",
        "description": "Perpetual liquidation price and safety distance for a position: entry, leverage, side, maintenance margin rate. Returns liquidation price, distance %, and a safety verdict with the exact formula. Price: 0.01 USDT per call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry": {"type": "number", "description": "Entry price"},
                "leverage": {"type": "number", "description": "Leverage, e.g. 5"},
                "side": {"type": "string", "description": "long | short"},
                "mmr": {"type": "number", "description": "Maintenance margin rate, default 0.005 (0.5%)"},
            },
            "required": ["entry", "leverage", "side"],
        },
        "price": 0.01,
    },
    {
        "name": "funding_scan",
        "description": "Scans liquid USDT-margined perpetuals on OKX for extreme funding rates and surfaces spot-perp arbitrage ideas with annualized rates. Price: 0.01 USDT per call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "description": "Rows per side, default 8"},
            },
        },
        "price": 0.01,
    },
    {
        "name": "get_sample",
        "description": "Free sample: position_size for bankroll=10000, win_rate=0.55, payoff=1.8, so you can evaluate output quality before paying.",
        "inputSchema": {"type": "object", "properties": {}},
        "price": 0.0,
    },
]

app = FastAPI(title="RiskPulse", version=APP_VERSION)


def _rpc_result(rpc_id, result):
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _rpc_error(rpc_id, code, message):
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}})


def _tool_result(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


async def _run_tool(name: str, args: dict) -> dict:
    args = args or {}
    if name == "position_size":
        return riskmath.position_size(
            float(args["bankroll"]), float(args["win_rate"]), float(args["payoff_ratio"]),
            float(args.get("risk_pct", 0.02)), str(args.get("kelly_mode", "quarter")),
        )
    if name == "liquidation_gate":
        return riskmath.liquidation_price(
            float(args["entry"]), float(args["leverage"]), str(args["side"]).lower(),
            float(args.get("mmr", 0.005)),
        )
    if name == "funding_scan":
        return await scan_funding(int(args.get("top", 8)))
    if name == "get_sample":
        return riskmath.position_size(10000.0, 0.55, 1.8)
    raise KeyError(name)


@app.post("/mcp")
async def mcp(request: Request):
    try:
        msg = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "parse error")

    method = msg.get("method")
    rpc_id = msg.get("id")

    if method == "notifications/initialized":
        return JSONResponse(status_code=202, content=None)

    if method == "initialize":
        return _rpc_result(rpc_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "riskpulse", "version": APP_VERSION},
        })

    if method == "tools/list":
        return _rpc_result(rpc_id, {
            "tools": [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]
        })

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if not tool:
            return _rpc_error(rpc_id, -32602, f"unknown tool: {name}; available: {[t['name'] for t in TOOLS]}")

        price = tool["price"]
        if price > 0 and not DEV_MODE:
            pay_header = request.headers.get("PAYMENT-SIGNATURE")
            if not pay_header:
                headers, envelope = challenge_headers_and_body(RESOURCE_URL, tool["description"], price)
                return JSONResponse(status_code=402, content=envelope, headers=headers)
            try:
                payload = decode_payment_signature(pay_header)
            except Exception:
                return _rpc_error(rpc_id, -32001, "invalid PAYMENT-SIGNATURE header")
            requirements = payload.get("accepted") or {}
            try:
                facilitator = OKXFacilitator()
                v = await facilitator.verify(payload, requirements)
                if not v.get("isValid"):
                    headers, envelope = challenge_headers_and_body(RESOURCE_URL, tool["description"], price)
                    envelope["error"] = f"payment invalid: {v.get('invalidReason') or v.get('invalidMessage')}"
                    return JSONResponse(status_code=402, content=envelope, headers=headers)
                result = await _run_tool(name, args)
                s = await facilitator.settle(payload, requirements)
                headers = {"PAYMENT-RESPONSE": encode_payment_response(s)}
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": rpc_id, "result": _tool_result(result)},
                    headers=headers,
                )
            except FacilitatorError as e:
                return _rpc_error(rpc_id, -32002, f"payment processing unavailable: {e}")

        try:
            result = await _run_tool(name, args)
        except (ValueError, KeyError) as e:
            return _rpc_error(rpc_id, -32602, str(e))
        except Exception as e:
            return _rpc_error(rpc_id, -32603, f"internal: {e}")
        return _rpc_result(rpc_id, _tool_result(result))

    return _rpc_error(rpc_id, -32601, f"method not found: {method}")


@app.get("/")
async def root():
    return {
        "service": "RiskPulse",
        "version": APP_VERSION,
        "mcp_endpoint": "/mcp",
        "pay_to": PAY_TO,
        "dev_mode": DEV_MODE,
        "tools": [{"name": t["name"], "price_usdt": t["price"]} for t in TOOLS],
    }


@app.get("/health")
async def health():
    return {"ok": True}


LLMS_TXT = """# RiskPulse — Deterministic Risk-Math API for AI Agents

> Position sizing, liquidation gating, and funding-rate arbitrage. Every result carries its formula and inputs; re-runnable and verifiable. Settles per call on X Layer.

## Endpoint
- MCP (Streamable HTTP): https://riskpulse-priz.onrender.com/mcp

## Tools
- position_size (0.01 USDT/call): Kelly criterion sizing (full/half/quarter) with fixed-fractional cap and risk-of-ruin estimate.
- liquidation_gate (0.01): perpetual liquidation price, distance % and safety verdict from entry, leverage, side, maintenance margin.
- funding_scan (0.01): scans liquid USDT-margined perpetuals for extreme funding rates and spot-perp arbitrage ideas.
- get_sample (free): sample position_size output.

## Payment
x402 v2, scheme=exact, network=eip155:196 (X Layer), asset=USDT0.

## Example
curl -X POST https://riskpulse-priz.onrender.com/mcp -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"position_size","arguments":{"bankroll":10000,"win_rate":0.55,"payoff_ratio":1.8}}}'
"""


@app.get("/llms.txt")
async def llms_txt():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(LLMS_TXT)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8001")))
