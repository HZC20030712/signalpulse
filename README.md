# Pulse Tool Suite — per-call crypto APIs for AI agents

A portfolio of deterministic, per-call paid APIs for autonomous agents, settled on X Layer via x402 v2 (scheme=exact, asset=USDT0). Every output is re-runnable and verifiable.

| Brand | Category | Endpoint | Tools & price |
|---|---|---|---|
| **SignalPulse** | trading signal | `https://signalpulse.onrender.com/mcp` | `get_signal` 0.05 · `get_market_pulse` 0.01 · `get_gas_prices` 0.005 · `get_sample_signal` free · `list_pairs` free |
| **RiskPulse** | risk math | `https://riskpulse-priz.onrender.com/mcp` | `position_size` 0.01 · `liquidation_gate` 0.01 · `funding_scan` 0.01 · `get_sample` free |
| **SentinelPulse** | decision safety | `https://sentinelpulse-b80t.onrender.com/mcp` | `tx_guard` 0.12 · `trust_score` 0.08 · `route_pick` 0.08 · `guard_sample` free |

## SignalPulse — trading signal API

Multi-factor trading signal for BTC/ETH/SOL and more: direction (long/short/flat), confidence 0-1, entry/stop-loss/targets from EMA, MACD, RSI, ATR and volume-flow on live OKX market data. Also market regime snapshots and multi-chain gas prices.

## RiskPulse — risk-math API

Deterministic position sizing via Kelly criterion with risk-of-ruin estimate; perpetual liquidation price and safety distance; and a market-wide funding-rate arbitrage scan. Every result carries its formula and inputs.

## SentinelPulse — decision-safety API

Pre-broadcast transaction final check: calldata decoding, risky-pattern detection (unlimited approval, setApprovalForAll, proxy upgrade), and on-chain simulation returning allow/caution/block with evidence. Plus deterministic trust scoring and service routing for agent-to-agent decisions.

## Calling convention

All endpoints are MCP (Streamable HTTP). Paid tools return HTTP 402 + `PAYMENT-REQUIRED` (x402 v2) until the buyer replays with a `PAYMENT-SIGNATURE` header.

```bash
curl -X POST https://signalpulse.onrender.com/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_signal","arguments":{"pair":"BTC-USD","timeframe":"1h"}}}'
```

Machine-readable catalogs: `https://<endpoint-host>/llms.txt`

## Compliance

All outputs carry an "Informational only, not investment advice." disclaimer.
