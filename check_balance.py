"""Quick script to check real Binance account balance."""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

# Force non-paper mode for balance check
os.environ['PAPER_TRADE'] = 'false'

import requests
import hmac
import hashlib
import time
from urllib.parse import urlencode
from core import BINANCE_API_KEY, BINANCE_SECRET_KEY

BASE = 'https://api.binance.com'
FBASE = 'https://fapi.binance.com'

def make_signed_request(url, params=None):
    if params is None:
        params = {}
    params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = 5000
    qs = urlencode(params)
    sig = hmac.new(BINANCE_SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
    params['signature'] = sig
    resp = requests.get(url, params=params, headers={'X-MBX-APIKEY': BINANCE_API_KEY}, timeout=10)
    return resp

print("=" * 50)
print("  BINANCE ACCOUNT CHECK")
print("=" * 50)

# Spot
print("\n[SPOT ACCOUNT]")
resp = make_signed_request(f'{BASE}/api/v3/account')
print(f"  Status: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    total_usdt = 0
    for bal in data.get('balances', []):
        free = float(bal['free'])
        locked = float(bal['locked'])
        total = free + locked
        if total > 0:
            print(f"  {bal['asset']}: {total:.8f} (free: {free:.8f})")
            if bal['asset'] == 'USDT':
                total_usdt += total
    print(f"\n  Total USDT in spot: ${total_usdt:.2f}")
else:
    error = resp.json() if resp.headers.get('content-type','').startswith('application/json') else resp.text[:300]
    print(f"  Error: {error}")

# Futures
print("\n[FUTURES ACCOUNT]")
resp2 = make_signed_request(f'{FBASE}/fapi/v2/balance')
print(f"  Status: {resp2.status_code}")
if resp2.status_code == 200:
    for bal in resp2.json():
        b = float(bal.get('balance', 0))
        if b > 0:
            print(f"  {bal['asset']}: {b:.8f}")
else:
    error = resp2.json() if resp2.headers.get('content-type','').startswith('application/json') else resp2.text[:300]
    print(f"  Error: {error}")

print("\n" + "=" * 50)
