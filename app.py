import os
import csv
import sqlite3
import io
import time
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, g, make_response

app = Flask(__name__)

# --- CONFIGURATION ---
DB_NAME = "race_data.db"
# Backup path
DOC_PATH = Path.home() / "Documents" / "race_car_weights.csv"
FUEL_DENSITY = 6.2 

# --- SCHEMA ---
FIELDNAMES = [
    "date", "car_num", "scale_num", 
    "lf", "rf", "lr", "rr", 
    "t_lf", "t_rf", "t_lr", "t_rr", 
    "p_lf", "p_rf", "p_lr", "p_rr",
    "total", "cross_pct", "left_pct", "rear_pct", 
    "fuel_lbs", "adjustment_notes", "sway_bar",
    "wt_per_turn", "fuel_sensitivity", 
    "is_baseline"
]

# --- DATABASE CONNECTION ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_NAME)
        db.row_factory = sqlite3.Row 
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cols = ", ".join([f"{f} TEXT" for f in FIELDNAMES]) 
        cursor.execute(f"CREATE TABLE IF NOT EXISTS setups ({cols})")
        db.commit()

def import_csv_to_sqlite():
    if not DOC_PATH.exists(): return
    with app.app_context():
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT count(*) FROM setups")
        if cur.fetchone()[0] > 0: return 
        
        try:
            with open(DOC_PATH, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    clean_row = [row.get(k, "0") for k in FIELDNAMES]
                    placeholders = ",".join(["?"] * len(FIELDNAMES))
                    cur.execute(f"INSERT INTO setups VALUES ({placeholders})", clean_row)
                db.commit()
        except: pass

init_db()
import_csv_to_sqlite()

# --- HELPERS ---
def safe_float(val):
    try: return float(val) if val else 0.0
    except: return 0.0

def write_backup_csv(data_dict):
    try:
        exists = DOC_PATH.exists()
        DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(DOC_PATH, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            if not exists: writer.writeheader()
            writer.writerow(data_dict)
    except: pass

# --- ROUTES ---

@app.route('/')
def index():
    selected_car = request.args.get('car_num', '1')
    db = get_db()
    cur = db.execute("SELECT * FROM setups WHERE car_num = ? ORDER BY date ASC", (selected_car,))
    history = [dict(row) for row in cur.fetchall()]
    
    last_run = history[-1] if history else None
    baseline_run = next((r for r in reversed(history) if r['is_baseline'] == 'Yes'), None)
    
    next_num = int(last_run['scale_num']) + 1 if last_run else 1
            
    return render_template('index.html', history=history, last_run=last_run, baseline_run=baseline_run, next_num=next_num, selected_car=selected_car)

@app.route('/download/<car_num>')
def download_csv(car_num):
    """Generates a CSV file for the selected car and serves it as a download."""
    db = get_db()
    cur = db.execute("SELECT * FROM setups WHERE car_num = ? ORDER BY date ASC", (car_num,))
    rows = cur.fetchall()

    # Use StringIO to build CSV in memory (saves SD card writes)
    si = io.StringIO()
    writer = csv.DictWriter(si, fieldnames=FIELDNAMES)
    writer.writeheader()
    for row in rows:
        writer.writerow(dict(row))

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=Car_{car_num}_Data.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route('/submit', methods=['POST'])
def submit():
    car_num = request.form.get('car_num', '1').strip()
    def get_f(k): return safe_float(request.form.get(k))
    
    lf, rf, lr, rr = get_f('lf'), get_f('rf'), get_f('lr'), get_f('rr')
    t_lf, t_rf, t_lr, t_rr = get_f('t_lf'), get_f('t_rf'), get_f('t_lr'), get_f('t_rr')
    p_lf, p_rf, p_lr, p_rr = get_f('p_lf'), get_f('p_rf'), get_f('p_lr'), get_f('p_rr')
    
    fuel_lbs = get_f('fuel_input') * (FUEL_DENSITY if request.form.get('fuel_unit') == 'gal' else 1)
    total = lf + rf + lr + rr
    cross_pct = round(((rf + lr) / total * 100), 2) if total > 0 else 0.0
    
    db = get_db()
    cur = db.execute("SELECT * FROM setups WHERE car_num = ? ORDER BY date DESC LIMIT 1", (car_num,))
    last_run_row = cur.fetchone()
    last_run = dict(last_run_row) if last_run_row else None

    wt_per_turn = 0.0
    fuel_sensitivity = 0.0
    net_turns = (t_rf + t_lr) - (t_lf + t_rr)
    
    if last_run:
        prev_cross = safe_float(last_run['cross_pct'])
        prev_rear = safe_float(last_run['rear_pct'])
        if net_turns != 0: wt_per_turn = (cross_pct - prev_cross) / net_turns
        else: wt_per_turn = safe_float(last_run['wt_per_turn'])
        if fuel_lbs > 0.5 and net_turns == 0:
            current_rear = round(((lr + rr) / total * 100), 2) if total else 0
            fuel_sensitivity = (current_rear - prev_rear) / fuel_lbs
        else: fuel_sensitivity = safe_float(last_run['fuel_sensitivity'])

    data = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "car_num": car_num,
        "scale_num": str(request.form.get('scale_num', 1)),
        "lf": lf, "rf": rf, "lr": lr, "rr": rr,
        "t_lf": t_lf, "t_rf": t_rf, "t_lr": t_lr, "t_rr": t_rr,
        "p_lf": p_lf, "p_rf": p_rf, "p_lr": p_lr, "p_rr": p_rr,
        "total": round(total, 1), "cross_pct": cross_pct, 
        "left_pct": round(((lf + lr) / total * 100), 2) if total else 0,
        "rear_pct": round(((lr + rr) / total * 100), 2) if total else 0,
        "fuel_lbs": round(fuel_lbs, 1), 
        "adjustment_notes": request.form.get('adjustment_notes', ''),
        "sway_bar": request.form.get('sway_bar', 'Disconnected'),
        "wt_per_turn": round(wt_per_turn, 4), "fuel_sensitivity": round(fuel_sensitivity, 5),
        "is_baseline": "Yes" if request.form.get('is_baseline') else "No"
    }
    
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    db.execute(f"INSERT INTO setups ({cols}) VALUES ({placeholders})", list(data.values()))
    db.commit()
    write_backup_csv(data)

    return redirect(url_for('index', car_num=car_num))

@app.route('/delete/<car_num>/<scale_num>')
def delete(car_num, scale_num):
    db = get_db()
    db.execute("DELETE FROM setups WHERE car_num = ? AND scale_num = ?", (car_num, scale_num))
    db.commit()
    return redirect(url_for('index', car_num=car_num))

if __name__ == '__main__':
    app.run(debug=True, port=5001)