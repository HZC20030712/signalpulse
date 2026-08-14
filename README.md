# SignalPulse

付费交易信号 MCP 服务（x402 v2，X Layer 结算）。面向 AI Agent 买家，按次收 USDT。

## 工具与定价

| 工具 | 价格 (USDT/次) | 说明 |
|---|---|---|
| `get_signal` | 0.05 | 方向/置信度/理由/入场/止损/目标位（EMA+MACD+RSI+ATR+成交量流） |
| `get_market_pulse` | 0.01 | BTC/ETH/SOL 市场状态快照 |
| `get_gas_prices` | 0.005 | 六链实时 gas（引流款） |
| `get_sample_signal` | 免费 | BTC-USD 1h 样例（引流） |
| `list_pairs` | 免费 | 支持的币对与周期 |

数据源：OKX 公开行情 API（无需鉴权）。支持 10 币对 × 5 时间框架。

## 本地运行

```bash
pip install -r requirements.txt

# 开发模式（跳过收款验证）
X402_DEV_MODE=true X402_PAY_TO=<你的收款地址> python server.py

# 生产模式（需要 OKX API 三件套，用于 facilitator verify/settle）
X402_PAY_TO=0x32bf81c00bb2bd9ceb087690a64f2d4408a924c4 \
OKX_API_KEY=... OKX_SECRET_KEY=... OKX_PASSPHRASE=... \
PUBLIC_URL=https://<你的域名> \
python server.py
```

端点：`POST /mcp`（MCP Streamable HTTP）。付费工具未携带 `PAYMENT-SIGNATURE` 时返回 HTTP 402 + `PAYMENT-REQUIRED`（x402 v2，scheme=exact，network=eip155:196，asset=USDT0）。

## 部署（Render 免费档）

1. 把本目录推到 GitHub 仓库
2. render.com → New Web Service → 选仓库
   - Build: `pip install -r requirements.txt`
   - Start: `python server.py`
3. 环境变量：`X402_PAY_TO`、`OKX_API_KEY`、`OKX_SECRET_KEY`、`OKX_PASSPHRASE`、`PUBLIC_URL=https://<分配到的域名>`
4. 验证：`curl https://<域名>/health`

## 上架 OKX.AI 市场

用 `onchainos` CLI 注册 ASP 身份并添加服务（serviceType=A2MCP，endpoint=`https://<域名>/mcp`，fee 按上表）。详见 okx-ai skill 的 identity-register 流程。

## 合规

所有输出含 "Informational only, not investment advice." 免责声明。
