"""
tick_news_monitor.py — Economic Calendar & News Bias Monitor
=============================================================
Provides daily market bias and news-window awareness for the live executor.

Data sources (ALL FREE, no API key required):
  1. ForexFactory economic calendar JSON (weekly events, times, impact, actual vs forecast)
  2. MarketWatch RSS feed (top financial headlines)
  3. Reuters RSS feed (business news)

Key outputs:
  - in_news_window()  → True if within N minutes of high-impact event
  - get_daily_bias()  → +1 (ES/NQ bullish), -1 (ES/NQ bearish), 0 (neutral)
  - get_event_schedule() → list of today's upcoming events
  - print_dashboard() → full status display

Usage:
  monitor = NewsMonitor()
  if monitor.in_news_window():
      print("NEWS WINDOW — skip new entries!")
  bias = monitor.get_daily_bias()
  print(monitor.get_status_line())

Run standalone for monitoring mode:
  python tick_news_monitor.py --poll 300  (check every 5 min)
"""

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


# ── Configuration ─────────────────────────────────────────────────────────────

# High-impact USD events that directly move ES, NQ, GC
HIGH_IMPACT_EVENTS = {
    "Non-Farm Employment Change", "Non-Farm Payrolls", "NFP",
    "CPI", "Consumer Price Index", "Core CPI",
    "PPI", "Producer Price Index",
    "FOMC", "Federal Funds Rate", "Fed Rate Decision",
    "GDP", "Gross Domestic Product",
    "ISM Manufacturing PMI", "ISM Services PMI",
    "Retail Sales", "Core Retail Sales",
    "Unemployment Rate", "Initial Jobless Claims",
    "PCE Price Index", "Core PCE",
    "Trade Balance",
    "Consumer Confidence",
    "ADP Non-Farm Employment Change",
}

MEDIUM_IMPACT_EVENTS = {
    "Building Permits", "Housing Starts",
    "Durable Goods Orders", "Factory Orders",
    "Industrial Production", "Capacity Utilization",
    "JOLTS Job Openings",
    "Michigan Consumer Sentiment",
}

# Bullish USD surprises → ES/NQ bearish, GC bearish
# Bearish USD surprises → ES/NQ bullish, GC bullish (risk-off reversal)
# Note: this is the SHORT-TERM immediate reaction direction

# RSS feeds for headlines
RSS_FEEDS = {
    "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "Reuters":     "https://feeds.reuters.com/reuters/businessNews",
    "Yahoo":       "https://finance.yahoo.com/rss/topfinstories",
}

# Keyword sentiment scoring for ES/NQ direction
BULLISH_KEYWORDS = {
    "rally", "surge", "soar", "gain", "rise", "rises", "gained",
    "beats", "beat", "exceeds", "record high", "strong jobs",
    "rate cut", "stimulus", "dovish", "easing", "accommodation",
    "soft landing", "expansion", "growth beats",
}

BEARISH_KEYWORDS = {
    "crash", "plunge", "tumble", "fall", "drop", "drops", "fell",
    "misses", "miss", "below", "recession", "contraction", "weak",
    "rate hike", "hawkish", "tightening", "inflation", "hot inflation",
    "layoffs", "jobless", "slowdown", "default", "crisis",
}

# GC has OPPOSITE bias to ES/NQ for most macro events
# (gold up = risk off = ES down; gold down = risk on = ES up)
GOLD_BULLISH_KEYWORDS = {
    "war", "conflict", "geopolitical", "uncertainty", "recession",
    "crisis", "safe haven", "dollar falls", "fed cut", "rate cut",
    "inflation surge", "stagflation",
}


class NewsEvent:
    def __init__(self, title: str, event_time: datetime, currency: str,
                 impact: str, forecast=None, previous=None, actual=None):
        self.title    = title
        self.time     = event_time
        self.currency = currency
        self.impact   = impact
        self.forecast = forecast
        self.previous = previous
        self.actual   = actual

    @property
    def has_result(self) -> bool:
        return self.actual is not None and self.actual != ""

    @property
    def beat_forecast(self) -> Optional[bool]:
        """True if actual beats forecast (higher), False if misses, None if no data."""
        if not self.has_result or self.forecast is None:
            return None
        try:
            a = float(str(self.actual).replace("%", "").replace("K", "000").replace("M", "000000"))
            f = float(str(self.forecast).replace("%", "").replace("K", "000").replace("M", "000000"))
            return a > f
        except (ValueError, AttributeError):
            return None

    def bias_score(self) -> int:
        """
        +1 = USD strengthening event (beats = hawkish)
        -1 = USD weakening event (misses = dovish)
         0 = neutral / no result yet
        Note: For ES/NQ, stronger USD often means lower stocks short-term.
        """
        beat = self.beat_forecast
        if beat is None:
            return 0
        title_lower = self.title.lower()
        # For unemployment/jobless claims: higher actual = bad (USD negative)
        inverse_events = {"unemployment", "jobless", "claims"}
        is_inverse = any(k in title_lower for k in inverse_events)
        if is_inverse:
            return -1 if beat else 1
        return 1 if beat else -1

    def minutes_until(self) -> float:
        now = datetime.now(timezone.utc)
        return (self.time - now).total_seconds() / 60

    def __repr__(self):
        result_str = f" → actual={self.actual}" if self.has_result else ""
        return f"[{self.impact}] {self.title} @ {self.time.strftime('%H:%M UTC')} (USD){result_str}"


class NewsMonitor:
    def __init__(self, cache_minutes: int = 60):
        self.events:        list[NewsEvent] = []
        self.headlines:     list[dict]      = []
        self.last_calendar  = None
        self.last_rss       = None
        self.cache_minutes  = cache_minutes
        self.errors:        list[str]       = []

        self.refresh()

    # ── Calendar Fetching ─────────────────────────────────────────────────────

    def fetch_calendar(self) -> bool:
        """Fetch ForexFactory economic calendar for current week."""
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            self.errors.append(f"Calendar fetch failed: {e}")
            return False

        events = []
        for item in data:
            try:
                currency = item.get("country", "")
                if currency not in ("USD", ""):
                    continue
                impact = item.get("impact", "")
                if impact not in ("High", "Medium"):
                    continue

                date_str = item.get("date", "")
                if not date_str:
                    continue

                # Parse ISO datetime
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                event = NewsEvent(
                    title    = item.get("title", "Unknown"),
                    event_time = dt,
                    currency = currency,
                    impact   = impact,
                    forecast = item.get("forecast"),
                    previous = item.get("previous"),
                    actual   = item.get("actual"),
                )
                events.append(event)
            except Exception:
                continue

        self.events = events
        self.last_calendar = datetime.now(timezone.utc)
        return True

    # ── RSS Headline Fetching ─────────────────────────────────────────────────

    def fetch_headlines(self) -> bool:
        """Fetch financial headlines from free RSS feeds."""
        all_headlines = []
        for source, url in RSS_FEEDS.items():
            try:
                req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req, timeout=8) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                root = ET.fromstring(raw)
                items = root.findall(".//item")[:15]
                for item in items:
                    title = (item.findtext("title") or "").strip()
                    pubdate = (item.findtext("pubDate") or "").strip()
                    if title:
                        all_headlines.append({
                            "source":  source,
                            "title":   title,
                            "pubdate": pubdate,
                        })
            except Exception as e:
                self.errors.append(f"RSS {source} failed: {e}")
                continue

        self.headlines   = all_headlines
        self.last_rss    = datetime.now(timezone.utc)
        return len(all_headlines) > 0

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self, force: bool = False) -> bool:
        now = datetime.now(timezone.utc)
        if (force or self.last_calendar is None or
                (now - self.last_calendar).seconds > self.cache_minutes * 60):
            self.fetch_calendar()

        if (force or self.last_rss is None or
                (now - self.last_rss).seconds > 15 * 60):  # headlines every 15 min
            self.fetch_headlines()

        return self.last_calendar is not None

    # ── Analysis Methods ─────────────────────────────────────────────────────

    def get_todays_events(self, min_impact: str = "High") -> list[NewsEvent]:
        """Return today's events (UTC date) filtered by minimum impact."""
        today = datetime.now(timezone.utc).date()
        impact_levels = {"High": 2, "Medium": 1, "Low": 0}
        min_level     = impact_levels.get(min_impact, 1)
        return [
            e for e in self.events
            if e.time.date() == today
            and impact_levels.get(e.impact, 0) >= min_level
            and e.currency == "USD"
        ]

    def get_upcoming_events(self, hours_ahead: float = 4.0) -> list[NewsEvent]:
        """Return events happening in the next N hours."""
        cutoff = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
        now    = datetime.now(timezone.utc)
        return [
            e for e in self.events
            if e.currency == "USD"
            and now <= e.time <= cutoff
        ]

    def in_news_window(self, minutes_before: int = 30,
                       minutes_after: int = 30) -> tuple[bool, Optional[NewsEvent]]:
        """
        Returns (True, event) if currently in a high-impact news window.
        Avoids new entries during this period.
        """
        self.refresh()
        now = datetime.now(timezone.utc)
        for event in self.events:
            if event.impact != "High" or event.currency != "USD":
                continue
            mins = event.minutes_until()
            if -minutes_after <= mins <= minutes_before:
                return True, event
        return False, None

    def get_daily_bias(self) -> dict:
        """
        Compute daily bias for ES/NQ and GC based on:
        1. Economic data beats/misses today
        2. Headline sentiment (simple keyword scoring)

        Returns:
          {
            "es_nq_bias":  +1/0/-1  (long/neutral/short)
            "gc_bias":     +1/0/-1
            "score":       int      (raw score)
            "events_used": int
            "headline_sentiment": float
            "reason": str
          }
        """
        self.refresh()
        today_events = self.get_todays_events(min_impact="High")

        # Score from economic data
        econ_score = sum(e.bias_score() for e in today_events if e.has_result)
        events_used = sum(1 for e in today_events if e.has_result)

        # Score from headlines
        headline_score = self._score_headlines()

        total_score = econ_score + (1 if headline_score > 0.3 else -1 if headline_score < -0.3 else 0)

        # ES/NQ directional logic
        # USD strong (positive score) → initial bearish ES/NQ reaction, bullish reversal
        # USD weak (negative score)   → ES/NQ bullish
        # Note: this is oversimplified; real logic depends on magnitude and context
        if total_score >= 1:
            es_nq_bias =  1  # Surprisingly strong economy → stocks can rally
            gc_bias    = -1  # Strong economy = less need for safe haven
            reason     = "Economic data beating expectations — USD strong, ES/NQ mildly bullish"
        elif total_score <= -1:
            es_nq_bias = -1  # Weak data → recession fears → stocks down
            gc_bias    =  1  # Risk off → gold up
            reason     = "Economic data missing expectations — ES/NQ bearish bias, GC bullish"
        else:
            es_nq_bias = 0
            gc_bias    = 0
            reason     = "Neutral — no strong economic bias today"

        return {
            "es_nq_bias":          es_nq_bias,
            "gc_bias":             gc_bias,
            "score":               total_score,
            "econ_score":          econ_score,
            "headline_sentiment":  headline_score,
            "events_used":         events_used,
            "reason":              reason,
            "today_events":        [str(e) for e in today_events],
        }

    def _score_headlines(self) -> float:
        """Score headlines for ES/NQ sentiment. Returns -1 to +1."""
        if not self.headlines:
            return 0.0

        score = 0.0
        count = 0
        for h in self.headlines[:20]:
            title = h["title"].lower()
            bull_hits = sum(1 for kw in BULLISH_KEYWORDS if kw in title)
            bear_hits = sum(1 for kw in BEARISH_KEYWORDS if kw in title)
            if bull_hits or bear_hits:
                score += (bull_hits - bear_hits)
                count += 1

        if count == 0:
            return 0.0
        raw = score / count
        return max(-1.0, min(1.0, raw))

    def get_status_line(self) -> str:
        """One-line summary for display in executor header."""
        in_window, event = self.in_news_window()
        if in_window and event:
            mins = event.minutes_until()
            prefix = "PRE" if mins > 0 else "POST"
            return f"[NEWS-WINDOW {prefix} | {event.title} {abs(int(mins))}min]"

        upcoming = self.get_upcoming_events(hours_ahead=2)
        if upcoming:
            next_evt = upcoming[0]
            mins     = next_evt.minutes_until()
            return f"[Next: {next_evt.title} in {int(mins)}min]"

        bias = self.get_daily_bias()
        if bias["es_nq_bias"] != 0:
            direction = "BULL" if bias["es_nq_bias"] > 0 else "BEAR"
            return f"[Daily bias: ES/NQ {direction} | score={bias['score']}]"

        return "[No significant news events]"

    def print_dashboard(self):
        """Full status display."""
        now = datetime.now(timezone.utc)
        print(f"\n{'='*70}")
        print(f"  NEWS MONITOR — {now.strftime('%Y-%m-%d %H:%M')} UTC")
        print(f"{'='*70}")

        in_window, event = self.in_news_window()
        if in_window:
            print(f"\n  *** NEWS WINDOW ACTIVE — AVOID NEW ENTRIES ***")
            print(f"  Event: {event}")

        today = self.get_todays_events("High")
        if today:
            print(f"\n  Today's High-Impact USD Events:")
            for e in sorted(today, key=lambda x: x.time):
                result = f"Actual: {e.actual}" if e.has_result else "Pending"
                beat   = {True: "BEAT", False: "MISS", None: ""}[e.beat_forecast]
                print(f"    {e.time.strftime('%H:%M')}  {e.title:<40}  {result}  {beat}")
        else:
            print(f"\n  No high-impact USD events today")

        upcoming = self.get_upcoming_events(hours_ahead=4)
        if upcoming:
            print(f"\n  Upcoming (next 4 hours):")
            for e in upcoming:
                print(f"    {e.time.strftime('%H:%M')}  [{e.impact}] {e.title}")

        bias = self.get_daily_bias()
        print(f"\n  Daily Bias:")
        print(f"    ES/NQ: {'LONG' if bias['es_nq_bias']>0 else 'SHORT' if bias['es_nq_bias']<0 else 'NEUTRAL'}")
        print(f"    GC:    {'LONG' if bias['gc_bias']>0 else 'SHORT' if bias['gc_bias']<0 else 'NEUTRAL'}")
        print(f"    Score: {bias['score']} ({bias['events_used']} events counted)")
        print(f"    Reason: {bias['reason']}")

        if self.headlines:
            print(f"\n  Top Headlines ({len(self.headlines)} fetched):")
            for h in self.headlines[:5]:
                print(f"    [{h['source']}] {h['title'][:75]}")

        if self.errors:
            print(f"\n  Warnings: {self.errors[-3:]}")

        print()

    def get_bias_for_symbol(self, base_symbol: str) -> int:
        """Returns +1 / 0 / -1 directional bias for a specific symbol."""
        bias = self.get_daily_bias()
        if base_symbol in ("ES", "NQ"):
            return bias["es_nq_bias"]
        elif base_symbol == "GC":
            return bias["gc_bias"]
        return 0  # SI, others — no strong directional model


def main():
    parser = argparse.ArgumentParser(description="News Monitor")
    parser.add_argument("--poll",     type=int, default=0,   help="Poll interval seconds (0=once)")
    parser.add_argument("--window",   action="store_true",   help="Only show if in news window")
    parser.add_argument("--bias",     action="store_true",   help="Only show daily bias")
    parser.add_argument("--json",     action="store_true",   help="Output as JSON")
    args = parser.parse_args()

    monitor = NewsMonitor()

    def run_once():
        monitor.refresh(force=True)

        if args.window:
            in_w, evt = monitor.in_news_window()
            if args.json:
                print(json.dumps({"in_window": in_w, "event": str(evt) if evt else None}))
            else:
                print(f"In news window: {in_w}")
                if evt:
                    print(f"Event: {evt}")
            return

        if args.bias:
            bias = monitor.get_daily_bias()
            if args.json:
                print(json.dumps(bias, indent=2))
            else:
                print(f"ES/NQ bias: {bias['es_nq_bias']}  GC bias: {bias['gc_bias']}")
                print(f"Reason: {bias['reason']}")
            return

        monitor.print_dashboard()

    run_once()

    if args.poll > 0:
        print(f"Polling every {args.poll}s... (Ctrl+C to stop)")
        while True:
            time.sleep(args.poll)
            run_once()


if __name__ == "__main__":
    main()
