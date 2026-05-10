"""
FOMC Pre-Announcement Drift on NQ (Nasdaq-100 Futures)
========================================================
Exact replication of H6 (ES FOMC drift) on NQ futures.
Same dates, same entry/exit logic, same param grid.
"""
from src.strategies.fomc_drift import FOMCDriftStrategy


class FOMCDriftNQStrategy(FOMCDriftStrategy):
    name = "FOMC_Drift_NQ"
    description = "Pre-FOMC announcement drift on Nasdaq-100 futures"
    category = "calendar"