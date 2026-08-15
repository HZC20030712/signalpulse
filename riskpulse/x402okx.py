"""x402 v2 seller side: 402 challenge builder + OKX facilitator client.

Spec (OKX Onchain OS payments docs):
- 402 response carries PAYMENT-REQUIRED header: base64(JSON{x402Version:2, resource, accepts[]})
- Buyer replays with PAYMENT-SIGNATURE header: base64(JSON{x402Version:2, resource, accepted, payload})
- Seller: POST /api/v6/pay/x402/verify then /settle (HMAC-SHA256 auth headers)
- Success response carries PAYMENT-RESPONSE header: base64(settle result)
- Network: eip155:196 (X Layer). Fee token: USDT0 (6 decimals).
"""

import base64
import hashlib
import hmac
import json
import os
import time

import httpx

OKX_FACILITATOR_BASE = os.environ.get("OKX_FACILITATOR_BASE", "https://web3.okx.com")
X402_API = "/api/v6/pay/x402"

NETWORK = "eip155:196"
ASSET_USDT0 = os.environ.get(
    "X402_ASSET", "0x779ded0c9e1022225f8e0630b35a9b54be713736"
)
ASSET_EXTRA = {"name": "USD₮0", "version": "1", "transferMethod": "eip3009"}
TOKEN_DECIMALS = 6

PAY_TO = os.environ.get("X402_PAY_TO", "")
DEV_MODE = os.environ.get("X402_DEV_MODE", "").lower() in ("1", "true", "yes")


def _b64e(obj) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def _b64d(s: str):
    return json.loads(base64.b64decode(s).decode())


def to_atomic(usd: float) -> str:
    return str(int(round(usd * 10**TOKEN_DECIMALS)))


def build_challenge(resource_url: str, description: str, price_usd: float) -> dict:
    """402 envelope (x402 v2)."""
    return {
        "x402Version": 2,
        "error": "Payment required",
        "resource": {"url": resource_url, "description": description, "mimeType": "application/json"},
        "accepts": [
            {
                "scheme": "exact",
                "network": NETWORK,
                "amount": to_atomic(price_usd),
                "payTo": PAY_TO,
                "maxTimeoutSeconds": 86400,
                "asset": ASSET_USDT0,
                "extra": dict(ASSET_EXTRA),
            }
        ],
    }


def challenge_headers_and_body(resource_url: str, description: str, price_usd: float):
    env = build_challenge(resource_url, description, price_usd)
    return {"PAYMENT-REQUIRED": _b64e(env)}, env


class FacilitatorError(Exception):
    pass


class OKXFacilitator:
    """Thin client for OKX /api/v6/pay/x402/* with HMAC-SHA256 auth."""

    def __init__(self):
        self.api_key = os.environ.get("OKX_API_KEY", "")
        self.secret = os.environ.get("OKX_SECRET_KEY", "")
        self.passphrase = os.environ.get("OKX_PASSPHRASE", "")
        if not (self.api_key and self.secret and self.passphrase):
            raise FacilitatorError("OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE not configured")

    def _headers(self, method: str, path: str, body: str) -> dict:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        msg = ts + method.upper() + path + body
        sign = base64.b64encode(
            hmac.new(self.secret.encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload)
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                OKX_FACILITATOR_BASE + path,
                content=body,
                headers=self._headers("POST", path, body),
            )
            r.raise_for_status()
            data = r.json()
        if str(data.get("code")) != "0":
            raise FacilitatorError(f"facilitator error: {data.get('msg')} ({path})")
        return data.get("data") or {}

    async def verify(self, payment_payload: dict, requirements: dict) -> dict:
        return await self._post(
            f"{X402_API}/verify",
            {
                "x402Version": 2,
                "paymentPayload": payment_payload,
                "paymentRequirements": requirements,
            },
        )

    async def settle(self, payment_payload: dict, requirements: dict) -> dict:
        return await self._post(
            f"{X402_API}/settle",
            {
                "x402Version": 2,
                "paymentPayload": payment_payload,
                "paymentRequirements": requirements,
            },
        )


def decode_payment_signature(header_value: str) -> dict:
    return _b64d(header_value)


def encode_payment_response(settle_result: dict) -> str:
    return _b64e(settle_result)
