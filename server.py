import subprocess
import platform
from datetime import datetime, timedelta
from flask import Flask, jsonify, send_from_directory
import threading
import time
import os
from collections import deque

app = Flask(__name__)

# ─── Keywatcher devices ───────────────────────────────────────────────────
KEYWATCHER_DEVICES = [
    {"name": "400",    "ip": "10.10.52.5", "cabinet": {"name": "400 Cabinet", "ip": "10.10.52.6"}},
    {"name": "GWP",    "ip": "10.20.3.2", "cabinet": {"name": "GWP Cabinet", "ip": "10.20.3.3"}},
    {"name": "KTN",    "ip": "192.168.75.99", "cabinet": {"name": "KTN Cabinet", "ip": "192.168.75.152"}},
    {"name": "BA",     "ip": "10.3.3.2", "cabinet": {"name": "BA Cabinet", "ip": "10.3.3.94"}},
]
# ─── Fabitrack devices ────────────────────────────────────────────────────────
FABITRACK_DEVICES = [
    {"name": "KTN App Server", "ip": "192.168.75.17"},
    {"name": "BA App Server", "ip": "10.3.0.6"},
    {"name": "GWP App Server", "ip": "10.20.1.41"},
    {"name": "400 App Server", "ip": "192.168.75.48"},
    # {"name": "400 Jackpot Server", "ip": "192.168.75.49"},
    # {"name": "KTN Jackpot Server", "ip": "192.168.75.18"},
    # {"name": "GWP Jackpot Server", "ip": "10.20.1.42"},
    # {"name": "BA Jackpot Server", "ip": "10.3.0.7"},
]
# ─── Info Genesis devices ────────────────────────────────────────────────────
INFOGENESIS_DEVICES = [
    {"name": "BA Print Server", "ip": "10.3.2.6"},
    {"name": "KTN/GWP/BA Interface", "ip": "192.168.75.45"},
    {"name": "400 Interface Server", "ip": "192.168.75.11"},
    {"name": "400 App / DB Server", "ip": "192.168.75.7"},
]
# ─── Fabi Kiosk devices ────────────────────────────────────────────────────
FABIKIOSK_DEVICES = [
    {"name": "400 Kiosk 1", "ip": "10.10.55.12"},
    {"name": "400 Kiosk 2", "ip": "10.10.55.31"},
    {"name": "KTN Kiosk 1", "ip": "192.168.75.229"},
    {"name": "GWP Kiosk 1", "ip": "10.20.3.250"},
    {"name": "GWP Kiosk 2", "ip": "10.20.3.251"},
]

# ─── Temp devices ────────────────────────────────────────────────────
TEMP_DEVICES = [
    {"name": "Placeholder 1", "ip": "8.8.8.8"},
    {"name": "Placeholder 2", "ip": "8.8.8.8"},
    {"name": "Placeholder 3", "ip": "8.8.8.8"},
    {"name": "Placeholder 4", "ip": "8.8.8.8"},
]

# ─── Temp 2 devices ────────────────────────────────────────────────────
TEMP2_DEVICES = [
    {"name": "Placeholder 1", "ip": "8.8.8.8"},
    {"name": "Placeholder 2", "ip": "8.8.8.8"},
    {"name": "Placeholder 3", "ip": "8.8.8.8"},
    {"name": "Placeholder 4", "ip": "8.8.8.8"},
]


# ─────────────────────────────────────────────────────────────────────────────

REFRESH_INTERVAL = 30  # seconds between auto-pings
WARNING_WINDOW = 300  # 5 minutes in seconds
WARNING_THRESHOLD = 8  # failed pings in window to trigger warning
OFFLINE_CONSECUTIVE = 5  # consecutive failures to mark offline


def make_device_entry(d):
    entry = {
        "name": d["name"],
        "ip": d["ip"],
        "online": None,
        "offline_since": None,
        "status": "pending",
        "consecutive": 0,
        "history": deque(),
        "cabinet": None,
    }
    if "cabinet" in d:
        entry["cabinet"] = {
            "name": d["cabinet"]["name"],
            "ip": d["cabinet"]["ip"],
            "online": None,
            "offline_since": None,
            "status": "pending",
            "consecutive": 0,
            "history": deque(),
        }
    return entry


def make_cache(devices):
    return {
        "devices": [make_device_entry(d) for d in devices],
        "last_checked": None,
        "checking": False,
    }


kw_cache = make_cache(KEYWATCHER_DEVICES)
fab_cache = make_cache(FABITRACK_DEVICES)
ig_cache = make_cache(INFOGENESIS_DEVICES)
kio_cache = make_cache(FABIKIOSK_DEVICES)
temp_cache = make_cache(TEMP_DEVICES)
temp2_cache = make_cache(TEMP2_DEVICES)


def ping(host):
    if platform.system().lower() == "windows":
        command = ["ping", "-n", "1", "-w", "1000", host]
    else:
        command = ["ping", "-c", "1", "-W", "1", host]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0




def update_device_status(device, success):
    old_status = device["status"]
    now = datetime.now()
    cutoff = now - timedelta(seconds=WARNING_WINDOW)

    # Record result in history
    device["history"].append((now, success))

    # Prune old history outside the 5-minute window
    while device["history"] and device["history"][0][0] < cutoff:
        device["history"].popleft()

    # Update consecutive fail counter
    if success:

        if old_status == "offline":
            device["offline_since"] = None

        device["consecutive"] = 0
    else:
        device["consecutive"] += 1

    if success and old_status == "offline":
        with open("offline_log.txt", "a") as logfile:
            logfile.write(
                f"{datetime.now():%Y-%m-%d %H:%M:%S} | "
                f"{device['name']} - "
                f"{device['ip']} is ONLINE\n"
            )

    # Count failures in the rolling window
    recent_failures = sum(1 for _, ok in device["history"] if not ok)

    # Determine status
    if device["consecutive"] >= OFFLINE_CONSECUTIVE:

        if old_status != "offline":
                device["offline_since"] = datetime.now()

        device["online"] = False
        device["status"] = "offline"

        if old_status != "offline":
            with open("offline_log.txt", "a") as logfile:
                logfile.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {device['name']} - {device['ip']} is OFFLINE\n")

    elif recent_failures > WARNING_THRESHOLD:
        device["online"] = True  # reachable right now but unstable
        device["status"] = "warning"
    else:
        device["online"] = success
        device["status"] = "online" if success else "offline"


def run_pings(cache, devices):
    if cache["checking"]:
        return
    cache["checking"] = True

    def do_ping(i, ip):
        success = ping(ip)
        update_device_status(cache["devices"][i], success)
        # Also ping cabinet if present
        cab = cache["devices"][i]["cabinet"]
        if cab is not None:
            update_device_status(cab, ping(cab["ip"]))

    threads = [threading.Thread(target=do_ping, args=(i, d["ip"])) for i, d in enumerate(devices)]
    for t in threads: t.start()
    for t in threads: t.join()

    cache["last_checked"] = datetime.now().strftime("%H:%M:%S")
    cache["checking"] = False


def serialize_device(d):
    entry = {
        "name": d["name"],
        "ip": d["ip"],
        "online": d["online"],
        "status": d["status"],
        "offline_since": (
            d["offline_since"].isoformat()
            if d["offline_since"]
            else None
        ),
        "cabinet": None,
    }
    if d["cabinet"] is not None:
        c = d["cabinet"]
        entry["cabinet"] = {
            "name": c["name"],
            "ip": c["ip"],
            "online": c["online"],
            "status": c["status"],
        }
    return entry


def serialize_cache(cache):
    """Return a JSON-safe copy of cache (strips deque from history)."""
    return {
        "last_checked": cache["last_checked"],
        "checking": cache["checking"],
        "devices": [serialize_device(d) for d in cache["devices"]],
    }


def auto_refresh():
    while True:
        threading.Thread(target=run_pings, args=(kw_cache, KEYWATCHER_DEVICES)).start()
        threading.Thread(target=run_pings, args=(fab_cache, FABITRACK_DEVICES)).start()
        threading.Thread(target=run_pings, args=(ig_cache, INFOGENESIS_DEVICES)).start()
        threading.Thread(target=run_pings, args=(kio_cache, FABIKIOSK_DEVICES)).start()
        threading.Thread(target=run_pings, args=(temp_cache, TEMP_DEVICES)).start()
        threading.Thread(target=run_pings, args=(temp2_cache, TEMP2_DEVICES)).start()
        time.sleep(REFRESH_INTERVAL)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(os.path.dirname(__file__), "home.html")


@app.route("/dashboard")
def dashboard():
    return send_from_directory(os.path.dirname(__file__), "dashboard.html")


@app.route("/fabitrack")
def fabitrack():
    return send_from_directory(os.path.dirname(__file__), "fabitrack.html")


@app.route("/infogenesis")
def infogenesis():
    return send_from_directory(os.path.dirname(__file__), "infogenesis.html")

@app.route("/kiosks")
def kiosks():
    return send_from_directory(os.path.dirname(__file__), "kiosks.html")

@app.route("/temp")
def temp():
    return send_from_directory(os.path.dirname(__file__), "temp.html")

@app.route("/temp2")
def temp2():
    return send_from_directory(os.path.dirname(__file__), "temp2.html")


@app.route("/api/status")
def kw_status():
    return jsonify(serialize_cache(kw_cache))


@app.route("/api/refresh", methods=["POST"])
def kw_refresh():
    threading.Thread(target=run_pings, args=(kw_cache, KEYWATCHER_DEVICES)).start()
    return jsonify({"ok": True})


@app.route("/api/fabitrack/status")
def fab_status():
    return jsonify(serialize_cache(fab_cache))


@app.route("/api/fabitrack/refresh", methods=["POST"])
def fab_refresh():
    threading.Thread(target=run_pings, args=(fab_cache, FABITRACK_DEVICES)).start()
    return jsonify({"ok": True})


@app.route("/api/infogenesis/status")
def ig_status():
    return jsonify(serialize_cache(ig_cache))


@app.route("/api/infogenesis/refresh", methods=["POST"])
def ig_refresh():
    threading.Thread(target=run_pings, args=(ig_cache, INFOGENESIS_DEVICES)).start()
    return jsonify({"ok": True})

@app.route("/api/kiosks/status")
def kio_status():
    return jsonify(serialize_cache(kio_cache))

@app.route("/api/kiosks/refresh", methods=["POST"])
def kio_refresh():
    threading.Thread(target=run_pings, args=(kio_cache, FABIKIOSK_DEVICES)).start()
    return jsonify({"ok": True})

@app.route("/api/temp/status")
def temp_status():
    return jsonify(serialize_cache(temp_cache))

@app.route("/api/temp/refresh", methods=["POST"])
def temp_refresh():
    threading.Thread(target=run_pings, args=(temp_cache, TEMP_DEVICES)).start()
    return jsonify({"ok": True})

@app.route("/api/temp2/status")
def temp2_status():
    return jsonify(serialize_cache(temp2_cache))

@app.route("/api/temp2/refresh", methods=["POST"])
def temp2_refresh():
    threading.Thread(target=run_pings, args=(temp2_cache, TEMP2_DEVICES)).start()
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("Starting initial ping sweep...")
    threading.Thread(target=auto_refresh, daemon=True).start()
    print("Dashboard running at http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)

