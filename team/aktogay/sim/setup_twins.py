#!/usr/bin/env python3
"""Create or replace the Aktogay twins in Ditto (idempotent PUTs, parents first)."""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

from twins import things

DITTO_URL = os.environ.get("DITTO_URL", "http://localhost:8080").rstrip("/")
DITTO_PASSWORD = os.environ["DITTO_PASSWORD"]
AUTH = "Basic " + base64.b64encode(f"ditto:{DITTO_PASSWORD}".encode()).decode()


def put(thing_id, body):
    request = urllib.request.Request(
        f"{DITTO_URL}/api/2/things/{thing_id}",
        data=json.dumps(body, ensure_ascii=False).encode(),
        method="PUT",
        headers={"Authorization": AUTH, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status


def main():
    all_things = things()
    for thing_id, body in all_things:
        for attempt in range(30):
            try:
                status = put(thing_id, body)
                print(f"{'created' if status == 201 else 'updated'}  {thing_id}")
                break
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 29:
                    print(f"FAILED   {thing_id}: {exc}", file=sys.stderr)
                    return 1
                time.sleep(3)
    print(f"{len(all_things)} twins ready in {DITTO_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
