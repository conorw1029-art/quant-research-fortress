\# Phase 2 Automation Layer



Automation on top of the fortress. You now drop in a new strategy,

add one line to the registry, and run a single command to test it

with full walk-forward, DSR correction, and automatic zoo logging.



\## Files



\### New additions (7 files)

\- `src/zoo/database.py` — append-only JSONL database for test results

\- `src/zoo/registry.py` — central catalog of all strategies

\- `src/zoo/\_\_init\_\_.py`

\- `src/strategies/template.py` — copy-paste boilerplate for new strategies

\- `src/strategies/bollinger\_rsi.py` — first new strategy (A1)

\- `src/strategies/\_\_init\_\_.py`

\- `src/backtesting/\_\_init\_\_.py`



\### Top-level scripts

\- `run\_strategy.py` — unified runner, tests any strategy and logs to zoo

\- `zoo\_analyze.py` — query/interpret zoo results



\## Installation



Copy all files preserving the directory structure into your `04\_codebase/`:



```

04\_codebase/

├── run\_strategy.py           # NEW

├── zoo\_analyze.py            # NEW

├── integration\_test.py       # EXISTING

└── src/

&#x20;   ├── backtesting/

&#x20;   │   ├── \_\_init\_\_.py       # NEW (empty)

&#x20;   │   ├── cost\_model.py     # EXISTING

&#x20;   │   ├── metrics.py        # EXISTING

&#x20;   │   └── walk\_forward.py   # EXISTING

&#x20;   ├── data/                 # EXISTING (unchanged)

&#x20;   ├── strategies/

&#x20;   │   ├── \_\_init\_\_.py       # NEW (empty)

&#x20;   │   ├── base.py           # EXISTING

&#x20;   │   ├── bollinger\_rsi.py  # NEW (A1 strategy)

&#x20;   │   ├── fomc\_drift.py     # EXISTING

&#x20;   │   ├── rsi\_meanrev.py    # EXISTING

&#x20;   │   └── template.py       # NEW

&#x20;   ├── utils/                # EXISTING (unchanged)

&#x20;   └── zoo/

&#x20;       ├── \_\_init\_\_.py       # NEW

&#x20;       ├── database.py       # NEW

&#x20;       └── registry.py       # NEW

```



\## First run — verify everything works



```powershell

cd C:\\Users\\conor\\iCloudDrive\\Trading\\quant-research\\04\_codebase



\# 1. Show the registry

.\\..\\venv\\Scripts\\python.exe run\_strategy.py --registry



\# 2. Test a single strategy (writes to ../05\_backtests/zoo.jsonl)

.\\..\\venv\\Scripts\\python.exe run\_strategy.py --key bollinger\_rsi



\# 3. View results

.\\..\\venv\\Scripts\\python.exe zoo\_analyze.py



\# 4. Re-run existing strategies with a new cost model

.\\..\\venv\\Scripts\\python.exe run\_strategy.py --key rsi\_meanrev --cost-scenario conservative

.\\..\\venv\\Scripts\\python.exe run\_strategy.py --key fomc\_drift --cost-scenario conservative



\# 5. Test everything active at once

.\\..\\venv\\Scripts\\python.exe run\_strategy.py --all



\# 6. See only survivors

.\\..\\venv\\Scripts\\python.exe zoo\_analyze.py --survivors



\# 7. See history for one strategy

.\\..\\venv\\Scripts\\python.exe zoo\_analyze.py --strategy fomc\_drift

```



\## Adding a new strategy — 4 steps



1\. \*\*Copy the template:\*\* `cp src/strategies/template.py src/strategies/my\_new\_strategy.py`



2\. \*\*Edit:\*\*

&#x20;  - Rename class `TemplateStrategy` → `MyNewStrategy`

&#x20;  - Set `name`, `category`, `timeframe`, `description`

&#x20;  - Define `param\_grid` (keep small: 2-3 params, 3-5 values)

&#x20;  - Implement `generate\_signals(data)` → returns `pd.Series` of {-1, 0, 1}

&#x20;  - Optionally override `signals\_to\_trades()` for custom exits



3\. \*\*Register\*\* in `src/zoo/registry.py`:

&#x20;  ```python

&#x20;  StrategyEntry(

&#x20;      key="my\_new\_strategy",

&#x20;      module\_path="src.strategies.my\_new\_strategy",

&#x20;      class\_name="MyNewStrategy",

&#x20;      category="mean\_reversion",   # or trend, calendar, volume, etc.

&#x20;      status=Status.EXPERIMENTAL,

&#x20;      test\_method=TestMethod.WALK\_FORWARD,

&#x20;      timeframe="5min",

&#x20;      notes="Brief thesis.",

&#x20;  ),

&#x20;  ```



4\. \*\*Run:\*\* `python run\_strategy.py --key my\_new\_strategy`



\## Cost scenarios



Each run can specify the cost assumption:



| Scenario      | Slippage/side | MES cost/RT |

|---------------|---------------|-------------|

| `zero`        | 0 ticks       | 0.25 pts    |

| `optimistic`  | 0 ticks       | 0.25 pts    |

| `realistic`   | 1 tick        | 0.75 pts    |

| `conservative`| 2 ticks       | 1.25 pts    |



Default is `realistic`. Always test surviving strategies under `conservative` before paper trading.



\## Key design decisions



\- \*\*JSONL storage\*\* — append-only, human-readable, crash-safe.

&#x20; Query with `pandas.read\_json(path, lines=True)` or `jq`.



\- \*\*Registry as source of truth\*\* — not filesystem scanning. Explicit

&#x20; registration prevents accidental runs of unfinished strategies.



\- \*\*Data caching\*\* — `run\_strategy.py --all` loads data ONCE across all

&#x20; strategies for the same timeframe. Saves minutes per run.



\- \*\*Zoo never deletes\*\* — rejected strategies stay in history. If you

&#x20; improve the cost model or fix a bug, re-run and compare entries.



\- \*\*Error records\*\* — if a strategy crashes, the failure is recorded

&#x20; with traceback in the zoo. No silent failures.



\## What this does NOT do (yet)



\- \*\*Regime detection\*\* — combining strategies based on current market

&#x20; conditions. Will add after we have 5+ survivors.

\- \*\*Ensemble construction\*\* — correlation-aware portfolio combining.

&#x20; Premature without more survivors.

\- \*\*Paper trading bridge\*\* — live execution. Phase 4.

\- \*\*VPS automation\*\* — scheduled unattended runs. Phase 5.



The goal of this phase is to make \*\*adding and testing a new hypothesis a 5-minute operation\*\* instead of a day of custom scripting.

