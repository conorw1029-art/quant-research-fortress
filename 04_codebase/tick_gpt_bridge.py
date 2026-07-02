#!/usr/bin/env python3
"""
tick_gpt_bridge.py — ChatGPT second-opinion bridge
===================================================
Lets Claude (or Conor) ask ChatGPT for a second opinion from inside a
Fortress session. Claude calls this via Bash, feeds it analysis or code,
and gets GPT's critique back — a two-model review loop.

Setup (one-time):
  1. Get an API key at https://platform.openai.com/api-keys
     (ChatGPT Plus is NOT enough — the API is billed separately,
      pay-as-you-go; $5 of credit lasts a long time for text.)
  2. Add to /opt/fortress/.env:   OPENAI_API_KEY=sk-...
     Optionally:                  OPENAI_MODEL=gpt-5.1   (default)

Usage:
  echo "review this risk logic: ..." | venv/bin/python tick_gpt_bridge.py
  venv/bin/python tick_gpt_bridge.py --prompt "Is 1/4-Kelly right for a $1k runway?"
  venv/bin/python tick_gpt_bridge.py --file tick_risk_manager.py \
      --prompt "Find bugs in this risk manager"

Exit codes: 0 ok, 1 config error, 2 API error.
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

ENV_PATH = "/opt/fortress/.env"


def _load_env(path: str = ENV_PATH) -> None:
    """Minimal .env loader — no dependency on python-dotenv."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


def ask_gpt(prompt: str, system: str, model: str, api_key: str,
            max_tokens: int = 4000) -> str:
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_completion_tokens": max_tokens,
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(r.read())
    return out["choices"][0]["message"]["content"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Ask ChatGPT for a second opinion")
    ap.add_argument("--prompt", help="Question/instruction (else read stdin)")
    ap.add_argument("--file", help="Optional file whose contents are appended to the prompt")
    ap.add_argument("--model", default=None, help="Override OPENAI_MODEL")
    ap.add_argument("--system", default=(
        "You are a skeptical senior quant developer reviewing a live futures "
        "trading system. Be blunt, concrete, and cite specific line-level "
        "problems. Another AI (Claude) wrote or reviewed this — your job is "
        "to find what it missed."))
    args = ap.parse_args()

    _load_env()
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in /opt/fortress/.env\n"
              "Get one at https://platform.openai.com/api-keys", file=sys.stderr)
        return 1
    model = args.model or os.environ.get("OPENAI_MODEL", "gpt-5.1")

    prompt = args.prompt or ""
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read()
    if args.file:
        prompt += "\n\n--- FILE: %s ---\n%s" % (
            args.file, open(args.file, encoding="utf-8", errors="replace").read())
    if not prompt.strip():
        print("ERROR: no prompt (use --prompt, --file, or pipe stdin)", file=sys.stderr)
        return 1

    try:
        print(ask_gpt(prompt, args.system, model, api_key))
        return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        print(f"API ERROR {e.code}: {body}", file=sys.stderr)
        if e.code == 404:
            print(f"(model '{model}' may not exist — set OPENAI_MODEL in .env "
                  f"to a current model name)", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
