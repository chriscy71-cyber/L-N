# =====================================================================
# 1. ΑΡΧΙΚΟΠΟΙΗΣΗ, ΒΑΣΗ ΔΕΔΟΜΕΝΩΝ & ΧΑΡΤΗΣ ΜΕΣΟΓΕΙΟΥ (v7.3 - FIXED)
# =====================================================================
import os
import sqlite3
import smtplib
import logging
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import openpyxl
from io import BytesIO

try:
    from supabase import create_client, Client
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
    if SUPABASE_URL and SUPABASE_KEY:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    else:
        supabase = None
except ImportError:
    supabase = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'lkn_ast_larnakas_secure_key_2026')

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DB_NAME = 'database.db'
DEFAULT_ADMIN_PASSWORD = 'LKN_ADMIN_2026'
BUCKET_NAME = 'larnaca_files'

def download_database_from_supabase():
    if not supabase: return False
    try:
        files = supabase.storage.from_(BUCKET_NAME).list()
        if any(f.get('name') == DB_NAME for f in files):
            res = supabase.storage.from_(BUCKET_NAME).download(DB_NAME)
            with open(DB_NAME, 'wb') as f:
                f.write(res)
            return True
    except Exception as e:
        logging.error(f"[SUPABASE STARTUP ERROR] {e}")
    return False

def sync_database_to_supabase():
    if not supabase or not os.path.exists(DB_NAME): return
    try:
        with open(DB_NAME, 'rb') as f:
            supabase.storage.from_(BUCKET_NAME).upload(file=f, path=DB_NAME, file_options={"upsert": "true"})
    except Exception as e:
        logging.error(f"[SUPABASE SYNC ERROR] {e}")

def get_supabase_storage_usage():
    if not supabase: return 0
    try:
        files = supabase.storage.from_(BUCKET_NAME).list()
        total = 0
        for f in files:
            if isinstance(f, dict) and 'metadata' in f and f['metadata']:
                total += f['metadata'].get('size', 0)
        return total
    except Exception:
        return 0

@app.before_request
def ensure_database_loaded():
    if not os.path.exists(DB_NAME) or os.path.getsize(DB_NAME) == 0:
        download_database_from_supabase()

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            station TEXT NOT NULL,
            status TEXT DEFAULT 'Pending',
            role TEXT DEFAULT 'User',
            password TEXT,
            email TEXT,
            admin_level INTEGER DEFAULT 0,
            custom_permissions TEXT DEFAULT ''
        )''')
        
        for col, col_type in [("password", "TEXT"), ("email", "TEXT"), ("admin_level", "INTEGER DEFAULT 0"), ("custom_permissions", "TEXT DEFAULT ''")]:
            try: cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type};")
            except sqlite3.OperationalError: pass

        cursor.execute('''CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            is_custom INTEGER DEFAULT 0
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS channel_members (
            channel_id INTEGER,
            user_id INTEGER,
            FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(channel_id, user_id)
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS user_notification_settings (
            user_id INTEGER,
            channel_id INTEGER,
            enabled INTEGER DEFAULT 1,
            PRIMARY KEY(user_id, channel_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS logbook_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_name TEXT NOT NULL,
            author_name TEXT NOT NULL,
            incident_category TEXT DEFAULT 'Γενικό',
            content TEXT NOT NULL,
            filename TEXT,
            mega_link TEXT,
            timestamp TEXT NOT NULL
        )''')
        
        try: cursor.execute("ALTER TABLE logbook_entries ADD COLUMN incident_category TEXT DEFAULT 'Γενικό';")
        except sqlite3.OperationalError: pass
        try: cursor.execute("ALTER TABLE logbook_entries ADD COLUMN mega_link TEXT;")
        except sqlite3.OperationalError: pass

        cursor.execute('''CREATE TABLE IF NOT EXISTS map_markers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            incident_type TEXT NOT NULL,
            description TEXT NOT NULL,
            author TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS vessel_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vessel_name TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            incident TEXT,
            damage TEXT,
            working_hours TEXT,
            repair_report TEXT,
            author TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            filename TEXT NOT NULL,
            mega_link TEXT,
            uploader TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS roster_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            year_month TEXT NOT NULL,
            day INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            UNIQUE(user_id, year_month, day),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )''')
        
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password', ?)", (generate_password_hash(DEFAULT_ADMIN_PASSWORD),))
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('roster_enabled', '0')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_server', 'smtp.gmail.com')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_port', '587')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_user', '')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_pass', '')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('external_station_url', '')")
        
        initial_channels = [
            'Συμβάντα', 'Ναυτικός Σταθμός', 'Μαρίνα', 'Λιμάνι', 
            'Τεχνικοί', 'Κυβερνήτες Α\'', 'Ad-hoc', 'Αρχείο', 
            'E-Καθήκοντα', 'Ψηφιακή Βιβλιοθήκη', 'Roster Βάρδιας'
        ]
        for ch in initial_channels:
            cursor.execute("INSERT OR IGNORE INTO channels (name, is_custom) VALUES (?, 0)", (ch,))
            
        cursor.execute("UPDATE channels SET name = 'Συμβάντα' WHERE name = 'Γενικό Κανάλι'")
        cursor.execute("UPDATE logbook_entries SET channel_name = 'Συμβάντα' WHERE channel_name = 'Γενικό Κανάλι'")
        cursor.execute("UPDATE channels SET name = 'Ad-hoc' WHERE name = 'Διοίκηση'")

        for i in range(1, 11):
            cursor.execute("INSERT OR IGNORE INTO channels (name, is_custom) VALUES (?, 0)", (f'Σκάφος {i}',))
        
        conn.commit()

init_db()
sync_database_to_supabase()
ROSTER_SYMBOLS = ['', 'Μ', 'Ν', 'SL', 'Α', 'RD', 'T', 'Υ', 'Π', 'Ε', 'ΑΠ']
INCIDENT_CATEGORIES = ['🚨 Έρευνα & Διάσωση (SAR)', '⛵ Παράνομη Μετανάστευση', '🛢️ Θαλάσσια Ρύπανση', '🔒 Παραβίαση ISPS', '🔧 Τεχνικό / Άλλο Περιστατικό']

# =====================================================================
# 2. ΔΙΑΧΕΙΡΙΣΗ ΑΡΧΕΙΩΝ, ΣΥΓΧΡΟΝΙΣΜΟΣ, WEBHOOKS & STYLE (v7.3)
# =====================================================================
def upload_file_to_supabase(file_path, filename, overwrite=False):
    if not supabase: return None
    try:
        if overwrite:
            try: supabase.storage.from_(BUCKET_NAME).remove([filename])
            except: pass
        with open(file_path, 'rb') as f:
            supabase.storage.from_(BUCKET_NAME).upload(file=f, path=filename, file_options={"upsert": "true"})
        return supabase.storage.from_(BUCKET_NAME).get_public_url(filename)
    except: return None

def delete_file_from_supabase(filename):
    if not supabase or not filename: return
    try: supabase.storage.from_(BUCKET_NAME).remove([filename])
    except: pass

def clean_old_channel_entries():
    two_months_ago = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d %H:%M:%S')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, filename FROM logbook_entries WHERE channel_name != 'Αρχείο' AND timestamp < ?", (two_months_ago,))
        old_entries = cursor.fetchall()
        for row in old_entries:
            if row['filename']: delete_file_from_supabase(row['filename'])
            cursor.execute("DELETE FROM logbook_entries WHERE id = ?", (row['id'],))
        conn.commit()
    sync_database_to_supabase()

def get_current_admin_password_hash():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'admin_password'")
        return cursor.fetchone()[0]

def is_roster_active():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'roster_enabled'")
        return cursor.fetchone()[0] == '1'

def get_super_admin_phone_and_id():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, phone FROM users WHERE role = 'Admin' AND status = 'Approved' AND admin_level = 1 ORDER BY id ASC LIMIT 1")
        row = cursor.fetchone()
        if row: return row['id'], row['phone']
        cursor.execute("SELECT id, phone FROM users WHERE role = 'Admin' AND status = 'Approved' ORDER BY admin_level ASC, id ASC LIMIT 1")
        row2 = cursor.fetchone()
        return (row2['id'], row2['phone']) if row2 else (None, "LKN_ADMIN_2026")

def send_adhoc_to_external_station(author, content):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'external_station_url'")
        row = cursor.fetchone()
        if not row or not row['value']: return
        target_url = row['value'].strip()
        _, super_phone = get_super_admin_phone_and_id()

    try:
        headers = {"Authorization": f"Bearer {super_phone}", "Content-Type": "application/json"}
        payload = {"author": author, "content": content, "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        requests.post(target_url, json=payload, headers=headers, timeout=5)
    except Exception as e:
        logging.error(f"[EXTERNAL STATION SYNC ERROR] {e}")

def send_real_email(to_email, subject, body, attachment_bytes=None, attachment_name="roster.xlsx"):
    if not to_email or '@' not in to_email: return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings WHERE key IN ('smtp_server', 'smtp_port', 'smtp_user', 'smtp_pass')")
        s = {r['key']: r['value'] for r in cursor.fetchall()}
    try:
        msg = MIMEMultipart()
        msg['From'], msg['To'], msg['Subject'] = s.get('smtp_user', ''), to_email, subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        if attachment_bytes:
            from email.mime.application import MIMEApplication
            att = MIMEApplication(attachment_bytes.read(), Name=attachment_name)
            att['Content-Disposition'] = f'attachment; filename="{attachment_name}"'
            msg.attach(att)
        server = smtplib.SMTP(s.get('smtp_server', 'smtp.gmail.com'), int(s.get('smtp_port', '587')))
        server.starttls()
        server.login(s.get('smtp_user', ''), s.get('smtp_pass', ''))
        server.sendmail(s.get('smtp_user', ''), to_email, msg.as_string())
        server.quit()
    except Exception as e:
        logging.error(f"[SMTP ERROR] {e}")

BASE_STYLE = """
<style>
    body { 
        background: linear-gradient(rgba(15, 23, 42, 0.75), rgba(30, 58, 138, 0.8), rgba(3, 105, 161, 0.75)), 
                    url('https://images.unsplash.com/photo-1507525428034-b723cf961d3e?q=80&w=1920&auto=format&fit=crop'); 
        background-size: cover; background-position: center; background-attachment: fixed; 
        font-family: Arial, sans-serif; color: #fff; margin: 0; padding: 20px; 
    }
    .container { max-width: 1100px; margin: 0 auto; background: rgba(255, 255, 255, 0.96); color: #333; padding: 25px; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.6); }
    a { color: #0056b3; text-decoration: none; } a:hover { text-decoration: underline; }
    input, select, textarea { padding: 8px; margin: 5px 0; border: 1px solid #ccc; border-radius: 4px; width: 100%; box-sizing: border-box; }
    button, .btn-link { display: inline-block; background: #0056b3; color: white !important; border: none; padding: 10px 16px; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 13px; text-decoration: none; margin: 3px; text-align: center; box-shadow: 0 2px 5px rgba(0,0,0,0.15); transition: all 0.2s; }
    button:hover, .btn-link:hover { background: #004085; transform: translateY(-1px); }
    .btn-danger { background: #dc2626 !important; } .btn-success { background: #16a34a !important; } .btn-warning { background: #ca8a04 !important; }
    .btn-home { background: #f97316 !important; color: #000 !important; font-weight: bold; }
    .btn-home:hover { background: #ea580c !important; }
    .btn-map { background: #0284c7 !important; font-size: 14px; }
    .btn-map:hover { background: #0369a1 !important; }
    .btn-channel { background: #334155 !important; font-size: 13px; text-align: left; display: inline-block; width: 23%; margin: 5px 1%; padding: 12px; border-radius: 6px; }
    .btn-channel:hover { background: #1e293b !important; }
    .btn-vessel { background: #0f766e !important; font-size: 13px; text-align: left; display: inline-block; width: 23%; margin: 5px 1%; padding: 12px; border-radius: 6px; }
    .btn-vessel:hover { background: #115e59 !important; }
    .nav-buttons { display: flex; flex-wrap: wrap; gap: 8px; margin: 15px 0; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; background: #fff; font-size: 13px; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: center; color: #333; } th { background: #f2f2f2; }
    hr { border: 0; height: 1px; background: #ccc; margin: 20px 0; }
    .viber-chat-container { background: #e7ebf0; padding: 15px; border-radius: 8px; max-height: 500px; overflow-y: auto; margin-bottom: 15px; }
    .chat-bubble { background: #ffffff; padding: 10px 14px; border-radius: 12px; margin-bottom: 10px; max-width: 80%; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .chat-author { font-size: 12px; font-weight: bold; color: #075e54; margin-bottom: 3px; }
    .chat-time { font-size: 10px; color: #888; float: right; margin-left: 10px; }
    .chat-text { font-size: 13px; color: #222; word-break: break-word; }
</style>
<div style="max-width:1100px; margin:0 auto 10px; display:flex; justify-content:space-between; color:#e2e8f0; font-weight:bold;">
    <span>⚓ Σταθμοί Λάρνακας version 7.3 (Mediterranean Map & Incident Board)</span>
    {% if session.get('user_phone') %}<a href="/user_settings" style="color:white;">⚙️ Ρυθμίσεις Χρήστη</a>{% endif %}
</div>
"""

# =====================================================================
# 3. DASHBOARD, ΚΑΝΑΛΙΑ, ΣΚΑΦΗ ΩΣ ΠΛΗΚΤΡΑ ΚΑΙ ΕΠΙΧΕΙΡΗΣΙΑΚΟΣ ΧΑΡΤΗΣ
# =====================================================================
@app.route('/')
def index():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT id, status, name, role, password, email FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if not user: session.clear(); return redirect(url_for('login'))
        if not user['password'] or not user['email']: return redirect(url_for('set_password'))
        if user['status'] != 'Approved' and user['role'] != 'Admin':
            return render_template_string(BASE_STYLE + "<div class='container'><h3>Ο λογαριασμός σας αναμένει έγκριση.</h3><div class='nav-buttons'><a href='/user_settings' class='btn-link btn-warning'>⚙️ Ρυθμίσεις Χρήστη</a><a href='/logout' class='btn-link btn-danger'>🚪 Αποσύνδεση</a></div></div>")
        
        if user['role'] == 'Admin': cursor.execute("SELECT name FROM channels")
        else: cursor.execute("SELECT c.name FROM channels c JOIN channel_members cm ON c.id = cm.channel_id WHERE cm.user_id = ?", (user['id'],))
        channels = sorted(list(set([r[0] for r in cursor.fetchall()])))
    
    vessel_channels = [c for c in channels if "Σκάφος" in c or "Ταχύπλοο" in c]
    standard_channels = [c for c in channels if c not in vessel_channels and c not in ['Ψηφιακή Βιβλιοθήκη', 'Roster Βάρδιας']]

    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Καλώς ορίσατε, {{ name }}</h2>
        <div class="nav-buttons" style="margin-bottom: 20px;">
            <a href="/admin" class="btn-link btn-warning">🛠️ Πάνελ Διαχείρισης</a>
            <a href="/map" class="btn-link btn-map">🗺️ Επιχειρησιακός Χάρτης Μεσογείου</a>
            <a href="/library" class="btn-link">📚 Ψηφιακή Βιβλιοθήκη</a>
            {% if roster_active or role == 'Admin' %}<a href="/roster" class="btn-link">📋 Roster Βάρδιας</a>{% endif %}
            <a href="/guide" class="btn-link" style="background:#0891b2;">📖 Οδηγίες Χρήσης</a>
            <a href="/user_settings" class="btn-link btn-warning">⚙️ Ρυθμίσεις Χρήστη</a>
            <a href="/logout" class="btn-link btn-danger">🚪 Αποσύνδεση</a>
        </div>
        <hr>
        <h3>🚨 Επιχειρησιακά Κανάλια & Logbooks</h3>
        <div style="display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 15px;">
            {% for ch in standard_channels %}
                <a href="/logbook/{{ ch }}" class="btn-link btn-channel">
                    {% if ch == 'Συμβάντα' %}🚨 <b>{{ ch }}</b>{% elif ch == 'Ad-hoc' %}🌐 <b>{{ ch }} (Gateway)</b>{% else %}💬 {{ ch }}{% endif %}
                </a>
            {% endfor %}
        </div>
        <hr>
        <h3>🛥️ Logbooks Περιπολικών Σκαφών</h3>
        <div style="display: flex; flex-wrap: wrap; gap: 4px;">
            {% for v in vessel_channels %}
                <a href="/vessel_log/{{ v }}" class="btn-link btn-vessel">🛥️ <b>{{ v }}</b></a>
            {% endfor %}
        </div>
    </div>
    """, name=user['name'], standard_channels=standard_channels, vessel_channels=vessel_channels, roster_active=is_roster_active(), role=user['role'])

@app.route('/map')
def operational_map():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT name, role FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        markers = cursor.execute("SELECT * FROM map_markers ORDER BY id DESC").fetchall()
    
    markers_list = [dict(m) for m in markers]
    
    return render_template_string(BASE_STYLE + """
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <div class="container" style="max-width: 1200px;">
        <h2>🗺️ Επιχειρησιακός Χάρτης - Λεκάνη Ανατολικής Μεσογείου</h2>
        <a href="/" class="btn-link btn-home">⮜⮜ Επιστροφή στην Αρχική</a>
        <p style="font-size:13px; color:#555; margin-top:5px;">💡 Κάντε <b>απλό κλικ</b> οπουδήποτε στον χάρτη για να καρφώσετε νέα σημαία συμβάντος.</p>
        
        <div id="map" style="height: 600px; width: 100%; border-radius: 8px; border: 2px solid #ccc; margin-top: 15px;"></div>
    </div>

    <div id="markerModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.6); z-index:9999; justify-content:center; align-items:center;">
        <div style="background:#fff; color:#333; padding:25px; border-radius:8px; width:400px; box-shadow:0 4px 15px rgba(0,0,0,0.3);">
            <h3>📍 Νέα Σημαία Συμβάντος</h3>
            <form method="POST" action="/api/add_marker">
                <input type="hidden" id="modal_lat" name="lat">
                <input type="hidden" id="modal_lng" name="lng">
                Είδος Συμβάντος:
                <select name="incident_type" required>
                    {% for cat in incident_categories %}
                    <option value="{{ cat }}">{{ cat }}</option>
                    {% endfor %}
                </select><br>
                Περιγραφή / Στοιχεία:
                <textarea name="description" rows="3" required placeholder="Περιγραφή σκάφους,στίγματος ή περιστατικού..."></textarea><br><br>
                <button type="submit" class="btn-success">💾 Αποθήκευση Σημαίας</button>
                <button type="button" class="btn-danger" onclick="document.getElementById('markerModal').style.display='none'">❌ Άκυρο</button>
            </form>
        </div>
    </div>

    <script>
        var map = L.map('map').setView([34.915, 33.629], 7);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 18,
            attribution: '© OpenStreetMap Contributors'
        }).addTo(map);

        var existingMarkers = {{ markers_list | tojson }};
        var isAdmin = {{ 'true' if user['role'] == 'Admin' else 'false' }};

        existingMarkers.forEach(function(m) {
            var popupContent = "<b>" + m.incident_type + "</b><br>" + m.description + "<br><hr style='margin:5px 0'><small>Από: " + m.author + " (" + m.timestamp + ")</small>";
            if (isAdmin) {
                popupContent += "<br><br><form method='POST' action='/api/delete_marker'><input type='hidden' name='marker_id' value='" + m.id + "'><button class='btn-danger' style='font-size:10px; padding:4px 8px;'>🗑️ Διαγραφή Σημαίας</button></form>";
            }
            L.marker([m.lat, m.lng]).addTo(map).bindPopup(popupContent);
        });

        map.on('click', function(e) {
            document.getElementById('modal_lat').value = e.latlng.lat;
            document.getElementById('modal_lng').value = e.latlng.lng;
            document.getElementById('markerModal').style.display = 'flex';
        });
    </script>
    """, incident_categories=INCIDENT_CATEGORIES, markers_list=markers_list, user=user)

@app.route('/api/add_marker', methods=['POST'])
def add_marker():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT name FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        cursor.execute("INSERT INTO map_markers (lat, lng, incident_type, description, author, timestamp) VALUES (?, ?, ?, ?, ?, ?)", 
                       (request.form.get('lat'), request.form.get('lng'), request.form.get('incident_type'), request.form.get('description'), user['name'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    sync_database_to_supabase()
    return redirect(url_for('operational_map'))

@app.route('/api/delete_marker', methods=['POST'])
def delete_marker():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT role FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if user and user['role'] == 'Admin':
            cursor.execute("DELETE FROM map_markers WHERE id = ?", (request.form.get('marker_id'),))
            conn.commit()
            sync_database_to_supabase()
    return redirect(url_for('operational_map'))

# =====================================================================
# 4. ΟΔΗΓΙΕΣ ΧΡΗΣΗΣ, LOGBOOKS ΜΕ ΚΑΤΗΓΟΡΙΕΣ ΚΑΙ WEBHOOKS
# =====================================================================
@app.route('/guide')
def guide():
    if 'user_phone' not in session: return redirect(url_for('login'))
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>📖 Γενικές Οδηγίες Χρήσης Συστήματος v7.3</h2>
        <a href="/" class="btn-link btn-home">⮜⮜ Επιστροφή στην Αρχική</a>
        <hr>
        <h3>1. Επιχειρησιακός Χάρτης Μεσογείου</h3>
        <p>Μεταβείτε στον χάρτη από την αρχική οθόνη για να δείτε τη λεκάνη της Ανατολικής Μεσογείου. Με απλό κλικ οπουδήποτε στον χάρτη μπορείτε να καταχωρήσετε σημαία συμβάντος (SAR, Μετανάστευση, Ρύπανση, ISPS). Οι διαχειριστές έχουν δικαίωμα διαγραφής των σημαιών.</p>
        
        <h3>2. Κανάλι «Συμβάντα» & Κατηγορίες</h3>
        <p>Το πρώην Γενικό Κανάλι λειτουργεί πλέον ως κανάλι «Συμβάντα», υποστηρίζοντας τυποποιημένη κατηγοριοποίηση περιστατικών για καλύτερη ταξινόμηση και άμεση αξιολόγηση.</p>

        <h3>3. Κανάλι Ad-hoc & Inter-Station Gateway</h3>
        <p>Το κανάλι Ad-hoc συνδέει αυτόματα τον σταθμό μας με απομακρυσμένους σταθμούς μέσω κρυπτογραφημένων Webhooks POST (με χρήση Bearer Token).</p>

        <h3>4. Logbooks Σκαφών & Ηχητικά Alerts</h3>
        <p>Τα σκάφη εμφανίζονται ως αυτόνομα πλήκτρα ταχείας πρόσβασης. Κάθε 10 δευτερόλεπτα γίνεται έλεγχος για νέα μηνύματα με ηχητική ειδοποίηση.</p>
        <hr>
        <a href="/" class="btn-link btn-home">⮜⮜ Επιστροφή στην Αρχική</a>
    </div>
    """)

@app.route('/api/check_new/<channel_name>/<int:last_id>')
def check_new_messages(channel_name, last_id):
    with get_db_connection() as conn:
        max_id = conn.cursor().execute("SELECT MAX(id) FROM logbook_entries WHERE channel_name = ?", (channel_name,)).fetchone()[0] or 0
    return jsonify({"has_new": max_id > last_id, "new_max_id": max_id})

@app.route('/api/webhook/adhoc', methods=['POST'])
def adhoc_webhook():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({"error": "Unauthorized"}), 401
    token_phone = auth_header.split(' ')[1].strip()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        valid_admin = cursor.execute("SELECT id FROM users WHERE phone = ? AND role = 'Admin' AND status = 'Approved'", (token_phone,)).fetchone()
        if not valid_admin:
            return jsonify({"error": "Forbidden"}), 403
        data = request.json
        if not data or 'content' not in data:
            return jsonify({"error": "Bad Request"}), 400
        cursor.execute("INSERT INTO logbook_entries (channel_name, author_name, incident_category, content, timestamp) VALUES ('Ad-hoc', ?, 'Διασύνδεση Σταθμών', ?, ?)", 
                       (f"🌐 {data.get('author', 'Σταθμός')}", data.get('content', ''), data.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        conn.commit()
    sync_database_to_supabase()
    return jsonify({"status": "success"}), 200

@app.route('/logbook/<channel_name>', methods=['GET', 'POST'])
def logbook(channel_name):
    if 'user_phone' not in session: return redirect(url_for('login'))
    clean_old_channel_entries()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT name, role FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if request.method == 'POST':
            content = request.form.get('content', '')
            inc_cat = request.form.get('incident_category', 'Γενικό')
            file = request.files.get('file')
            ov = True if request.form.get('overwrite_file') == 'on' else False
            fname, clink = None, None
            if file and file.filename != '':
                raw = secure_filename(file.filename)
                fname = raw if ov else f"{uuid.uuid4().hex}_{raw}"
                lpath = os.path.join(UPLOAD_FOLDER, fname)
                file.save(lpath)
                clink = upload_file_to_supabase(lpath, fname, overwrite=ov)
            
            cursor.execute("INSERT INTO logbook_entries (channel_name, author_name, incident_category, content, filename, mega_link, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                           (channel_name, user['name'], inc_cat, content, fname, clink, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit(); sync_database_to_supabase()

            if channel_name == 'Ad-hoc':
                send_adhoc_to_external_station(user['name'], content)

        entries = cursor.execute("SELECT * FROM logbook_entries WHERE channel_name = ? ORDER BY id DESC", (channel_name,)).fetchall()
        max_id = entries[0]['id'] if entries else 0
    is_archive = (channel_name == 'Αρχείο')
    is_incidents = (channel_name == 'Συμβάντα')
    return render_template_string(BASE_STYLE + """
    <div class="container"><h2>💬 Κανάλι: {{ channel_name }}</h2><a href="/" class="btn-link btn-home">⮜⮜ Επιστροφή στην Αρχική</a>
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; margin: 15px 0;">
            {% if is_incidents %}
            Κατηγορία Συμβάντος:
            <select name="incident_category" required>
                {% for cat in incident_categories %}
                <option value="{{ cat }}">{{ cat }}</option>
                {% endfor %}
            </select><br>
            {% endif %}
            <textarea name="content" required placeholder="Καταγραφή συμβάντος ή μηνύματος..."></textarea><br>
            Συνημμένο Αρχείο: <input type="file" name="file"><br>
            <label><input type="checkbox" name="overwrite_file" style="width:auto;"> Αντικατάσταση αρχείου στο Cloud</label><br><br>
            <button>📝 Καταχώρηση Καναλιού</button>
        </form>
        <div class="{% if not is_archive %}viber-chat-container{% endif %}">
            {% for e in entries %}
                <div class="chat-bubble" style="{% if is_archive %}background:#fff; max-width:100%; border:1px solid #ddd; margin-bottom:10px;{% endif %}">
                    <div class="chat-author">{{ e['author_name'] }} {% if e['incident_category'] %}<span style="background:#0284c7; color:#fff; padding:2px 6px; border-radius:4px; font-size:10px; margin-left:5px;">{{ e['incident_category'] }}</span>{% endif %} <span class="chat-time">{{ e['timestamp'] }}</span></div>
                    <div class="chat-text">{{ e['content'] }}</div>
                    {% if e['mega_link'] %}<div><a href="{{ e['mega_link'] }}" target="_blank" class="btn-link" style="font-size:11px;">📥 Λήψη Αρχείου</a></div>{% endif %}
                    {% if role == 'Admin' %}<form method="POST" action="/delete_logbook_message"><input type="hidden" name="entry_id" value="{{ e['id'] }}"><input type="hidden" name="channel_name" value="{{ channel_name }}"><button class="btn-danger" style="font-size:10px;">🗑️ Διαγραφή</button></form>{% endif %}
                </div>
            {% endfor %}
        </div>
    </div>
    {% if not is_archive %}
    <script>
        let lastId = {{ max_id }};
        setInterval(() => {
            fetch(`/api/check_new/{{ channel_name }}/${lastId}`).then(r => r.json()).then(d => {
                if(d.has_new) { new Audio('https://actions.google.com/sounds/v1/alarms/beep_short.ogg').play(); setTimeout(() => location.reload(), 1000); }
            });
        }, 10000);
    </script>
    {% endif %}
    """, channel_name=channel_name, entries=entries, is_archive=is_archive, is_incidents=is_incidents, role=user['role'], max_id=max_id, incident_categories=INCIDENT_CATEGORIES)

# =====================================================================
# 5. AUTHENTICATION, VESSEL LOGS, LIBRARY, ROSTER & ADMIN PANEL
# =====================================================================
@app.route('/delete_logbook_message', methods=['POST'])
def delete_logbook_message():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT filename FROM logbook_entries WHERE id = ?", (request.form.get('entry_id'),)).fetchone()
        if row and row['filename']: delete_file_from_supabase(row['filename'])
        cursor.execute("DELETE FROM logbook_entries WHERE id = ?", (request.form.get('entry_id'),))
        conn.commit()
    sync_database_to_supabase()
    return redirect(url_for('logbook', channel_name=request.form.get('channel_name')))

@app.route('/vessel_log/<vessel_name>', methods=['GET', 'POST'])
def vessel_log(vessel_name):
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT name, role FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if request.method == 'POST':
            cursor.execute("INSERT INTO vessel_logs (vessel_name, entry_date, incident, damage, working_hours, repair_report, author, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                           (vessel_name, request.form.get('entry_date', datetime.now().strftime('%Y-%m-%d')), request.form.get('incident', ''), request.form.get('damage', ''), request.form.get('working_hours', ''), request.form.get('repair_report', ''), user['name'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit(); sync_database_to_supabase()
        entries = cursor.execute("SELECT * FROM vessel_logs WHERE vessel_name = ? ORDER BY id DESC", (vessel_name,)).fetchall()
    return render_template_string(BASE_STYLE + """
    <div class="container"><h2>🛥️ Logbook Σκάφους: {{ vessel_name }}</h2><a href="/" class="btn-link btn-home">⮜⮜ Επιστροφή στην Αρχική</a>
        <form method="POST" style="background:#f1f5f9; padding:15px; margin: 15px 0;">
            Ημερομηνία: <input type="date" name="entry_date" value="{{ today }}" required><br>
            Περιστατικό / Αποστολή: <textarea name="incident" required placeholder="Λεπτομέρειες περιπολίας..."></textarea><br>
            Βλάβη / Παρατήρηση: <textarea name="damage" required placeholder="Αναφορά βλαβών..."></textarea><br>
            Ώρες Λειτουργίας Κινητήρων: <input name="working_hours" required placeholder="π.χ. 1250.5 ώρες"><br>
            Επιδιόρθωση / Ενέργειες: <textarea name="repair_report" required placeholder="Ενέργειες τεχνικής αποκατάστασης..."></textarea><br>
            <button class="btn-success">📝 Καταχώρηση Ημερολογίου Σκάφους</button>
        </form>
        <ul>{% for e in entries %}
            <li style="margin-bottom: 12px; background:#fff; padding:12px; border:1px solid #ddd; color:#333;">
                <b>[{{ e['entry_date']}}] {{ e['author'] }}</b><br>
                <b>Περιστατικό:</b> {{ e['incident'] }}<br>
                <b>Βλάβη:</b> {{ e['damage'] }}<br>
                <b>Ώρες:</b> {{ e['working_hours'] }}<br>
                <b>Επιδιόρθωση:</b> {{ e['repair_report'] }}
                {% if role == 'Admin' %}<form method="POST" action="/delete_vessel_message"><input type="hidden" name="entry_id" value="{{ e['id'] }}"><input type="hidden" name="vessel_name" value="{{ vessel_name }}"><button class="btn-danger" style="font-size:10px; margin-top:5px;">🗑️ Διαγραφή</button></form>{% endif %}
            </li>{% endfor %}
        </ul>
    </div>
    """, vessel_name=vessel_name, entries=entries, today=datetime.now().strftime('%Y-%m-%d'), role=user['role'])

@app.route('/delete_vessel_message', methods=['POST'])
def delete_vessel_message():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        conn.cursor().execute("DELETE FROM vessel_logs WHERE id = ?", (request.form.get('entry_id'),))
        conn.commit()
    sync_database_to_supabase()
    return redirect(url_for('vessel_log', vessel_name=request.form.get('vessel_name')))

@app.route('/library', methods=['GET', 'POST'])
def library():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT name, role FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if request.method == 'POST' and user['role'] == 'Admin':
            file = request.files.get('file')
            if file and file.filename != '':
                fname = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
                lpath = os.path.join(UPLOAD_FOLDER, fname)
                file.save(lpath)
                clink = upload_file_to_supabase(lpath, fname)
                cursor.execute("INSERT INTO library (title, category, filename, mega_link, uploader, timestamp) VALUES (?, ?, ?, ?, ?, ?)", 
                               (request.form.get('title'), request.form.get('category'), fname, clink, user['name'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                conn.commit(); sync_database_to_supabase()
        items = cursor.execute("SELECT * FROM library ORDER BY id DESC").fetchall()
    return render_template_string(BASE_STYLE + """
    <div class="container"><h2>📚 Ψηφιακή Βιβλιοθήκη & Νομοθεσία</h2><a href="/" class="btn-link btn-home">⮜⮜ Επιστροφή στην Αρχική</a>
        {% if role == 'Admin' %}
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; margin:15px 0; color:#333;">
            Τίτλος Εγγράφου: <input name="title" required><br>
            Κατηγορία: <select name="category"><option>Νομοθεσία</option><option>Εγκύκλιος</option><option>Εγχειρίδιο</option><option>Άλλο</option></select><br>
            Αρχείο PDF/Doc: <input type="file" name="file" required><br><br>
            <button class="btn-success">📤 Μεταφόρτωση στη Βιβλιοθήκη</button>
        </form>
        {% endif %}
        <table><tr><th>Τίτλος</th><th>Κατηγορία</th><th>Ανάρτηση</th><th>Λήψη</th>{% if role == 'Admin' %}<th>Διαγραφή</th>{% endif %}</tr>
        {% for it in items %}
        <tr><td><b>{{ it['title'] }}</b></td><td>{{ it['category'] }}</td><td>{{ it['uploader'] }}</td>
            <td><a href="{{ it['mega_link'] }}" target="_blank" class="btn-link" style="font-size:11px;">📥 Λήψη</a></td>
            {% if role == 'Admin' %}<td><form method="POST" action="/delete_library_item"><input type="hidden" name="item_id" value="{{ it['id'] }}"><button class="btn-danger" style="font-size:10px;">🗑️ Διαγραφή</button></form></td>{% endif %}
        </tr>
        {% endfor %}</table>
    </div>
    """, items=items, role=user['role'])

@app.route('/delete_library_item', methods=['POST'])
def delete_library_item():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT filename FROM library WHERE id = ?", (request.form.get('item_id'),)).fetchone()
        if row and row['filename']: delete_file_from_supabase(row['filename'])
        cursor.execute("DELETE FROM library WHERE id = ?", (request.form.get('item_id'),))
        conn.commit()
    sync_database_to_supabase()
    return redirect(url_for('library'))

@app.route('/roster', methods=['GET', 'POST'])
def roster():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT id, name, role FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        ym = request.args.get('ym', datetime.now().strftime('%Y-%m'))
        if request.method == 'POST':
            act = request.form.get('action')
            if act == 'save':
                for d in range(1, 32):
                    sym = request.form.get(f'day_{d}', '').strip().upper()
                    if sym in ROSTER_SYMBOLS:
                        cursor.execute("INSERT INTO roster_entries (user_id, year_month, day, symbol) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, year_month, day) DO UPDATE SET symbol = ?", (user['id'], ym, d, sym, sym))
                conn.commit(); sync_database_to_supabase()
            elif act == 'email' and user['role'] == 'Admin':
                wb = openpyxl.Workbook(); ws = wb.active; ws.append(['Ονοματεπώνυμο'] + [str(d) for d in range(1, 32)])
                ul = cursor.execute("SELECT id, name FROM users WHERE status = 'Approved'").fetchall()
                rd = {u['id']: {d: '' for d in range(1, 32)} for u in ul}
                for uid, d, s in cursor.execute("SELECT user_id, day, symbol FROM roster_entries WHERE year_month = ?", (ym,)).fetchall():
                    if uid in rd: rd[uid][d] = s
                for u in ul: ws.append([u['name']] + [rd[u['id']][d] for d in range(1, 32)])
                io = BytesIO(); wb.save(io); io.seek(0)
                for adm in cursor.execute("SELECT email FROM users WHERE role = 'Admin' AND email != ''").fetchall():
                    send_real_email(adm['email'], f"Roster {ym}", "Επισυνάπτεται το μηνιαίο Roster βαρδιών.", io, f"Roster_{ym}.xlsx"); io.seek(0)
                flash("Το Roster στάλθηκε επιτυχώς!")
        users_l = cursor.execute("SELECT id, name FROM users WHERE status = 'Approved'").fetchall()
        rows = cursor.execute("SELECT user_id, day, symbol FROM roster_entries WHERE year_month = ?", (ym,)).fetchall()
        r_data = {u[0]: {d: '' for d in range(1, 32)} for u in users_l}
        for uid, d, s in rows:
            if uid in r_data: r_data[uid][d] = s
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width:100%;"><h2>📋 Μηνιαίο Roster Βάρδιας</h2><a href="/" class="btn-link btn-home">⮜⮜ Επιστροφή στην Αρχική</a>
        <form method="GET" style="margin:10px 0;"><input name="ym" value="{{ ym }}" style="width:150px; display:inline;"><button>🔍 Προβολή Μηνός</button></form>
        {% if role == 'Admin' %}<form method="POST"><input type="hidden" name="action" value="email"><input type="hidden" name="ym" value="{{ ym }}"><button class="btn-success">📧 Αποστολή Excel σε Admins</button></form>{% endif %}
        <form method="POST"><input type="hidden" name="action" value="save">
        <div style="overflow-x:auto;">
        <table><tr><th>Προσωπικό</th>{% for d in range(1, 32) %}<th>{{ d }}</th>{% endfor %}</tr>
        <tr><td><b>{{ user_name }} (Εσείς)</b></td>{% for d in range(1, 32) %}<td><select name="day_{{ d }}" style="width:55px; padding:2px;"><option value=""></option>{% for s in symbols[1:] %}<option value="{{ s }}" {% if my_row.get(d)==s %}selected{% endif %}>{{ s }}</option>{% endfor %}</select></td>{% endfor %}</tr>
        </table></div><button class="btn-success" style="margin-top:10px;">💾 Αποθήκευση Roster</button></form>
    </div>
    """, ym=ym, my_row=r_data.get(user['id'], {}), symbols=ROSTER_SYMBOLS, role=user['role'], user_name=user['name'])

def get_super_admin_id():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE role = 'Admin' AND status = 'Approved' AND admin_level = 1 ORDER BY id ASC LIMIT 1")
        row = cursor.fetchone()
        if row: return row['id']
        cursor.execute("SELECT id FROM users WHERE role = 'Admin' AND status = 'Approved' ORDER BY admin_level ASC, id ASC LIMIT 1")
        row2 = cursor.fetchone()
        return row2['id'] if row2 else None

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT id, role, admin_level FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if not user or user['role'] != 'Admin': return "Μη εξουσιοδοτημένη πρόσβαση"
        
        current_admin_id = user['id']
        is_super_admin = (current_admin_id == get_super_admin_id() or user['admin_level'] == 1)
        msg = ""

        if request.method == 'POST':
            entered_pass = request.form.get('admin_password')
            if check_password_hash(get_current_admin_password_hash(), entered_pass):
                act = request.form.get('action')
                if act == 'delete_orphan':
                    delete_file_from_supabase(request.form.get('filename'))
                    msg = "Το ορφανό αρχείο διεγράφη."
                elif act == 'rename_channel':
                    o, n = request.form.get('old_name'), request.form.get('new_name')
                    cursor.execute("UPDATE channels SET name = ? WHERE name = ?", (n, o))
                    cursor.execute("UPDATE logbook_entries SET channel_name = ? WHERE channel_name = ?", (n, o))
                    cursor.execute("UPDATE vessel_logs SET vessel_name = ? WHERE vessel_name = ?", (n, o))
                    conn.commit(); sync_database_to_supabase(); msg = "Επιτυχής μετονομασία!"
                elif act == 'toggle_roster':
                    new_st = '1' if not is_roster_active() else '0'
                    cursor.execute("UPDATE settings SET value = ? WHERE key = 'roster_enabled'", (new_st,))
                    conn.commit(); sync_database_to_supabase(); msg = "Η κατάσταση Roster άλλαξε."
                elif act == 'update_webhook':
                    ext_url = request.form.get('external_station_url', '').strip()
                    cursor.execute("INSERT INTO settings (key, value) VALUES ('external_station_url', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (ext_url, ext_url))
                    conn.commit(); sync_database_to_supabase(); msg = "Το URL του εξωτερικού σταθμού ενημερώθηκε."
                elif act == 'approve':
                    uid = request.form.get('user_id')
                    cursor.execute("UPDATE users SET status = 'Approved' WHERE id = ?", (uid,))
                    # Διασφάλιση πρόσβασης στα κανάλια για τον εγκεκριμένο χρήστη
                    all_ch = cursor.execute("SELECT id FROM channels").fetchall()
                    for ch in all_ch:
                        cursor.execute("INSERT OR IGNORE INTO channel_members (channel_id, user_id) VALUES (?, ?)", (ch['id'], uid))
                    conn.commit(); sync_database_to_supabase(); msg = "Ο χρήστης εγκρίθηκε!"
                elif act == 'revoke':
                    uid = request.form.get('user_id')
                    target_u = cursor.execute("SELECT admin_level FROM users WHERE id = ?", (uid,)).fetchone()
                    if target_u and target_u['admin_level'] == 1:
                        msg = "Σφάλμα: Δεν μπορείτε να ανακαλέσετε τον Super Admin!"
                    else:
                        cursor.execute("UPDATE users SET status = 'Pending', role = 'User', admin_level = 0 WHERE id = ?", (uid,))
                        conn.commit(); sync_database_to_supabase(); msg = "Η έγκριση ανακλήθηκε."
                elif act == 'hard_delete_user':
                    uid = request.form.get('user_id')
                    uname = cursor.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()['name']
                    for t in ['library', 'logbook_entries']:
                        col = 'uploader' if t == 'library' else 'author_name'
                        for r in cursor.execute(f"SELECT filename FROM {t} WHERE {col} = ?", (uname,)).fetchall():
                            if r['filename']: delete_file_from_supabase(r['filename'])
                        cursor.execute(f"DELETE FROM {t} WHERE {col} = ?", (uname,))
                    cursor.execute("DELETE FROM users WHERE id = ?", (uid,))
                    conn.commit(); sync_database_to_supabase(); msg = "Ολική διαγραφή χρήστη."
            else: msg = "Λάθος κωδικός διαχειριστή."

        users = cursor.execute("SELECT * FROM users").fetchall()
        channels = cursor.execute("SELECT name FROM channels").fetchall()
        super_admin_id = get_super_admin_id()
        ext_url_row = cursor.execute("SELECT value FROM settings WHERE key = 'external_station_url'").fetchone()
        ext_url = ext_url_row['value'] if ext_url_row else ''
        
        orphan_files = []
        try:
            if supabase:
                all_f = supabase.storage.from_(BUCKET_NAME).list()
                db_f = {r['filename'] for r in cursor.execute("SELECT filename FROM logbook_entries WHERE filename IS NOT NULL").fetchall()}
                db_f.update({r['filename'] for r in cursor.execute("SELECT filename FROM library WHERE filename IS NOT NULL").fetchall()})
                db_f.add(DB_NAME)
                for f in all_f:
                    if isinstance(f, dict) and 'name' in f and f['name'] not in db_f:
                        orphan_files.append({'name': f['name'], 'size': f.get('metadata', {}).get('size', 0)})
        except: pass

        used_mb = get_supabase_storage_usage() / (1024 * 1024)
        usage_percent = (used_mb / 1024) * 100
        
    return render_template_string(BASE_STYLE + """
    <div class="container"><h2>🛠️ Πάνελ Διαχείρισης v7.3</h2><a href="/" class="btn-link btn-home">⮜⮜ Επιστροφή στην Αρχική</a>
        <p style="color:red; font-weight:bold;">{{ msg }}</p>
        <div style="background:#f0fdf4; border:1px solid #22c55e; padding:10px; margin-bottom:20px; font-weight:bold; color:#15803d;">📊 Cloud Storage: {{ "%.1f"|format(usage_percent) }} &#37; ({{ "%.1f"|format(used_mb) }} MB / 1024 MB)</div>
        
        {% if orphan_files %}
        <div style="background:#fef2f2; border:1px solid #ef4444; padding:15px; margin-bottom:20px; color:#333;">
            <h3>🧹 Ορφανά Αρχεία στο Cloud</h3>
            <table><tr><th>Όνομα</th><th>Μέγεθος</th><th>Ενέργεια</th></tr>
            {% for of in orphan_files %}
            <tr><td>{{ of['name'] }}</td><td>{{ "%.1f"|format(of['size']/1024) }} KB</td>
                <td><form method="POST"><input type="hidden" name="action" value="delete_orphan"><input type="hidden" name="filename" value="{{ of['name'] }}"><button class="btn-danger" style="font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Admin:');">🗑️ Διαγραφή</button><input type="hidden" name="admin_password" value=""></form></td>
            </tr>
            {% endfor %}</table>
        </div>
        {% endif %}

        <form method="POST" style="background:#e0f2fe; padding:15px; border:1px solid #bae6fd; border-radius:5px; margin-bottom:15px; color:#333;">
            <input type="hidden" name="action" value="update_webhook">
            <h3>🌐 Ρύθμιση Διασύνδεσης Σταθμών (Inter-Station Webhook)</h3>
            <p style="font-size:12px;">URL απομακρυσμένου σταθμού για το κανάλι Ad-hoc:</p>
            <input type="url" name="external_station_url" value="{{ ext_url }}" placeholder="https://other-station.onrender.com/api/webhook/adhoc"><br>
            Κωδικός Admin: <input type="password" name="admin_password" required><br>
            <button class="btn-success">💾 Αποθήκευση Webhook URL</button>
        </form>

        <form method="POST" style="background:#f1f5f9; padding:15px; margin-bottom:15px; color:#333;">
            <h3>🔑 Κατάσταση Roster Βάρδιας</h3>
            <p>Roster: <b>{% if roster_active %}ΕΝΕΡΓΟ{% else %}ΑΝΕΝΕΡΓΟ{% endif %}</b></p>
            Κωδικός Admin: <input type="password" name="admin_password" required style="width:200px;"><br>
            <button type="submit" name="action" value="toggle_roster" class="{% if roster_active %}btn-danger{% else %}btn-success{% endif %}">{% if roster_active %}🔒 Απενεργοποίηση Roster{% else %}🔓 Ενεργοποίηση Roster{% endif %}</button>
        </form>

        <h3>✏️ Μετονομασία Καναλιού / Σκάφους</h3>
        <form method="POST" style="background:#f1f5f9; padding:15px; color:#333;">
            <input type="hidden" name="action" value="rename_channel">
            Επιλογή: <select name="old_name">{% for c in channels %}<option value="{{ c['name'] }}">{{ c['name'] }}</option>{% endfor %}</select><br>
            Νέο Όνομα: <input name="new_name" required><br>
            Κωδικός Admin: <input type="password" name="admin_password" required><br>
            <button class="btn-warning">✏️ Μετονομασία</button>
        </form>

        <hr><h3>👥 Διαχείριση Χρηστών & Εγκρίσεων</h3>
        <table><tr><th>Όνομα</th><th>Τηλέφωνο</th><th>Κατάσταση</th><th>Ρόλος</th><th>Ενέργειες</th></tr>
        {% for u in users %}
        <tr><td>{{ u['name'] }} {% if u['id'] == super_admin_id %}👑{% endif %}</td><td>{{ u['phone'] }}</td><td>{{ u['status'] }}</td><td>{{ u['role'] }}</td>
            <td>
                <form method="POST" style="display:inline;">
                    <input type="hidden" name="user_id" value="{{ u['id'] }}">
                    {% if u['status'] == 'Pending' %}
                    <button type="submit" name="action" value="approve" class="btn-success" style="font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Admin:');">✅ Έγκριση</button>
                    {% else %}
                    <button type="submit" name="action" value="revoke" class="btn-warning" style="font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Admin:');">🔄 Ανάκληση</button>
                    {% endif %}
                    <input type="hidden" name="admin_password" value="">
                </form>
                <form method="POST" style="display:inline;" onsubmit="return confirm('Προσοχή: Ολική διαγραφή χρήστη;');">
                    <input type="hidden" name="action" value="hard_delete_user"><input type="hidden" name="user_id" value="{{ u['id'] }}"><button class="btn-danger" style="font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Admin:');">🗑️ Διαγραφή</button><input type="hidden" name="admin_password" value="">
                </form>
            </td>
        </tr>
        {% endfor %}</table>
    </div>
    """, msg=msg, users=users, channels=channels, roster_active=is_roster_active(), super_admin_id=super_admin_id, is_super_admin=is_super_admin, ext_url=ext_url, used_mb=used_mb, usage_percent=usage_percent, orphan_files=orphan_files)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error_msg = ""
    if request.method == 'POST':
        phone, password = request.form.get('phone'), request.form.get('password', '')
        with get_db_connection() as conn:
            user = conn.cursor().execute("SELECT phone, password, email FROM users WHERE phone = ?", (phone,)).fetchone()
        if user:
            if not user['password']: session['user_phone'] = phone; return redirect(url_for('set_password'))
            if check_password_hash(user['password'], password): session['user_phone'] = phone; return redirect(url_for('index'))
            else: error_msg = "Λάθος κωδικός πρόσβασης."
        else: error_msg = "Ο αριθμός τηλεφώνου δεν βρέθηκε."
    return render_template_string(BASE_STYLE + "<div class='container' style='max-width:400px; margin-top:50px; color:#333;'><h2>🔐 Σύνδεση Συστήματος</h2><p style='color:red;'>{{ error_msg }}</p><form method='POST'>Τηλέφωνο: <input name='phone' required><br>Κωδικός: <input type='password' name='password'><br><br><button style='width:100%;'>🔑 Είσοδος</button></form><div style='margin-top:15px; text-align:center;'><a href='/register' class='btn-link' style='width:100%; box-sizing:border-box;'>📝 Εγγραφή Νέου Χρήστη</a></div></div>", error_msg=error_msg)

@app.route('/set_password', methods=['GET', 'POST'])
def set_password():
    if 'user_phone' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        if request.form.get('new_password') == request.form.get('confirm_password') and request.form.get('email'):
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET password = ?, email = ? WHERE phone = ?", (generate_password_hash(request.form.get('new_password')), request.form.get('email').strip(), session['user_phone']))
                
                # Αυτόματη σύνδεση Super Admin σε όλα τα κανάλια κατά το setup
                u_row = cursor.execute("SELECT id FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
                if u_row:
                    all_ch = cursor.execute("SELECT id FROM channels").fetchall()
                    for ch in all_ch:
                        cursor.execute("INSERT OR IGNORE INTO channel_members (channel_id, user_id) VALUES (?, ?)", (ch['id'], u_row['id']))
                conn.commit()
            sync_database_to_supabase()
            return redirect(url_for('index'))
    return render_template_string(BASE_STYLE + "<div class='container' style='max-width:400px; margin-top:50px; color:#333;'><h2>🔑 Ορισμός Κωδικού & Email</h2><form method='POST'>Email επικοινωνίας: <input type='email' name='email' required><br>Νέος Κωδικός: <input type='password' name='new_password' required><br>Επιβεβαίωση: <input type='password' name='confirm_password' required><br><br><button style='width:100%;'>💾 Αποθήκευση</button></form></div>")

@app.route('/register', methods=['GET', 'POST'])
def register():
    msg = ""
    if request.method == 'POST':
        phone, name, station, email = request.form.get('phone'), request.form.get('name'), request.form.get('station'), request.form.get('email')
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                uc = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                role, status, lvl = ('Admin', 'Approved', 1) if uc == 0 else ('User', 'Pending', 0)
                cursor.execute("INSERT INTO users (phone, name, station, status, role, email, admin_level) VALUES (?, ?, ?, ?, ?, ?, ?)", (phone, name, station, status, role, email, lvl))
                
                if uc == 0:
                    new_uid = cursor.lastrowid
                    all_ch = cursor.execute("SELECT id FROM channels").fetchall()
                    for ch in all_ch:
                        cursor.execute("INSERT OR IGNORE INTO channel_members (channel_id, user_id) VALUES (?, ?)", (ch['id'], new_uid))
                
                conn.commit()
            sync_database_to_supabase()
            session['user_phone'] = phone
            return redirect(url_for('set_password'))
        except sqlite3.IntegrityError: msg = 'Ο αριθμός τηλεφώνου υπάρχει ήδη.'
    return render_template_string(BASE_STYLE + "<div class='container' style='max-width:400px; margin-top:50px; color:#333;'><h2>📝 Εγγραφή Προσωπικού</h2><p style='color:red;'>{{ msg }}</p><form method='POST'>Τηλέφωνο: <input name='phone' required><br>Ονοματεπώνυμο: <input name='name' required><br>Σταθμός / Υπηρεσία: <input name='station' required><br>Email: <input type='email' name='email' required><br><br><button style='width:100%;' class='btn-success'>🚀 Συνέχεια</button></form><div style='margin-top:15px; text-align:center;'><a href='/' class='btn-link btn-home' style='width:100%; box-sizing:border-box;'>⮜⮜ Επιστροφή στην Αρχική</a></div></div>", msg=msg)

@app.route('/user_settings', methods=['GET', 'POST'])
def user_settings():
    if 'user_phone' not in session: return redirect(url_for('login'))
    msg = ""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user_row = cursor.execute("SELECT id, name, email FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if not user_row: return redirect(url_for('login'))
        user_id, current_email = user_row['id'], user_row['email']

        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'update_profile':
                new_email = request.form.get('email', '').strip()
                curr_pwd, new_pwd, conf_pwd = request.form.get('current_password', ''), request.form.get('new_password', ''), request.form.get('confirm_password', '')
                db_pwd = cursor.execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()[0]

                if new_pwd:
                    if not check_password_hash(db_pwd, curr_pwd): msg = "Λάθος τρέχων κωδικός πρόσβασης."
                    elif new_pwd != conf_pwd: msg = "Οι νέοι κωδικοί δεν ταιριάζουν."
                    else:
                        cursor.execute("UPDATE users SET email = ?, password = ? WHERE id = ?", (new_email, generate_password_hash(new_pwd), user_id))
                        conn.commit(); sync_database_to_supabase(); msg = "Επιτυχής ενημέρωση προφίλ και κωδικού."
                        current_email = new_email
                else:
                    cursor.execute("UPDATE users SET email = ? WHERE id = ?", (new_email, user_id))
                    conn.commit(); sync_database_to_supabase(); msg = "Το email ενημερώθηκε."
                    current_email = new_email

            elif action == 'update_notifications':
                channels_acc = cursor.execute("SELECT c.id FROM channels c JOIN channel_members cm ON c.id = cm.channel_id WHERE cm.user_id = ? UNION SELECT id FROM channels WHERE name = 'Συμβάντα'", (user_id,)).fetchall()
                for ch in channels_acc:
                    ch_id = ch[0]
                    en = 1 if request.form.get(f'notif_{ch_id}') == 'on' else 0
                    cursor.execute("INSERT INTO user_notification_settings (user_id, channel_id, enabled) VALUES (?, ?, ?) ON CONFLICT(user_id, channel_id) DO UPDATE SET enabled = ?", (user_id, ch_id, en, en))
                conn.commit(); sync_database_to_supabase(); msg = "Οι ρυθμίσεις ειδοποιήσεων αποθηκεύτηκαν."

        channels_accessible = cursor.execute("SELECT c.id, c.name FROM channels c JOIN channel_members cm ON c.id = cm.channel_id WHERE cm.user_id = ? UNION SELECT id, name FROM channels WHERE name = 'Συμβάντα'", (user_id,)).fetchall()
        channel_notif_prefs = {}
        for ch in channels_accessible:
            ns = cursor.execute("SELECT enabled FROM user_notification_settings WHERE user_id = ? AND channel_id = ?", (user_id, ch[0])).fetchone()
            channel_notif_prefs[ch[0]] = ns['enabled'] if ns else 1

    return render_template_string(BASE_STYLE + """
    <div class="container" style="color:#333;">
        <h2>⚙️ Ρυθμίσεις Χρήστη</h2>
        <a href="/" class="btn-link btn-home">⮜⮜ Επιστροφή στην Αρχική</a>
        <p style="color:red; font-weight:bold;">{{ msg }}</p>

        <form method="POST" style="background:#f1f5f9; padding:15px; margin-bottom:20px;">
            <input type="hidden" name="action" value="update_profile">
            <h3>👤 Στοιχεία & Αλλαγή Κωδικού</h3>
            Email: <input type="email" name="email" value="{{ current_email }}" required><br><br>
            Τρέχων Κωδικός: <input type="password" name="current_password"><br>
            Νέος Κωδικός: <input type="password" name="new_password"><br>
            Επιβεβαίωση Νέου: <input type="password" name="confirm_password"><br><br>
            <button class="btn-success">💾 Αποθήκευση Αλλαγών</button>
        </form>

        <form method="POST" style="background:#f1f5f9; padding:15px;">
            <input type="hidden" name="action" value="update_notifications">
            <h3>🔔 Προτιμήσεις Ειδοποιήσεων</h3>
            <ul style="list-style:none; padding:0;">
                {% for ch in channels_accessible %}
                <li style="margin-bottom:8px; background:#fff; padding:8px; border:1px solid #ddd;">
                    <label><input type="checkbox" name="notif_{{ ch[0] }}" {% if channel_notif_prefs[ch[0]] == 1 %}checked{% endif %} style="width:auto;"> <b>{{ ch[1] }}</b></label>
                </li>
                {% endfor %}
            </ul>
            <button class="btn-success">💾 Αποθήκευση Ειδοποιήσεων</button>
        </form>
    </div>
    """, current_email=current_email, channels_accessible=channels_accessible, channel_notif_prefs=channel_notif_prefs, msg=msg)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)