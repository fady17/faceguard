#!/usr/bin/env python3
"""Called by Makefile _require-config to validate config.json."""
import json, sys, os

config_file = os.path.expanduser("~/.faceguard/config.json")

try:
    cfg = json.load(open(config_file))
    url = cfg.get("discord", {}).get("webhook_url", "")
    if not url or "YOUR_WEBHOOK" in url:
        print(f"Error: discord.webhook_url is not set in {config_file}.")
        print("  Edit the file and replace YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN")
        sys.exit(1)
except json.JSONDecodeError as e:
    print(f"Error: {config_file} is not valid JSON: {e}")
    sys.exit(1)