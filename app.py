"""
fanhist - a small, self-hosted iDRAC fan controller with history.

Inspired by Hush (natankeddem/hush), but purpose-built and simpler:
- Reads CPU/Inlet temperature directly via ipmitool (fast, no Redfish/TLS involved)
- Optionally reads a disk temperature over SSH (e.g. TrueNAS drivetemp/hwmon)
- Applies a user-configurable temperature -> fan% curve via IPMI raw commands
- Logs every reading to SQLite and shows history on a small dashboard
"""

import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request

# --------------------------------------------------------------------------
# Configuration (all overridable via environment variables / docker-compose)
# --------------------------------------------------------------------------

IDRAC_HOST = os.environ.get("IDRAC_HOST", "192.168.50.11")
IDRAC_USER = os.environ.get("IDRAC_USER", "root")
IDRAC_PASS = os.environ.get("IDRAC_PASS", "")

CPU_SENSOR_NAME = os.environ.get("CPU_SENSOR_NAME", "Inlet Temp")

DISK_SSH_HOST = os.environ.get("DISK_SSH_HOST", "")  # e.g. TrueNAS IP; leave empty to disable
DISK_SSH_USER = os.environ.get("DISK_SSH_USER", "root")
DISK_SSH_KEY = os.environ.get("DISK_SSH_KEY", "/config/ssh/id_ed25519")
# Command run over SSH that must print ONE temperature per line (Celsius or milli-Celsius).
# Multiple lines = multiple disks; they are averaged. Default reads every drivetemp
# hwmon sensor it can find (i.e. every disk exposed via the drivetemp kernel module).
DISK_TEMP_CMD = os.environ.get(
    "DISK_TEMP_CMD",
    "for f in /sys/class/hwmon/hwmon*/name; do "
    "  grep -qx drivetemp \"$f\" 2>/dev/null && cat \"$(dirname \"$f\")\"/temp1_input; "
    "done",
)
# How to combine multiple disk readings: "avg" (default), "max", or "min"
DISK_TEMP_AGGREGATION = os.environ.get("DISK_TEMP_AGGREGATION", "avg")

INTERVAL_SECONDS = int(os.environ.get("INTERVAL_SECONDS", "30"))
IPMI_TIMEOUT = int(os.environ.get("IPMI_TIMEOUT", "10"))
DB_PATH = os.environ.get("DB_PATH", "/data/fanhist.db")
HISTORY_RETENTION_DAYS = int(os.environ.get("HISTORY_RETENTION_DAYS", "30"))

DEFAULT_CURVE = [
    {"temp": 35, "percent": 5},
    {"temp": 45, "percent": 20},
    {"temp": 55, "percent": 40},
    {"temp": 65, "percent": 70},
    {"temp": 75, "percent": 100},
]

app = Flask(__name__)
_lock = threading.Lock()
_state = {
    "cpu_temp": None,
    "disk_temp": None,
    "disk_temps": [],
    "disk_count": 0,
    "effective_temp": None,
    "fan_percent": None,
    "last_update": None,
    "last_error": None,
}


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def db_init():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS readings (
                ts TEXT NOT NULL,
                cpu_temp REAL,
                disk_temp REAL,
                effective_temp REAL,
                fan_percent REAL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )"""
        )
        conn.commit()


def db_log_reading(cpu_temp, disk_temp, effective_temp, fan_percent):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO readings (ts, cpu_temp, disk_temp, effective_temp, fan_percent) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), cpu_temp, disk_temp, effective_temp, fan_percent),
        )
        conn.commit()


def db_prune_old():
    cutoff = (datetime.utcnow() - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
        conn.commit()


def db_get_history(hours):
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT ts, cpu_temp, disk_temp, effective_temp, fan_percent "
            "FROM readings WHERE ts >= ? ORDER BY ts ASC",
            (cutoff,),
        ).fetchall()
    return [
        {
            "ts": r[0],
            "cpu_temp": r[1],
            "disk_temp": r[2],
            "effective_temp": r[3],
            "fan_percent": r[4],
        }
        for r in rows
    ]


def db_get_curve():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT value FROM config WHERE key = 'curve'").fetchone()
    if row:
        return json.loads(row[0])
    return DEFAULT_CURVE


def db_set_curve(curve):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO config (key, value) VALUES ('curve', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(curve),),
        )
        conn.commit()


# --------------------------------------------------------------------------
# IPMI helpers
# --------------------------------------------------------------------------

def _ipmi_base_cmd():
    return [
        "ipmitool", "-I", "lanplus",
        "-H", IDRAC_HOST, "-U", IDRAC_USER, "-P", IDRAC_PASS,
    ]


def ipmi_read_cpu_temp():
    """Read a named sensor via ipmitool. Raises on failure/timeout."""
    cmd = _ipmi_base_cmd() + ["sensor", "reading", CPU_SENSOR_NAME]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=IPMI_TIMEOUT)
    if out.returncode != 0:
        raise RuntimeError(f"ipmitool sensor reading failed: {out.stderr.strip()}")
    match = re.search(r"[-+]?\d+(\.\d+)?", out.stdout)
    if not match:
        raise RuntimeError(f"Could not parse sensor output: {out.stdout!r}")
    return float(match.group())


def ipmi_set_manual_mode():
    cmd = _ipmi_base_cmd() + ["raw", "0x30", "0x30", "0x01", "0x00"]
    subprocess.run(cmd, capture_output=True, text=True, timeout=IPMI_TIMEOUT, check=True)


def ipmi_set_fan_percent(percent):
    percent = max(0, min(100, int(round(percent))))
    hex_val = f"0x{percent:02x}"
    cmd = _ipmi_base_cmd() + ["raw", "0x30", "0x30", "0x02", "0xff", hex_val]
    subprocess.run(cmd, capture_output=True, text=True, timeout=IPMI_TIMEOUT, check=True)


def read_disk_temps():
    """Read one or more disk temperatures over SSH. Returns a list of °C values
    (empty list if not configured/failed)."""
    if not DISK_SSH_HOST:
        return []
    cmd = [
        "ssh", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no",
        "-i", DISK_SSH_KEY, f"{DISK_SSH_USER}@{DISK_SSH_HOST}", DISK_TEMP_CMD,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=IPMI_TIMEOUT)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"disk temp SSH command failed: {out.stderr.strip()}")

    temps = []
    for line in out.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = float(line)
        except ValueError:
            continue
        # drivetemp/hwmon reports millidegrees; normalize to Celsius if needed
        temps.append(raw / 1000.0 if raw > 200 else raw)

    if not temps:
        raise RuntimeError(f"no parsable disk temps in output: {out.stdout!r}")
    return temps


def aggregate_disk_temp(temps):
    if not temps:
        return None
    if DISK_TEMP_AGGREGATION == "max":
        return max(temps)
    if DISK_TEMP_AGGREGATION == "min":
        return min(temps)
    return sum(temps) / len(temps)  # avg (default)


# --------------------------------------------------------------------------
# Curve interpolation
# --------------------------------------------------------------------------

def curve_percent_for_temp(curve, temp):
    points = sorted(curve, key=lambda p: p["temp"])
    if temp <= points[0]["temp"]:
        return points[0]["percent"]
    if temp >= points[-1]["temp"]:
        return points[-1]["percent"]
    for a, b in zip(points, points[1:]):
        if a["temp"] <= temp <= b["temp"]:
            span = b["temp"] - a["temp"]
            if span == 0:
                return a["percent"]
            ratio = (temp - a["temp"]) / span
            return a["percent"] + ratio * (b["percent"] - a["percent"])
    return points[-1]["percent"]


# --------------------------------------------------------------------------
# Control loop
# --------------------------------------------------------------------------

def control_loop():
    ipmi_set_manual_mode()
    while True:
        cpu_temp = None
        disk_temps = []
        disk_temp = None
        error = None
        try:
            cpu_temp = ipmi_read_cpu_temp()
        except Exception as exc:
            error = f"CPU temp read failed: {exc}"

        try:
            disk_temps = read_disk_temps()
            disk_temp = aggregate_disk_temp(disk_temps)
        except Exception as exc:
            error = (error + " | " if error else "") + f"Disk temp read failed: {exc}"

        candidates = [t for t in (cpu_temp, disk_temp) if t is not None]
        effective_temp = max(candidates) if candidates else None

        fan_percent = None
        if effective_temp is not None:
            curve = db_get_curve()
            fan_percent = curve_percent_for_temp(curve, effective_temp)
            try:
                ipmi_set_fan_percent(fan_percent)
            except Exception as exc:
                error = (error + " | " if error else "") + f"Fan set failed: {exc}"

        with _lock:
            _state.update(
                cpu_temp=cpu_temp,
                disk_temp=disk_temp,
                disk_temps=disk_temps,
                disk_count=len(disk_temps),
                effective_temp=effective_temp,
                fan_percent=fan_percent,
                last_update=datetime.utcnow().isoformat(),
                last_error=error,
            )

        if effective_temp is not None:
            db_log_reading(cpu_temp, disk_temp, effective_temp, fan_percent)
        db_prune_old()

        time.sleep(INTERVAL_SECONDS)


# --------------------------------------------------------------------------
# Web routes
# --------------------------------------------------------------------------

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(_state)


@app.route("/api/history")
def api_history():
    hours = int(request.args.get("hours", 24))
    return jsonify(db_get_history(hours))


@app.route("/api/curve", methods=["GET", "POST"])
def api_curve():
    if request.method == "POST":
        curve = request.get_json()
        if not isinstance(curve, list) or not curve:
            return jsonify({"error": "curve must be a non-empty list"}), 400
        db_set_curve(curve)
        return jsonify({"ok": True})
    return jsonify(db_get_curve())


if __name__ == "__main__":
    db_init()
    threading.Thread(target=control_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8081)
