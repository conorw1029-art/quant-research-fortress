# Personal Broker Automation Design

**Project:** The Fortress — Personal Broker Live Trading Layer  
**Date:** 2026-06-03  
**Status:** Design (not yet implemented)

---

## 1. Why a Personal Broker (Not a Prop Firm)?

The prop firm path (Topstep, Apex, Tradeify) has strict operational constraints that make full automation difficult:

| Constraint | Prop Firm | Personal Broker |
|---|---|---|
| Daily draw-down flat requirement | Yes — must go flat by EOD | No |
| Consistency rules | Max-day-profit rules at some firms | No |
| Account stacking limits | 1 strategy per account guidance | No enforced limit |
| Withdrawal lock-up | 1–3 months | Immediate |
| Automation allowed | Technically yes, but flagged at some firms | Yes, fully |
| Position overnight | Forbidden or restricted | Permitted |
| API access for order routing | Via NinjaTrader/Rithmic only | Native REST/FIX |

**Key insight:** The prop firm model was necessary to build a track record with minimal capital at risk. Once that track record exists, a personal broker account eliminates artificial constraints, enables true multi-strategy portfolios, allows overnight holds where the strategy edge extends beyond RTH, and removes the per-account consistency surveillance risk.

**Capital requirement:** A personal futures account requires approximately $5,000–$25,000 depending on the broker and instruments traded. This is the primary barrier vs. prop.

---

## 2. Three Broker Candidates

### 2.1 NinjaTrader Brokerage

- **API quality:** C# native NinjaScript environment. Python access requires a bridge (custom TCP socket or third-party wrapper). Not natively Python-friendly.
- **Simulator quality:** Best-in-class replay/simulation with tick-accurate historical data. Used throughout this project's OHLCV research.
- **Commissions (GC/SI):** $1.09/side flat through NT brokerage. Competitive.
- **Market data cost:** Included in platform fee or low monthly add-on.
- **Capital required:** $0 minimum for paper; ~$5,000 practical for GC/SI margin.
- **Automation friendliness:** High for C#; moderate for Python via bridge.
- **Notes:** All existing NinjaScript automation from the prop firm phase works here. Porting to Python requires maintaining a C# adapter or using the NT8 REST API add-on.

### 2.2 Interactive Brokers (IBKR)

- **API quality:** `ib_insync` (asyncio Python wrapper for TWS API) is the gold standard for Python algo trading. Full programmatic access to orders, fills, account state, P&L.
- **Bracket order support:** Full bracket OCO natively via `BracketOrder` in `ib_insync`.
- **Simulator quality:** Paper trading account available 24/7 with realistic fills.
- **Commissions (GC/SI):** ~$0.85/side (tiered, volume-dependent). Best long-term economics.
- **Market data cost:** Level 1 free for active traders; Level 2 CME subscription ~$30/month.
- **Capital required:** $10,000 practical minimum to avoid pattern-day-trader margin issues.
- **API documentation quality:** Excellent. `ib_insync` has active community.
- **Automation friendliness:** Highest of the three candidates.
- **Hardest to set up:** TWS or IB Gateway must run as a subprocess; connection management is non-trivial; separate paper vs. live port numbers; requires keeping a local desktop process alive.

### 2.3 Tradovate Personal Account

- **API quality:** REST + WebSocket API. Python `tradovate` client already exists in this codebase (`src/data/es_data_pipeline.py` and the Tradovate client modules).
- **Bracket order support:** Yes via `placeOrder` with attached stop/target legs.
- **Simulator quality:** Demo account with near-identical API to live. Existing paper trading pipeline already validated.
- **Commissions (GC/SI):** $0.79/side (membership plan) or $1.99/side (pay-per-trade). Monthly membership cost offsets at ~80 round-trips/month.
- **Market data cost:** CME data bundled with membership plan.
- **Capital required:** ~$3,000–$5,000 practical for micro futures; ~$12,000 for full GC.
- **API documentation quality:** Moderate. REST API is documented; WebSocket protocol requires reverse-engineering for some edge cases.
- **Automation friendliness:** High — existing codebase is built on Tradovate.

---

## 3. Broker Abstraction Layer

The system uses a layered abstraction so strategy logic never touches broker-specific code.

```
                       ┌──────────────────────────────────┐
                       │         Strategy Engine           │
                       │  (OFI_Continuation, CVD_VWAP …)   │
                       └────────────────┬─────────────────┘
                                        │  signal, position_size, stop, target
                                        ▼
                       ┌──────────────────────────────────┐
                       │       PortfolioCoordinator        │
                       │  - netting / conflict resolution  │
                       │  - per-account allocation        │
                       │  - max daily loss gate            │
                       └────────────────┬─────────────────┘
                                        │  order_request (instrument, side,
                                        │  qty, bracket_stop, bracket_target)
                                        ▼
                       ┌──────────────────────────────────┐
                       │        BrokerRiskGateway          │
                       │  - position limits                │
                       │  - kill switch check              │
                       │  - drawdown circuit breaker       │
                       │  - duplicate order prevention     │
                       └────────────────┬─────────────────┘
                                        │  validated order
                                        ▼
                       ┌──────────────────────────────────┐
                       │         BrokerAdapter             │
                       │      (abstract interface)         │
                       └──┬───────────┬───────────┬───────┘
                          │           │           │
               ┌──────────▼──┐ ┌─────▼──────┐ ┌─▼────────────────┐
               │ MockBroker  │ │ Tradovate   │ │  IBKRAdapter      │
               │ (unit tests)│ │ Adapter     │ │  (ib_insync)      │
               └─────────────┘ │ (existing)  │ └──────────────────┘
                               └─────────────┘
                                              ┌──────────────────┐
                                              │ NinjaTrader      │
                                              │ Adapter          │
                                              │ (C# bridge or    │
                                              │  NT8 REST add-on)│
                                              └──────────────────┘
```

### BrokerAdapter Interface (Python abstract base)

```python
class BrokerAdapter(ABC):
    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def place_bracket_order(
        self,
        symbol: str,
        side: str,          # "BUY" | "SELL"
        quantity: int,
        stop_price: float,
        target_price: float,
        order_type: str = "MKT",
    ) -> str:               # returns order_id
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_open_positions(self) -> List[Position]: ...

    @abstractmethod
    def get_account_state(self) -> AccountState: ...

    @abstractmethod
    def subscribe_fills(self, callback: Callable) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...
```

---

## 4. Safety Requirements Before Any Personal Broker Live Trading

The following gates must be verified before a single live order is placed:

### Gate 1: Bracket Orders Verified (paper)
- [ ] Place a bracket order (entry + stop + target) via the adapter
- [ ] Verify stop and target are attached as OCO legs
- [ ] Verify cancellation of one leg cancels the other
- [ ] Verify fill notification arrives via WebSocket/callback within 500ms

### Gate 2: State Persistence
- [ ] On process restart, the system reads open positions from the broker and reconciles against local state file
- [ ] Any position in broker but not in local state triggers an alert and manual review pause
- [ ] Any position in local state but not in broker triggers a forced-flat procedure

### Gate 3: Reconciliation
- [ ] End-of-session reconciliation runs within 60 seconds of market close
- [ ] Reconciliation diff is written to a log file with full position and fill details
- [ ] Any unreconciled difference halts automated trading until manually cleared

### Gate 4: Kill Switch
- [ ] A keyboard interrupt or SIGTERM triggers an immediate flat-all procedure
- [ ] A daily P&L threshold (configurable, default -$500) triggers a trading halt for the session
- [ ] A cumulative drawdown threshold (default -$1,000 for personal, same as prop DD constraint) triggers a halt until manual reset

### Gate 5: 30-Day Paper Proof
- [ ] Minimum 30 calendar days of paper trading with the live adapter (not the mock broker)
- [ ] Minimum 50 round-trip trades in the paper environment
- [ ] P&L is within 15% of the backtested simulation result (accounting for execution differences)
- [ ] Zero unhandled exceptions in the paper run logs

---

## 5. What Is NOT Yet Implemented

The following items are required before live personal-broker trading and are **not complete** as of this writing:

| Item | Status | Blocking? |
|---|---|---|
| `IBKRAdapter` class | Not started | No (Tradovate path available) |
| `NinjaTraderAdapter` Python bridge | Not started | No |
| `BrokerRiskGateway` full implementation | Partial (logic exists in RiskManager) | Yes |
| State persistence file format | Defined, not wired to broker adapters | Yes |
| End-of-session reconciliation script | Prototype only | Yes |
| Kill switch with flat-all sequence | Not connected to personal broker adapter | Yes |
| `PortfolioCoordinator` → BrokerAdapter integration | Not wired | Yes |
| 30-day paper proof | Not started | Yes |
| Personal broker account funded | Not started | Yes (capital needed) |

---

## 6. Deployment Sequence

### Phase 1: Paper Simulation (Months 1–2)
- Select broker: Tradovate (existing codebase, lowest friction) or IBKR (best API)
- Open demo/paper account
- Wire `PortfolioCoordinator` to `TradovateAdapter` (or `IBKRAdapter`)
- Run all 5 survivors in paper for 30 days minimum
- Validate P&L, fill quality, latency, reconciliation

### Phase 2: Micro Live (Month 3)
- Fund account with minimum capital (micro futures where possible)
- Run 1 strategy at micro size (MES instead of ES, MGC instead of GC where available)
- 30 days of micro live; confirm P&L within 15% of paper
- Confirm kill switch, reconciliation, state persistence all work under live conditions

### Phase 3: Scale (Month 4+)
- Promote to full-size contracts (GC, SI) on best-performing strategies
- Add strategies from the portfolio one at a time (one new strategy per 30-day review period)
- Scale to personal account limit based on risk constraints ($2,000 personal max DD per account)

---

## 7. Recommended Broker

**Primary recommendation: Tradovate Personal Account**

Rationale:
- Existing Python client code is battle-tested in this codebase
- Demo account path is already validated (dry-run and paper trading done)
- REST API eliminates the need for a running desktop application
- Lowest friction path from current state to live

**Fallback / long-term: IBKR**

Rationale:
- Best execution quality and lowest commissions at scale
- `ib_insync` is the most mature Python algo-trading library for futures
- Better for multi-instrument expansion beyond GC/SI
