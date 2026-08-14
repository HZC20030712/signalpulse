"""Multi-chain gas prices via public JSON-RPC endpoints (eth_gasPrice)."""

import asyncio

import httpx

CHAINS = {
    "xlayer": {"rpc": ["https://rpc.xlayer.tech"], "native": "OKB", "note": "X Layer is gas-free for eligible AA txs"},
    "ethereum": {"rpc": ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com"], "native": "ETH"},
    "base": {"rpc": ["https://mainnet.base.org", "https://base-rpc.publicnode.com"], "native": "ETH"},
    "bsc": {"rpc": ["https://bsc-dataseed.binance.org", "https://bsc-rpc.publicnode.com"], "native": "BNB"},
    "polygon": {"rpc": ["https://polygon-bor-rpc.publicnode.com", "https://polygon-rpc.com"], "native": "POL"},
    "arbitrum": {"rpc": ["https://arb1.arbitrum.io/rpc", "https://arbitrum-one-rpc.publicnode.com"], "native": "ETH"},
}


async def _gas_one(client: httpx.AsyncClient, name: str, cfg: dict) -> tuple:
    last_err = "unreachable"
    for rpc in cfg["rpc"]:
        try:
            r = await client.post(
                rpc,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice", "params": []},
                timeout=10,
            )
            wei = int(r.json()["result"], 16)
            return name, {
                "gas_gwei": round(wei / 1e9, 4),
                "native_token": cfg["native"],
                **({"note": cfg["note"]} if "note" in cfg else {}),
            }
        except Exception as e:
            last_err = str(e)
    return name, {"error": last_err}


async def all_gas() -> dict:
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_gas_one(client, n, c) for n, c in CHAINS.items()])
    return {
        "chains": dict(results),
        "tip": "Route cost-sensitive transactions to the cheapest chain; X Layer is often near-zero.",
    }
