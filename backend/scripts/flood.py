"""Flood a running API from one address and show the rate limit kicking in.

    .venv/bin/uvicorn app.main:app --port 8000          # in another terminal
    .venv/bin/python scripts/flood.py                    # 30 fast GETs, expect 20 x 200 then 429
    .venv/bin/python scripts/flood.py -n 50 --wait       # then wait for Retry-After and try once more
    .venv/bin/python scripts/flood.py --spoof            # X-Forwarded-For per request must NOT help

Uses the cheap GET /api/samples so no LLM call is made. Exit code 0 when the
limiter behaved as expected, 1 otherwise.
"""

import argparse
import sys
import time
import urllib.error
import urllib.request


def hit(url: str, headers: dict[str, str]) -> tuple[int, str | None]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, None
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Retry-After")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("-n", type=int, default=30, help="requests to send as fast as possible")
    parser.add_argument("--spoof", action="store_true", help="send a different X-Forwarded-For on every request")
    parser.add_argument("--wait", action="store_true", help="after the block, sleep Retry-After and try once more")
    args = parser.parse_args()
    url = f"{args.url.rstrip('/')}/api/samples"

    try:
        hit(f"{args.url.rstrip('/')}/health", {})
    except OSError as e:
        print(f"API not reachable at {args.url}: {e}")
        return 1

    start = time.monotonic()
    codes, retry_after = [], None
    for i in range(args.n):
        headers = {"X-Forwarded-For": f"10.0.{i // 256}.{i % 256}"} if args.spoof else {}
        code, retry = hit(url, headers)
        codes.append(code)
        retry_after = retry_after or retry
        print(f"#{i + 1:>3} {code}" + (f"  Retry-After: {retry}" if retry else ""))
    elapsed = time.monotonic() - start

    allowed, blocked = codes.count(200), codes.count(429)
    print(f"\n{args.n} requests in {elapsed:.2f} s: {allowed} allowed, {blocked} blocked (429)")
    if blocked == 0:
        print("FAIL: never blocked. Is EHDS_RATE_LIMIT_REQUESTS >= n?")
        if args.spoof:
            print("      With --spoof from localhost: uvicorn itself rewrites the client address from X-Forwarded-For\n"
                  "      (default --proxy-headers trusts 127.0.0.1). Start it with --no-proxy-headers, or set\n"
                  "      --forwarded-allow-ips to your reverse proxy only.")
        return 1
    first_block = codes.index(429) + 1
    if any(c == 200 for c in codes[first_block:]):
        print("FAIL: a request was allowed after the block started")
        return 1
    print(f"OK: blocked from request #{first_block} on, Retry-After {retry_after} s")

    if args.wait and retry_after:
        wait = int(retry_after) + 1
        print(f"\nwaiting {wait} s for the block to end...")
        time.sleep(wait)
        code, _ = hit(url, {})
        print(f"after the wait: {code}")
        if code != 200:
            print("FAIL: still blocked after Retry-After")
            return 1
        print("OK: address unblocked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
