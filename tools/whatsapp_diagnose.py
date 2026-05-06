"""
whatsapp_diagnose.py
--------------------
Checks whether your WhatsApp app is correctly subscribed to the WABA's
webhook events. If the subscription is missing, this is the most common
reason inbound `messages` webhooks never arrive even though the App-level
webhook URL is verified and the `messages` field shows "Subscribed".

Usage:
    python tools/whatsapp_diagnose.py            # check only
    python tools/whatsapp_diagnose.py --fix      # check + auto-subscribe if missing
"""

import sys
from pathlib import Path

# Make project root importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx

from config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_GRAPH_VERSION,
    WHATSAPP_PHONE_NUMBER_ID,
)

# Hardcoded for this project — same value the user shared earlier.
# (WABA ID is not a secret — public identifier.)
WABA_ID = "1001442865550504"

# Meta's auto-subscribed first-party "DevX" app — captures events for the
# API Setup test panel but does NOT forward them to user-configured webhook
# URLs. Its presence in /subscribed_apps does not satisfy our app's needs.
META_DEVX_APP_ID = "2202427980234937"


def _bearer():
    return {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}


def check_phone_number():
    """Hit the phone number ID endpoint to confirm the access token works
    AND the phone number ID is real."""
    print("─" * 60)
    print("1.  Checking access token + phone number ID...")
    url = f"https://graph.facebook.com/{WHATSAPP_GRAPH_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}"
    r = httpx.get(url, headers=_bearer(), timeout=15.0)
    print(f"    HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"    Response: {r.text}")
        if r.status_code == 401:
            print("    → Access token is invalid or expired. Re-generate it.")
        return False
    data = r.json()
    print(f"    Display number: {data.get('display_phone_number')}")
    print(f"    Verified name: {data.get('verified_name', '(none)')}")
    print(f"    Quality rating: {data.get('quality_rating', 'UNKNOWN')}")
    return True


def check_waba_subscriptions():
    """
    Get the list of apps subscribed to this WABA's webhook events.

    Returns:
      None  on API error
      False if no user app is subscribed (only Meta's DevX 1P app, or empty)
      True  if a user-owned app is subscribed
    """
    print("─" * 60)
    print(f"2.  Checking apps subscribed to WABA {WABA_ID}...")
    url = f"https://graph.facebook.com/{WHATSAPP_GRAPH_VERSION}/{WABA_ID}/subscribed_apps"
    r = httpx.get(url, headers=_bearer(), timeout=15.0)
    print(f"    HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"    Response: {r.text}")
        return None
    data = r.json().get("data", [])
    if not data:
        print("    ⚠ NO APPS SUBSCRIBED — webhooks won't be delivered.")
        return False
    user_app_found = False
    for entry in data:
        app_data = entry.get("whatsapp_business_api_data", {})
        app_id = str(app_data.get("id", ""))
        is_meta_devx = app_id == META_DEVX_APP_ID
        marker = "(Meta internal — does NOT forward to your webhook URL)" if is_meta_devx else "✓ user app"
        print(f"    • {app_data.get('name', '?')} (id: {app_id}) {marker}")
        if not is_meta_devx:
            user_app_found = True
    if not user_app_found:
        print()
        print("    ⚠ Only Meta's DevX 1P app is subscribed — YOUR app isn't.")
        print("    That's why webhooks aren't reaching your /webhook endpoint.")
    return user_app_found


def subscribe_app_to_waba():
    """POST to subscribe the calling app to the WABA's webhook events."""
    print("─" * 60)
    print(f"3.  Subscribing app to WABA {WABA_ID}...")
    url = f"https://graph.facebook.com/{WHATSAPP_GRAPH_VERSION}/{WABA_ID}/subscribed_apps"
    r = httpx.post(url, headers=_bearer(), timeout=15.0)
    print(f"    HTTP {r.status_code}")
    print(f"    Response: {r.text}")
    if r.status_code == 200 and r.json().get("success"):
        print("    ✓ Subscription successful.")
        return True
    print("    ✗ Subscription failed.")
    return False


def main():
    fix = "--fix" in sys.argv

    print()
    print("WhatsApp Cloud API setup diagnosis")
    print(f"  Graph version: {WHATSAPP_GRAPH_VERSION}")
    print(f"  Phone number ID: {WHATSAPP_PHONE_NUMBER_ID}")
    print(f"  WABA ID: {WABA_ID}")
    print()

    if not check_phone_number():
        print("\nStop — fix the access token before continuing.")
        sys.exit(1)

    subscribed = check_waba_subscriptions()
    if subscribed is None:
        # Error reading — already printed
        sys.exit(1)
    if subscribed is False:
        if fix:
            ok = subscribe_app_to_waba()
            if ok:
                print()
                print("─" * 60)
                print("4.  Re-checking subscriptions after fix...")
                check_waba_subscriptions()
            sys.exit(0 if ok else 1)
        else:
            print()
            print("─" * 60)
            print("Re-run with --fix to subscribe the app:")
            print("    python tools/whatsapp_diagnose.py --fix")
            sys.exit(1)

    print()
    print("─" * 60)
    print("✓ All checks passed.")
    print("If webhooks still aren't arriving, the issue is something else")
    print("(check the ngrok inspector at http://127.0.0.1:4040 when sending).")


if __name__ == "__main__":
    main()
