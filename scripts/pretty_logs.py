#!/usr/bin/env python3
import sys, json

for line in sys.stdin:
    try:
        e = json.loads(line.strip())
        ts = e.get('timestamp', '')[-8:-1] if e.get('timestamp', '') else ''
        level = e.get('level', 'INFO').ljust(8)
        event = e.get('event', '')
        ctx = {k: v for k, v in e.items() if k not in ('timestamp', 'level', 'event', 'faces')}
        ctx_str = '  ' + str(ctx) if ctx else ''
        print(f'[{ts}] {level} {event}{ctx_str}', flush=True)
    except:
        print(line.rstrip(), flush=True)