# v2 - KW overview redesign
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import sqlite3
import os
import json
import hmac
import hashlib
from datetime import datetime

app = Flask(__name__)
CORS(app)

DATABASE = os.environ.get('DATABASE_PATH', 'stundenerfassung.db')

EMPLOYEES = [
    {"id": "JHE", "name": "Jan Heck"},
    {"id": "MTH", "name": "Maxime Theatre"},
    {"id": "ODY", "name": "Odyseus Novak"},
    {"id": "PNS", "name": "Pascal Niessen"},
    {"id": "RHE", "name": "Romain Heindrichs"},
    {"id": "RTE", "name": "Romain Theissen"},
]

# ── Login / PIN ───────────────────────────────────────────────────────────
# Start-PINs (werden bei leerer DB einmalig uebernommen; Aenderung danach ueber Admin-Seite).
DEFAULT_PINS = {
    "JHE": "1234",
    "MTH": "2345",
    "ODY": "3456",
    "PNS": "4567",
    "RHE": "5678",
    "RTE": "6789",
}
ADMIN_IDS = {"JHE"}  # darf alle Mitarbeiter sehen
AUTH_SECRET = os.environ.get('AUTH_SECRET', 'gimatherm-stunden-2026-geheim')

def token_for(employee_id):
    return hmac.new(AUTH_SECRET.encode(), str(employee_id).encode(),
                    hashlib.sha256).hexdigest()[:32]

def valid_for(employee_id, token):
    if not token:
        return False
    if token == token_for(employee_id):
        return True
    for aid in ADMIN_IDS:
        if token == token_for(aid):
            return True
    return False

def check_auth(employee_id):
    return valid_for(employee_id, request.headers.get('X-Auth-Token', ''))

def is_admin_token(token):
    return bool(token) and any(token == token_for(a) for a in ADMIN_IDS)

def ensure_pins():
    db = get_db()
    db.execute("CREATE TABLE IF NOT EXISTS employee_pins (employee_id TEXT PRIMARY KEY, pin TEXT NOT NULL)")
    if db.execute("SELECT COUNT(*) FROM employee_pins").fetchone()[0] == 0:
        db.executemany("INSERT OR IGNORE INTO employee_pins (employee_id, pin) VALUES (?, ?)",
                       list(DEFAULT_PINS.items()))
    db.commit()
    db.close()

def get_pin(employee_id):
    db = get_db()
    row = db.execute("SELECT pin FROM employee_pins WHERE employee_id=?", (employee_id,)).fetchone()
    db.close()
    return str(row["pin"]) if row else None

def get_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS activities (
            code INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT NOT NULL,
            datum TEXT NOT NULL,
            kundenname TEXT,
            kunden_id INTEGER,
            aktivitaet TEXT,
            akt_code INTEGER,
            von TEXT,
            bis TEXT,
            pause INTEGER DEFAULT 0,
            kommentar TEXT,
            synced INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS available_weeks (
            year INTEGER NOT NULL,
            week INTEGER NOT NULL,
            employee_id TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (year, week, employee_id)
        );
    """)
    # Seed customers and activities if empty
    if db.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
        seed_path = os.path.join(os.path.dirname(__file__), 'seed_data.json')
        if os.path.exists(seed_path):
            with open(seed_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            db.executemany(
                "INSERT OR IGNORE INTO customers (id, name) VALUES (?, ?)",
                [(c['id'], c['name']) for c in data['customers']]
            )
            db.executemany(
                "INSERT OR IGNORE INTO activities (code, name) VALUES (?, ?)",
                [(a['code'], a['name']) for a in data['activities']]
            )
            db.commit()
    db.close()

# -- Static data -------------------------------------------------------------
@app.route('/api/employees')
def get_employees():
    return jsonify(EMPLOYEES)

@app.route('/api/customers')
def get_customers():
    db = get_db()
    rows = db.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
    db.close()
    return jsonify([{"id": r["id"], "name": r["name"]} for r in rows])

@app.route('/api/customers/import', methods=['POST'])
def import_customers():
    """Ersetzt die gesamte Kundenliste."""
    data = request.get_json()
    customers = data.get('customers', [])
    if not customers:
        return jsonify({"error": "keine Kunden"}), 400
    db = get_db()
    db.execute("DELETE FROM customers")
    db.executemany(
        "INSERT INTO customers (id, name) VALUES (?, ?)",
        [(c['id'], c['name']) for c in customers]
    )
    db.commit()
    db.close()
    return jsonify({"ok": True, "count": len(customers)})

@app.route('/api/activities')
def get_activities():
    db = get_db()
    rows = db.execute("SELECT code, name FROM activities ORDER BY code").fetchall()
    db.close()
    return jsonify([{"code": r["code"], "name": r["name"]} for r in rows])

@app.route('/api/login', methods=['POST'])
def login():
    ensure_pins()
    data = request.get_json() or {}
    employee = data.get('employee')
    pin = str(data.get('pin', '')).strip()
    stored = get_pin(employee) if employee else None
    if stored is None:
        return jsonify({"ok": False, "error": "Unbekanntes Kuerzel"}), 401
    if pin != stored:
        return jsonify({"ok": False, "error": "Falsche PIN"}), 401
    return jsonify({
        "ok": True,
        "employee": employee,
        "token": token_for(employee),
        "is_admin": employee in ADMIN_IDS
    })

@app.route('/api/pins', methods=['GET'])
def get_pins():
    if not is_admin_token(request.headers.get('X-Auth-Token', '')):
        return jsonify({"error": "unauthorized"}), 401
    ensure_pins()
    db = get_db()
    rows = db.execute("SELECT employee_id, pin FROM employee_pins").fetchall()
    db.close()
    pins = {r["employee_id"]: r["pin"] for r in rows}
    out = [{"id": e["id"], "name": e["name"], "pin": pins.get(e["id"], "")} for e in EMPLOYEES]
    return jsonify(out)

@app.route('/api/pins', methods=['POST'])
def set_pins():
    if not is_admin_token(request.headers.get('X-Auth-Token', '')):
        return jsonify({"error": "unauthorized"}), 401
    ensure_pins()
    data = request.get_json() or {}
    pins = data.get('pins', {})
    valid_ids = {e["id"] for e in EMPLOYEES}
    db = get_db()
    count = 0
    for emp, pin in pins.items():
        if emp not in valid_ids:
            continue
        pin = str(pin).strip()
        if not pin:
            continue
        db.execute(
            "INSERT INTO employee_pins (employee_id, pin) VALUES (?, ?) "
            "ON CONFLICT(employee_id) DO UPDATE SET pin=excluded.pin",
            (emp, pin)
        )
        count += 1
    db.commit()
    db.close()
    return jsonify({"ok": True, "updated": count})

# -- Available Weeks ---------------------------------------------------------
@app.route('/api/available-weeks', methods=['GET'])
def get_available_weeks():
    employee = request.args.get('employee')
    if employee and not check_auth(employee):
        return jsonify({"error": "unauthorized"}), 401
    db = get_db()
    if employee:
        rows = db.execute(
            "SELECT DISTINCT year, week FROM available_weeks WHERE employee_id=? ORDER BY year DESC, week DESC",
            (employee,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT DISTINCT year, week FROM available_weeks ORDER BY year DESC, week DESC"
        ).fetchall()
    db.close()
    return jsonify([{"year": r["year"], "week": r["week"]} for r in rows])

@app.route('/api/available-weeks', methods=['POST'])
def set_available_weeks():
    data = request.get_json()
    weeks = data.get('weeks', [])
    db = get_db()
    db.execute("DELETE FROM available_weeks")
    for w in weeks:
        db.execute(
            "INSERT OR REPLACE INTO available_weeks (year, week, employee_id, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (w['year'], w['week'], w['employee_id'])
        )
    db.commit()
    db.close()
    return jsonify({"ok": True, "count": len(weeks)})

# -- Entries -----------------------------------------------------------------
@app.route('/api/entries', methods=['GET'])
def get_entries():
    employee = request.args.get('employee')
    datum = request.args.get('date')
    if not employee or not datum:
        return jsonify({"error": "employee and date required"}), 400
    if not check_auth(employee):
        return jsonify({"error": "unauthorized"}), 401
    db = get_db()
    rows = db.execute(
        """SELECT id, employee_id, datum, kundenname, kunden_id, aktivitaet, akt_code,
                  von, bis, pause, kommentar, synced
           FROM entries WHERE employee_id = ? AND datum = ? ORDER BY von, id""",
        (employee, datum)
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/entries', methods=['POST'])
def create_entry():
    data = request.get_json()
    required = ['employee_id', 'datum']
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"{field} required"}), 400
    if not check_auth(data['employee_id']):
        return jsonify({"error": "unauthorized"}), 401
    db = get_db()
    cursor = db.execute(
        """INSERT INTO entries (employee_id, datum, kundenname, kunden_id, aktivitaet, akt_code,
                                von, bis, pause, kommentar, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (
            data['employee_id'], data['datum'],
            data.get('kundenname'), data.get('kunden_id'),
            data.get('aktivitaet'), data.get('akt_code'),
            data.get('von'), data.get('bis'),
            data.get('pause', 0), data.get('kommentar')
        )
    )
    db.commit()
    entry_id = cursor.lastrowid
    row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    db.close()
    return jsonify(dict(row)), 201

@app.route('/api/entries/<int:entry_id>', methods=['PUT'])
def update_entry(entry_id):
    data = request.get_json()
    db = get_db()
    existing = db.execute("SELECT employee_id FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if not existing:
        db.close()
        return jsonify({"error": "not found"}), 404
    if not check_auth(existing["employee_id"]):
        db.close()
        return jsonify({"error": "unauthorized"}), 401
    db.execute(
        """UPDATE entries SET kundenname=?, kunden_id=?, aktivitaet=?, akt_code=?,
                              von=?, bis=?, pause=?, kommentar=?, synced=0,
                              updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (
            data.get('kundenname'), data.get('kunden_id'),
            data.get('aktivitaet'), data.get('akt_code'),
            data.get('von'), data.get('bis'),
            data.get('pause', 0), data.get('kommentar'),
            entry_id
        )
    )
    db.commit()
    row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    db.close()
    return jsonify(dict(row))

@app.route('/api/entries/<int:entry_id>', methods=['DELETE'])
def delete_entry(entry_id):
    db = get_db()
    row = db.execute("SELECT employee_id FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if row and not check_auth(row["employee_id"]):
        db.close()
        return jsonify({"error": "unauthorized"}), 401
    db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})

# -- Week & overview endpoints -----------------------------------------------
@app.route('/api/entries/week')
def get_week_entries():
    employee = request.args.get('employee')
    year = request.args.get('year', type=int)
    week = request.args.get('week', type=int)
    if not employee or not year or not week:
        return jsonify({"error": "employee, year and week required"}), 400
    if not check_auth(employee):
        return jsonify({"error": "unauthorized"}), 401
    import datetime as dt
    monday = dt.date.fromisocalendar(year, week, 1)
    sunday = dt.date.fromisocalendar(year, week, 7)
    db = get_db()
    rows = db.execute(
        """SELECT id, employee_id, datum, kundenname, kunden_id, aktivitaet, akt_code,
                  von, bis, pause, kommentar, synced
           FROM entries WHERE employee_id = ? AND datum BETWEEN ? AND ?
           ORDER BY datum, von, id""",
        (employee, monday.isoformat(), sunday.isoformat())
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/sync-status')
def get_sync_status():
    db = get_db()
    rows = db.execute(
        """SELECT employee_id, datum, COUNT(*) as count
           FROM entries WHERE synced = 0
           GROUP BY employee_id, datum ORDER BY datum, employee_id"""
    ).fetchall()
    db.close()
    import datetime as dt
    result = {}
    for r in rows:
        emp = r['employee_id']
        d = dt.date.fromisoformat(r['datum'])
        iso = d.isocalendar()
        kw_key = f"{iso[0]}-W{iso[1]:02d}"
        year, week = iso[0], iso[1]
        if emp not in result:
            result[emp] = {}
        if kw_key not in result[emp]:
            result[emp][kw_key] = {'year': year, 'week': week, 'count': 0}
        result[emp][kw_key]['count'] += r['count']
    out = []
    for emp, weeks in sorted(result.items()):
        for kw_key, info in sorted(weeks.items()):
            out.append({
                'employee_id': emp,
                'year': info['year'],
                'week': info['week'],
                'kw_label': f"KW {info['week']:02d} / {info['year']}",
                'unsynced_count': info['count']
            })
    return jsonify(out)

# -- Sync endpoints ----------------------------------------------------------
@app.route('/api/entries/unsynced')
def get_unsynced():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM entries WHERE synced = 0 ORDER BY datum, employee_id, von"
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/admin/reset', methods=['POST'])
def admin_reset():
    """Loescht alle Eintraege und verfuegbaren Wochen (nur Admin). Fuer sauberen Start."""
    if not is_admin_token(request.headers.get('X-Auth-Token', '')):
        return jsonify({"error": "unauthorized"}), 401
    db = get_db()
    cur = db.execute("DELETE FROM entries")
    deleted = cur.rowcount
    db.execute("DELETE FROM available_weeks")
    db.commit()
    db.close()
    return jsonify({"ok": True, "deleted_entries": deleted})

@app.route('/api/entries/mark-synced', methods=['POST'])
def mark_synced():
    data = request.get_json()
    ids = data.get('ids', [])
    if not ids:
        return jsonify({"ok": True})
    db = get_db()
    db.execute(
        f"UPDATE entries SET synced=1 WHERE id IN ({','.join('?' * len(ids))})",
        ids
    )
    db.commit()
    db.close()
    return jsonify({"ok": True, "synced": len(ids)})

# -- Frontend ----------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
