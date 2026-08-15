"""SentinelPulse — agent decision-safety MCP endpoint (x402 v2, X Layer).

tx_guard = pre-broadcast transaction final check (decode + rules + RPC sim),
trust_score / route_pick = deterministic scoring for agent-vs-agent decisions.
Every output carries evidence + formula, verifiable and re-runnable.
"""

import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import trustscore
import txguard
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
RESOURCE_URL = os.environ.get("PUBLIC_URL", "http://localhost:8002") + "/mcp"

TOOLS = [
    {
        "name": "tx_guard",
        "description": "Pre-broadcast transaction final check: decodes calldata, flags risky patterns (unlimited approval, setApprovalForAll, proxy upgrade, ownership change), and simulates the call on-chain. Returns allow/caution/block verdict with evidence. Price: 0.12 USDT per call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chain": {"type": "string", "description": "xlayer | ethereum"},
                "to": {"type": "string", "description": "Target contract address (0x...)"},
                "calldata": {"type": "string", "description": "Raw transaction calldata hex"},
                "from": {"type": "string", "description": "Optional sender address for simulation"},
            },
            "required": ["chain", "to", "calldata"],
        },
        "price": 0.12,
    },
    {
        "name": "trust_score",
        "description": "Deterministic trust scoring for candidate service providers. Supply candidate data (sales, rating, online, days listed) and receive a ranked 0-100 score with risk flags and the formula. Price: 0.08 USDT per call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidates": {"type": "array", "description": "Array of {name, sales?, rating?, online?, days_listed?}"},
            },
            "required": ["candidates"],
        },
        "price": 0.08,
    },
    {
        "name": "route_pick",
        "description": "Ranks candidate services for a task by capability match, price fit and trust, returning the recommended pick, alternatives and fallbacks with an evidence receipt. Price: 0.08 USDT per call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task goal description"},
                "budget": {"type": "number", "description": "Per-call budget in USDT"},
                "candidates": {"type": "array", "description": "Array of {name, fee?, capabilities?: [], trust?: 0-100}"},
                "capabilities": {"type": "array", "description": "Required capability keywords, e.g. ['signal','sizing']"},
            },
            "required": ["task", "budget", "candidates"],
        },
        "price": 0.08,
    },
    {
        "name": "guard_sample",
        "description": "Free sample: tx_guard verdict on a real unlimited-approval transaction, so you can evaluate output quality before paying.",
        "inputSchema": {"type": "object", "properties": {}},
        "price": 0.0,
    },
]

app = FastAPI(title="SentinelPulse", version=APP_VERSION)

SAMPLE_CALLDATA = "0x095ea7b3000000000000000000000000000000000000000000000000000000000000dead" + "f" * 64


def _rpc_result(rpc_id, result):
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _rpc_error(rpc_id, code, message):
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}})


def _tool_result(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


async def _run_tool(name: str, args: dict) -> dict:
    args = args or {}
    if name == "tx_guard":
        return await txguard.tx_guard(
            str(args["chain"]), str(args["to"]), str(args["calldata"]),
            args.get("from"),
        )
    if name == "trust_score":
        return trustscore.trust_score(list(args["candidates"]))
    if name == "route_pick":
        return trustscore.route_pick(
            str(args["task"]), float(args["budget"]),
            list(args["candidates"]), list(args.get("capabilities") or []),
        )
    if name == "guard_sample":
        return await txguard.tx_guard(
            "ethereum", "0x000000000000000000000000000000000000dead", SAMPLE_CALLDATA, None
        )
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
            "serverInfo": {"name": "sentinelpulse", "version": APP_VERSION},
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
            return _rpc_error(rpc_id, -32602, f"unknown tool: {name}")

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
                return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": _tool_result(result)}, headers=headers)
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
        "service": "SentinelPulse",
        "version": APP_VERSION,
        "mcp_endpoint": "/mcp",
        "pay_to": PAY_TO,
        "dev_mode": DEV_MODE,
        "tools": [{"name": t["name"], "price_usdt": t["price"]} for t in TOOLS],
    }


@app.get("/health")
async def health():
    return {"ok": True}


LLMS_TXT = """# SentinelPulse — Agent Decision-Safety API

> Pre-broadcast transaction final check, deterministic trust scoring, and service routing for agent-to-agent decisions. Every output carries evidence + formula; re-runnable and verifiable. Settles per call on X Layer.

## Endpoint
- MCP (Streamable HTTP): https://sentinelpulse-b80t.onrender.com/mcp

## Tools
- tx_guard (0.12 USDT/call): decodes calldata, flags risky patterns (unlimited approval, setApprovalForAll, proxy upgrade, ownership change), simulates on-chain, returns allow/caution/block with evidence.
- trust_score (0.08): deterministic 0-100 trust ranking of candidate service providers with risk flags.
- route_pick (0.08): ranks candidate services by capability match, price fit and trust; returns recommendation, alternatives, fallbacks with evidence receipt.
- guard_sample (free): sample tx_guard verdict on an unlimited-approval transaction.

## Payment
x402 v2, scheme=exact, network=eip155:196 (X Layer), asset=USDT0.

## Example
curl -X POST https://sentinelpulse-b80t.onrender.com/mcp -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"tx_guard","arguments":{"chain":"ethereum","to":"0x000000000000000000000000000000000000dead","calldata":"0x095ea7b3"}}}'
"""


@app.get("/llms.txt")
async def llms_txt():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(LLMS_TXT)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8002")))
