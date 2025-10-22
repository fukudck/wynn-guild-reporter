import os
import sys
import requests
import time
import random
from datetime import datetime, timezone

# ===== CONFIG =====
try:
    GUILD_PREFIX = os.environ["GUILD_PREFIX"]
    WEBHOOK_URL = os.environ["WEBHOOK_URL"]
except KeyError as e:
    print(f"[FATAL] Missing environment variable: {e}. Please set it in your environment or GitHub Secrets.")
    sys.exit(1)

API_GUILD_URL = f"https://api.wynncraft.com/v3/guild/prefix/{GUILD_PREFIX}"
API_PLAYER_URL = "https://api.wynncraft.com/v3/player/{}"  # uses UUID

DELAY_MIN = 0.5
DELAY_MAX = 2.0
MAX_RETRIES = 5
RETRY_DELAY = 5
OUTPUT_FILE = "guild_activity.txt"



# ===== SAFE REQUEST =====
def safe_request(url):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 429:
                print(f"[WARN] Rate limited. Waiting {RETRY_DELAY}s before retry ({attempt}/{MAX_RETRIES})...")
                time.sleep(RETRY_DELAY)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            print(f"[ERROR] Request failed: {e}. Retrying in {RETRY_DELAY}s ({attempt}/{MAX_RETRIES})...")
            time.sleep(RETRY_DELAY)
    raise Exception(f"Failed to fetch after {MAX_RETRIES} attempts: {url}")


# ===== FETCH GUILD MEMBERS =====
def fetch_guild_members():
    print(f"[INFO] Fetching guild data for prefix '{GUILD_PREFIX}'...")
    resp = safe_request(API_GUILD_URL)
    data = resp.json()

    members = []
    for rank, players in data["members"].items():
        if rank == "total":
            continue
        for name, info in players.items():
            uuid = info.get("uuid")
            if not uuid:
                continue
            members.append({
                "uuid": uuid,
                "guild_name": name,
                "rank": rank,
                "contributed": info.get("contributed", 0),
                "joined": info.get("joined", "Unknown")
            })
    print(f"[INFO] Found {len(members)} members in guild '{data['name']}'.")
    return members


# ===== FETCH PLAYER INFO =====
def fetch_player_info(uuid):
    """
    Returns dict with:
      - current_name (str)
      - playtime (float)
      - delta_str (str)          # human readable
      - last_join_dt (datetime or None)
      - inactivity_seconds (int or float('inf'))  # larger = more inactive
    """
    resp = safe_request(API_PLAYER_URL.format(uuid))
    data = resp.json()

    username = data.get("username", uuid)
    playtime = data.get("playtime", 0)
    last_join_str = data.get("lastJoin")

    if not last_join_str:
        # Never joined -> treat as most inactive
        return {
            "current_name": username,
            "playtime": playtime,
            "delta_str": "Never joined",
            "last_join_dt": None,
            "inactivity_seconds": float("inf")
        }

    # parse ISO datetime to aware datetime in UTC
    try:
        last_join_dt = datetime.fromisoformat(last_join_str.replace("Z", "+00:00"))
    except Exception:
        # fallback: keep as None and mark as very inactive
        return {
            "current_name": username,
            "playtime": playtime,
            "delta_str": "Invalid date",
            "last_join_dt": None,
            "inactivity_seconds": float("inf")
        }

    now = datetime.now(timezone.utc)
    delta = now - last_join_dt
    inactivity_seconds = int(delta.total_seconds())

    # build human readable delta_str
    days = delta.days
    hours = (delta.seconds) // 3600
    minutes = (delta.seconds % 3600) // 60
    delta_str = f"{days}d {hours}h {minutes}m ago"

    return {
        "current_name": username,
        "playtime": playtime,
        "delta_str": delta_str,
        "last_join_dt": last_join_dt,
        "inactivity_seconds": inactivity_seconds
    }



# ===== SEND FILE VIA WEBHOOK =====
def send_webhook_file(filepath, message=None):
    if not WEBHOOK_URL:
        print("[WARN] No WEBHOOK_URL set — skipping webhook send.")
        return

    try:
        with open(filepath, "rb") as f:
            files = {"file": (os.path.basename(filepath), f)}
            data = {"content": message or f"Guild activity report for {GUILD_PREFIX}"}
            resp = requests.post(WEBHOOK_URL, data=data, files=files, timeout=30)
            resp.raise_for_status()
            print(f"[INFO] Webhook (file) sent successfully! ({resp.status_code})")
    except Exception as e:
        print(f"[ERROR] Failed to send webhook file: {e}")


# ===== MAIN =====
def main():
    members = fetch_guild_members()
    results = []

    for i, m in enumerate(members, start=1):
        print(f"[{i}/{len(members)}] Fetching player data for UUID: {m['uuid']} ({m['guild_name']})...")
        try:
            info = fetch_player_info(m["uuid"])
            merged = {**m, **info}
            results.append(merged)
            print(f" → {merged['guild_name']} → {merged['current_name']}: {merged['playtime']}h | Last Join: {merged['delta_str']}")
        except Exception as e:
            print(f" → Failed to fetch {m['uuid']}: {e}")
            results.append({
                "uuid": m["uuid"],
                "guild_name": m.get("guild_name", "Unknown"),
                "current_name": "Error",
                "rank": m["rank"],
                "playtime": 0,
                "delta_str": f"Error: {e}"
            })
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # Sort by inactivity_seconds (descending) -> most inactive first
    # if inactivity_seconds is float("inf") (Never joined), those appear on top
    results.sort(key=lambda x: x.get("inactivity_seconds", 0), reverse=True)



    # Output full log file
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(f"Guild: {GUILD_PREFIX}\n")
        f.write(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"{'Old Name':<20} | {'New Name':<20} | {'Rank':<10} | {'Playtime':>8} | Last Join\n")
        f.write("-" * 70 + "\n")
        for r in results:
            f.write(f"{r['guild_name']:<20} | {r['current_name']:<20} | {r['rank']:<10} | {r['playtime']:>6.1f}h | {r['delta_str']}\n")

    print(f"\n[INFO] Report saved to '{OUTPUT_FILE}'")

    # Send file via webhook
    send_webhook_file(OUTPUT_FILE, f"Guild report for `{GUILD_PREFIX}` generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")


if __name__ == "__main__":
    main()
