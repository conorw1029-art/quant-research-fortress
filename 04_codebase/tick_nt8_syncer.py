"""
tick_nt8_syncer.py — NinjaTrader JSONL → Hetzner Server SFTP Uploader
=======================================================================
Watches for new JSONL files written by FortressBarWriter on this Windows
machine and uploads them to the Hetzner server every N seconds.

Runs as Window 4 in start_fortress.bat when NT8 is active.

Usage:
    python tick_nt8_syncer.py
    python tick_nt8_syncer.py --interval 30 --verbose
"""

import argparse
import os
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("ERROR: pip install paramiko")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

SERVER_HOST   = "46.225.110.190"
SERVER_USER   = "root"
SSH_KEY       = str(Path.home() / ".ssh" / "fortress_deploy")

LOCAL_JSONL   = Path(__file__).parent.parent / "01_data" / "tick_bars" / "live"
REMOTE_JSONL  = "/opt/fortress/01_data/tick_bars/live"

# ── Main ──────────────────────────────────────────────────────────────────────

def connect(verbose: bool) -> paramiko.SFTPClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SERVER_HOST, username=SERVER_USER, key_filename=SSH_KEY, timeout=10)
    if verbose:
        print(f"[Syncer] Connected to {SERVER_HOST}")
    return client


def sync_once(sftp: paramiko.SFTPClient, verbose: bool) -> int:
    """Upload any JSONL files newer than their remote copy. Returns count uploaded."""
    uploaded = 0
    for local in LOCAL_JSONL.glob("*.jsonl"):
        remote = f"{REMOTE_JSONL}/{local.name}"
        try:
            rstat = sftp.stat(remote)
            if local.stat().st_mtime <= rstat.st_mtime:
                continue  # remote is current
        except FileNotFoundError:
            pass  # new file — upload
        sftp.put(str(local), remote)
        uploaded += 1
        if verbose:
            print(f"[Syncer] Uploaded {local.name} ({local.stat().st_size // 1024} KB)")
    return uploaded


def run(interval: int, verbose: bool) -> None:
    print(f"[Syncer] Watching {LOCAL_JSONL}")
    print(f"[Syncer] Uploading to {SERVER_HOST}:{REMOTE_JSONL} every {interval}s")
    print("[Syncer] Press Ctrl+C to stop")

    client = None
    sftp   = None

    while True:
        try:
            if client is None or not client.get_transport() or not client.get_transport().is_active():
                if client:
                    try: client.close()
                    except Exception: pass
                client = connect(verbose)
                sftp   = client.open_sftp()
                # ensure remote live dir exists
                try:
                    sftp.stat(REMOTE_JSONL)
                except FileNotFoundError:
                    sftp.mkdir(REMOTE_JSONL)

            n = sync_once(sftp, verbose)
            if n and not verbose:
                print(f"[Syncer] {time.strftime('%H:%M:%S')} — {n} file(s) synced")

        except Exception as exc:
            print(f"[Syncer] ERROR: {exc} — will retry in {interval}s")
            client = None
            sftp   = None

        time.sleep(interval)


def main():
    p = argparse.ArgumentParser(description="NT8 JSONL → server SFTP syncer")
    p.add_argument("--interval", type=int, default=30,
                   help="Sync interval in seconds (default 30)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    LOCAL_JSONL.mkdir(parents=True, exist_ok=True)
    run(args.interval, args.verbose)


if __name__ == "__main__":
    main()
