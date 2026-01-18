import os
import csv
import time
import shutil
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

# --- CONFIGURATION ---
# Stores file in your local Documents folder
DOC_PATH = Path.home() / "Documents" / "race_car_weights.csv"
FUEL_DENSITY = 6.2 

# --- DATA SCHEMA ---
# Defines the columns for the CSV file
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

# --- HELPERS ---
def safe_float(val):
    if val is None or str(val).strip() == "": return 0.0
    try: return float(val)
    except: return 0.0

def check_and_migrate_csv():
    """
    Ensures the CSV file exists and has the correct headers.
    If new features (like Tire Pressure) were added, this updates the file automatically.
    """
    if not DOC_PATH.exists():
        try:
            with open(DOC_PATH, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                writer.writeheader()
            print("--- CREATED NEW CSV FILE ---")
        except Exception as e:
            print(f"Error creating CSV: {e}")
        return

    # Check for missing columns in existing file
    try:
        with open(DOC_PATH, 'r', newline='', encoding='utf-8') as f:
            headers = next(csv.reader(f), [])
        
        missing = [h for h in FIELDNAMES if h not in headers]
        
        if missing:
            print(f"--- MIGRATING CSV: Adding {missing} ---")
            # Read all existing data
            with open(DOC_PATH, 'r', newline='', encoding='utf-8') as f:
                data = list(csv.DictReader(f))
            
            # Create backup
            shutil.copy(DOC_PATH, DOC_PATH.parent / "race_car_weights_backup.csv")
            
            # Rewrite with new headers
            with open(DOC_PATH, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                writer.writeheader()
                for row in data:
                    new_row = {k: row.get(k, "") for k in FIELDNAMES}
                    # Map legacy fuel column if it exists
                    if 'fuel_qty' in row and row['fuel_qty'] and not new_row['fuel_lbs']:
                         new_row['fuel_lbs'] = row['fuel_qty']
                    writer.writerow(new_row)
            print("--- MIGRATION COMPLETE ---")
            
    except Exception as e:
        print(f"Migration check failed: {e}")

# Run check on startup
check_and_migrate_csv()

def get_history(car_num=None):
    if not DOC_PATH.exists(): return []
    
    # Small delay to ensure file handles are closed
    time.sleep(0.05)
    
    try:
        with open(DOC_PATH, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            cleaned = []
            
            for r in reader:
                # Create a safe row with defaults
                row = {k: r.get(k, "") for k in FIELDNAMES}
                
                # Ensure numeric fields default to "0" if empty
                for key in FIELDNAMES:
                    if key not in ['date','car_num','adjustment_notes','sway_bar','is_baseline']:
                        if not row.get(key): row[key] = "0"
                
                cleaned.append(row)
                
            if car_num:
                return [r for r in cleaned if str(r['car_num']).strip() == str(car_num).strip()]
            return cleaned
    except Exception as e:
        print(f"Read Error: {e}")
        return []

# --- ROUTES ---

@app.route('/')
def index():
    selected_car = request.args.get('car_num', '1')
    history = get_history(car_num=selected_car)
    
    last_run = history[-1] if history else None
    baseline_run = next((r for r in reversed(history) if r['is_baseline'] == 'Yes'), None)
            
    next_num = 1
    if last_run:
        try: next_num = int(last_run['scale_num']) + 1
        except: next_num = 1
            
    return render_template('index.html', 
                           history=history, 
                           last_run=last_run, 
                           baseline_run=baseline_run,
                           next_num=next_num, 
                           selected_car=selected_car)

@app.route('/submit', methods=['POST'])
def submit():
    car_num = request.form.get('car_num', '1').strip()
    
    def get_f(k): return safe_float(request.form.get(k))
    
    # Inputs
    lf, rf, lr, rr = get_f('lf'), get_f('rf'), get_f('lr'), get_f('rr')
    t_lf, t_rf, t_lr, t_rr = get_f('t_lf'), get_f('t_rf'), get_f('t_lr'), get_f('t_rr')
    p_lf, p_rf, p_lr, p_rr = get_f('p_lf'), get_f('p_rf'), get_f('p_lr'), get_f('p_rr')
    
    # Fuel Calculation
    raw_fuel = get_f('fuel_input')
    unit = request.form.get('fuel_unit')
    fuel_lbs = raw_fuel * FUEL_DENSITY if unit == 'gal' else raw_fuel

    # Weight Math
    total = lf + rf + lr + rr
    cross_pct = round(((rf + lr) / total * 100), 2) if total > 0 else 0.0
    left_pct  = round(((lf + lr) / total * 100), 2) if total > 0 else 0.0
    rear_pct  = round(((lr + rr) / total * 100), 2) if total > 0 else 0.0

    # Sensitivity Learning (Compare against previous run)
    history = get_history(car_num=car_num)
    last_run = history[-1] if history else None
    
    wt_per_turn = 0.0
    fuel_sensitivity = 0.0
    net_turns = (t_rf + t_lr) - (t_lf + t_rr)
    
    if last_run:
        prev_cross = safe_float(last_run['cross_pct'])
        prev_rear = safe_float(last_run['rear_pct'])
        
        # Turn Sensitivity
        if net_turns != 0:
            wt_per_turn = (cross_pct - prev_cross) / net_turns
        else:
            wt_per_turn = safe_float(last_run['wt_per_turn'])
            
        # Fuel Sensitivity
        if fuel_lbs > 0.5 and net_turns == 0:
            fuel_sensitivity = (rear_pct - prev_rear) / fuel_lbs
        else:
            fuel_sensitivity = safe_float(last_run['fuel_sensitivity'])

    data = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "car_num": car_num,
        "scale_num": str(request.form.get('scale_num', 1)),
        "lf": lf, "rf": rf, "lr": lr, "rr": rr,
        "t_lf": t_lf, "t_rf": t_rf, "t_lr": t_lr, "t_rr": t_rr,
        "p_lf": p_lf, "p_rf": p_rf, "p_lr": p_lr, "p_rr": p_rr,
        "total": round(total, 1),
        "cross_pct": cross_pct, "left_pct": left_pct, "rear_pct": rear_pct,
        "fuel_lbs": round(fuel_lbs, 1),
        "adjustment_notes": request.form.get('adjustment_notes', ''),
        "sway_bar": request.form.get('sway_bar', 'Disconnected'),
        "wt_per_turn": round(wt_per_turn, 4),
        "fuel_sensitivity": round(fuel_sensitivity, 5),
        "is_baseline": "Yes" if request.form.get('is_baseline') else "No"
    }
    
    # Save to CSV
    file_exists = DOC_PATH.exists()
    try:
        with open(DOC_PATH, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            if not file_exists: writer.writeheader()
            writer.writerow(data)
            # Force write to disk
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        print(f"Save Error: {e}")

    return redirect(url_for('index', car_num=car_num))

@app.route('/delete/<car_num>/<scale_num>')
def delete(car_num, scale_num):
    if not DOC_PATH.exists(): return redirect(url_for('index', car_num=car_num))
    
    # Read all, filter out target, rewrite all
    try:
        with open(DOC_PATH, 'r', encoding='utf-8') as f: 
            rows = list(csv.DictReader(f))
        
        new_rows = [r for r in rows if not (str(r['car_num']) == str(car_num) and str(r['scale_num']) == str(scale_num))]
        
        with open(DOC_PATH, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(new_rows)
    except Exception as e:
        print(f"Delete Error: {e}")
            
    return redirect(url_for('index', car_num=car_num))

if __name__ == '__main__':
    app.run(debug=True, port=5001)