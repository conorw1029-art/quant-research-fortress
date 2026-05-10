"""
FOMC Pre-Announcement Drift on RTY (Russell 2000 Futures)
===========================================================
Exact replication of H6 (ES FOMC drift) on RTY futures.
Same dates, same entry/exit logic, same param grid.
"""
from src.strategies.fomc_drift import FOMCDriftStrategy


class FOMCDriftRTYStrategy(FOMCDriftStrategy):
    name = "FOMC_Drift_RTY"
    description = "Pre-FOMC announcement drift on Russell 2000 futures"
    category = "calendar"