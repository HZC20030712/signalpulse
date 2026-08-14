"""SignalPulse — paid trading-signal MCP endpoint (x402 v2, X Layer).

MCP over Streamable HTTP (minimal JSON-RPC):
  POST /mcp  {initialize | notifications/initialized | tools/list | tools/call}
Paywall lives at the tools/call layer, per OKX A2MCP convention:
  - free tools  -> normal JSON-RPC result
  - paid tools  -> HTTP 402 + PAYMENT-REQUIRED header
  - paid replay -> PAYMENT-SIGNATURE header -> verify -> run -> settle -> 200 + PAYMENT-RESPONSE
"""

import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import market
import signals
from gasprice import all_gas
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
RESOURCE_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000") + "/mcp"

TOOLS = [
    {
        "name": "get_signal",
        "description": "Trading signal for a crypto pair/timeframe: direction (long|short|flat), confidence 0-1, rationale (EMA/MACD/RSI/ATR/volume-flow), plus entry, stop-loss and targets when directional. Price: 0.05 USDT per call, settled on X Layer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pair": {"type": "string", "description": "e.g. BTC-USD. Supported: " + ", ".join(market.PAIRS)},
                "timeframe": {"type": "string", "description": "5m | 15m | 1h | 4h | 1d (default 1h)"},
            },
            "required": ["pair"],
        },
        "price": 0.05,
    },
    {
        "name": "get_market_pulse",
        "description": "Market regime snapshot: BTC/ETH/SOL 1h & 24h changes, RSI, ATR volatility, and an overall risk-on / chop / risk-off read. Price: 0.01 USDT per call.",
        "inputSchema": {"type": "object", "properties": {}},
        "price": 0.01,
    },
    {
        "name": "get_gas_prices",
        "description": "Live gas prices across X Layer, Ethereum, Base, BSC, Polygon, Arbitrum — route transactions to the cheapest chain. Price: 0.005 USDT per call.",
        "inputSchema": {"type": "object", "properties": {}},
        "price": 0.005,
    },
    {
        "name": "get_sample_signal",
        "description": "Free sample: the same get_signal output for BTC-USD 1h, so you can evaluate quality before paying.",
        "inputSchema": {"type": "object", "properties": {}},
        "price": 0.0,
    },
    {
        "name": "list_pairs",
        "description": "Free: list supported pairs and timeframes.",
        "inputSchema": {"type": "object", "properties": {}},
        "price": 0.0,
    },
]

app = FastAPI(title="SignalPulse", version=APP_VERSION)


def _rpc_result(rpc_id, result):
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _rpc_error(rpc_id, code, message):
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}})


def _tool_result(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


async def _run_tool(name: str, args: dict) -> dict:
    if name in ("get_signal", "get_sample_signal"):
        pair = (args or {}).get("pair", "BTC-USD")
        tf = (args or {}).get("timeframe", "1h")
        if name == "get_sample_signal":
            pair, tf = "BTC-USD", "1h"
        candles = await market.fetch_candles(pair, tf)
        return signals.compute_signal(pair, tf, candles)
    if name == "get_market_pulse":
        return await signals.market_pulse(market.fetch_candles)
    if name == "get_gas_prices":
        return await all_gas()
    if name == "list_pairs":
        return {"pairs": list(market.PAIRS.keys()), "timeframes": list(market.TIMEFRAMES.keys())}
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
            "serverInfo": {"name": "signalpulse", "version": APP_VERSION},
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
            # paid replay: verify -> run -> settle
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

        # free tool, or DEV_MODE bypass
        try:
            result = await _run_tool(name, args)
        except ValueError as e:
            return _rpc_error(rpc_id, -32602, str(e))
        except Exception as e:
            return _rpc_error(rpc_id, -32603, f"internal: {e}")
        return _rpc_result(rpc_id, _tool_result(result))

    return _rpc_error(rpc_id, -32601, f"method not found: {method}")


@app.get("/")
async def root():
    return {
        "service": "SignalPulse",
        "version": APP_VERSION,
        "mcp_endpoint": "/mcp",
        "pay_to": PAY_TO,
        "dev_mode": DEV_MODE,
        "tools": [{"name": t["name"], "price_usdt": t["price"]} for t in TOOLS],
    }


@app.get("/health")
async def health():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
