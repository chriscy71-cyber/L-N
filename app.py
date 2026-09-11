# =====================================================================
# 1. ΑΡΧΙΚΟΠΟΙΗΣΗ, ΒΑΣΗ ΔΕΔΟΜΕΝΩΝ & ΧΑΡΤΗΣ ΜΕΣΟΓΕΙΟΥ (v7.4 - 5/0 VERIFIED)
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
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'xls', 'xlsx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
# 2. ΔΙΑΧΕΙΡΙΣΗ ΑΡΧΕΙΩΝ, ΣΥΓΧΡΟΝΙΣΜΟΣ, WEBHOOKS & STYLE (v7.4)
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
    <span>⚓ Σταθμοί Λάρνακας version 7.4 (5/0 Verified Core)</span>
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
    """, name=user['name'], role=user['role'], standard_channels=standard_channels, vessel_channels=vessel_channels, roster_active=is_roster_active())

@app.route('/map')
def map_view():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        markers = cursor.execute("SELECT id, lat, lng, incident_type, description, author, timestamp FROM map_markers ORDER BY id DESC").fetchall()
    
    return render_template_string(BASE_STYLE + """
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <div class="container" style="max-width:1300px;">
        <h2>🗺️ Επιχειρησιακός Χάρτης Μεσογείου (Mediterranean Operational Map)</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link btn-home">🏠 Αρχική Σελίδα</a>
            <a href="/logout" class="btn-link btn-danger">🚪 Αποσύνδεση</a>
        </div>
        <hr>
        <div style="display:flex; gap:20px; flex-wrap:wrap;">
            <div style="flex:2; min-width:300px;">
                <div id="map" style="height:600px; border-radius:8px; border:2px solid #ccc;"></div>
            </div>
            <div style="flex:1; min-width:300px; background:#f8fafc; padding:15px; border-radius:8px; border:1px solid #cbd5e1;">
                <h3>➕ Καταχώρηση Νέου Συμβάντος Χάρτη</h3>
                <form method="POST" action="/map/add">
                    <label><b>Τύπος Συμβάντος:</b></label>
                    <select name="incident_type" required>
                        {% for cat in incident_categories %}
                            <option value="{{ cat }}">{{ cat }}</option>
                        {% endfor %}
                    </select>
                    <label><b>Γεωγραφικό Πλάτος (Lat):</b></label>
                    <input type="text" id="lat" name="lat" placeholder="π.χ. 34.9123" required>
                    <label><b>Γεωγραφικό Μήκος (Lng):</b></label>
                    <input type="text" id="lng" name="lng" placeholder="π.χ. 33.6342" required>
                    <label><b>Περιγραφή & Στοιχεία:</b></label>
                    <textarea name="description" rows="3" placeholder="Λεπτομέρειες περιστατικού..." required></textarea>
                    <button type="submit" class="btn-link btn-success" style="width:100%; margin-top:10px;">💾 Αποθήκευση & Σήμανση στον Χάρτη</button>
                </form>
                <p style="font-size:11px; color:#666; margin-top:10px;">💡 <i>Κάντε κλικ οπουδήποτε στον χάρτη για αυτόματη συμπλήρωση συντεταγμένων Lat/Lng.</i></p>
            </div>
        </div>
    </div>
    <script>
        var map = L.map('map').setView([34.9167, 33.6333], 9);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 18,
            attribution: '© OpenStreetMap contributors'
        }).addTo(map);

        var markersData = [
            {% for m in markers %}
                {id: {{ m['id'] }}, lat: {{ m['lat'] }}, lng: {{ m['lng'] }}, type: "{{ m['incident_type'] }}", desc: "{{ m['description'] | escape }}", author: "{{ m['author'] }}", time: "{{ m['timestamp'] }}"},
            {% endfor %}
        ];

        markersData.forEach(function(item) {
            var marker = L.marker([item.lat, item.lng]).addTo(map);
            marker.bindPopup("<b>" + item.type + "</b><br>" + item.desc + "<br><small>Από: " + item.author + " (" + item.time + ")</small>");
        });

        map.on('click', function(e) {
            document.getElementById('lat').value = e.latlng.lat.toFixed(5);
            document.getElementById('lng').value = e.latlng.lng.toFixed(5);
        });
    </script>
    """, incident_categories=INCIDENT_CATEGORIES, markers=markers)

@app.route('/map/add', methods=['POST'])
def map_add():
    if 'user_phone' not in session: return redirect(url_for('login'))
    lat = request.form.get('lat')
    lng = request.form.get('lng')
    incident_type = request.form.get('incident_type')
    description = request.form.get('description')
    author = session.get('user_name', 'Χρήστης')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except ValueError:
        flash("Μη έγκυρες συντεταγμένες.")
        return redirect(url_for('map_view'))

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO map_markers (lat, lng, incident_type, description, author, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                       (lat_f, lng_f, incident_type, description, author, timestamp))
        conn.commit()
    sync_database_to_supabase()
    return redirect(url_for('map_view'))

# =====================================================================
# 4. LOGBOOKS & VESSEL LOGS
# =====================================================================
@app.route('/logbook/<channel_name>', methods=['GET', 'POST'])
def logbook(channel_name):
    if 'user_phone' not in session: return redirect(url_for('login'))
    clean_old_channel_entries()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        ch_exists = cursor.execute("SELECT id FROM channels WHERE name = ?", (channel_name,)).fetchone()
        if not ch_exists: return "Το κανάλι δεν βρέθηκε", 404

    if request.method == 'POST':
        content = request.form.get('content')
        incident_category = request.form.get('incident_category', 'Γενικό')
        file = request.files.get('file')
        author_name = session.get('user_name', 'Χρήστης')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        filename = None
        mega_link = None
        
        if file and file.filename != '':
            if allowed_file(file.filename):
                filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(file_path)
                mega_link = upload_file_to_supabase(file_path, filename)
                try: os.remove(file_path)
                except: pass
            else:
                flash('Μη έγκυρος τύπος αρχείου. Επιτρέπονται μόνο PDF, DOC, DOCX, XLS, XLSX.')
                return redirect(url_for('logbook', channel_name=channel_name))

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO logbook_entries (channel_name, author_name, incident_category, content, filename, mega_link, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (channel_name, author_name, incident_category, content, filename, mega_link, timestamp))
            conn.commit()
        sync_database_to_supabase()

        if channel_name == 'Ad-hoc':
            send_adhoc_to_external_station(author_name, content)

        return redirect(url_for('logbook', channel_name=channel_name))

    with get_db_connection() as conn:
        cursor = conn.cursor()
        entries = cursor.execute("SELECT * FROM logbook_entries WHERE channel_name = ? ORDER BY id DESC", (channel_name,)).fetchall()

    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>💬 Κανάλι / Logbook: {{ channel_name }}</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link btn-home">🏠 Αρχική Σελίδα</a>
            <a href="/logout" class="btn-link btn-danger">🚪 Αποσύνδεση</a>
        </div>
        <hr>
        <div class="viber-chat-container">
            {% if entries %}
                {% for entry in entries %}
                    <div class="chat-bubble">
                        <div class="chat-author">
                            {{ entry['author_name'] }} 
                            {% if entry['incident_category'] and entry['incident_category'] != 'Γενικό' %}
                                <span style="background:#e0f2fe; color:#0369a1; padding:2px 6px; border-radius:4px; font-size:11px; margin-left:5px;">{{ entry['incident_category'] }}</span>
                            {% endif %}
                            <span class="chat-time">{{ entry['timestamp'] }}</span>
                        </div>
                        <div class="chat-text">{{ entry['content'] | replace('\\n', '<br>') | safe }}</div>
                        {% if entry['mega_link'] %}
                            <div style="margin-top:8px;">
                                <a href="{{ entry['mega_link'] }}" target="_blank" class="btn-link" style="padding:4px 8px; font-size:11px;">👁️ Προβολή Εγγράφου</a>
                            </div>
                        {% endif %}
                    </div>
                {% endfor %}
            {% else %}
                <p style="text-align:center; color:#666;">Δεν υπάρχουν καταχωρήσεις σε αυτό το κανάλι.</p>
            {% endif %}
        </div>
        
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; border-radius:8px;">
            {% if channel_name == 'Συμβάντα' %}
                <label><b>Κατηγορία Συμβάντος:</b></label>
                <select name="incident_category">
                    {% for cat in incident_categories %}
                        <option value="{{ cat }}">{{ cat }}</option>
                    {% endfor %}
                </select>
            {% endif %}
            <label><b>Μήνυμα / Καταχώρηση:</b></label>
            <textarea name="content" rows="3" placeholder="Γράψτε την αναφορά ή το μήνυμά σας..." required></textarea>
            <label style="margin-top:8px; display:block;"><b>Συνημμένο Αρχείο (Word, Excel, PDF - max 1):</b></label>
            <input type="file" name="file" accept=".pdf,.doc,.docx,.xls,.xlsx">
            <button type="submit" class="btn-link btn-success" style="margin-top:10px;">📤 Αποστολή Καταχώρησης</button>
        </form>
    </div>
    """, channel_name=channel_name, entries=entries, incident_categories=INCIDENT_CATEGORIES)

@app.route('/vessel_log/<vessel_name>', methods=['GET', 'POST'])
def vessel_log(vessel_name):
    if 'user_phone' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        entry_date = request.form.get('entry_date')
        incident = request.form.get('incident')
        damage = request.form.get('damage')
        working_hours = request.form.get('working_hours')
        repair_report = request.form.get('repair_report')
        author = session.get('user_name', 'Χρήστης')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO vessel_logs (vessel_name, entry_date, incident, damage, working_hours, repair_report, author, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (vessel_name, entry_date, incident, damage, working_hours, repair_report, author, timestamp))
            conn.commit()
        sync_database_to_supabase()
        return redirect(url_for('vessel_log', vessel_name=vessel_name))

    with get_db_connection() as conn:
        cursor = conn.cursor()
        logs = cursor.execute("SELECT * FROM vessel_logs WHERE vessel_name = ? ORDER BY id DESC", (vessel_name,)).fetchall()

    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>🛥️ Ημερολόγιο & Τεχνικό Logbook: {{ vessel_name }}</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link btn-home">🏠 Αρχική Σελίδα</a>
            <a href="/logout" class="btn-link btn-danger">🚪 Αποσύνδεση</a>
        </div>
        <hr>
        <h3>➕ Νέα Καταχώρηση Σκάφους</h3>
        <form method="POST" style="background:#f1f5f9; padding:15px; border-radius:8px; margin-bottom:20px;">
            <div style="display:flex; gap:10px; flex-wrap:wrap;">
                <div style="flex:1; min-width:200px;">
                    <label><b>Ημερομηνία:</b></label>
                    <input type="date" name="entry_date" required value="{{ today }}">
                </div>
                <div style="flex:1; min-width:200px;">
                    <label><b>Ώρες Λειτουργίας (Engine Hours):</b></label>
                    <input type="text" name="working_hours" placeholder="π.χ. 1250 hrs">
                </div>
            </div>
            <label><b>Περιστατικό / Αποστολή:</b></label>
            <textarea name="incident" rows="2" placeholder="Αναφέρετε περιπολία ή συμβάν..."></textarea>
            <label><b>Βλάβες / Ζημιές:</b></label>
            <textarea name="damage" rows="2" placeholder="Τυχόν μηχανικές ή υλικές βλάβες..."></textarea>
            <label><b>Αναφορά Επισκευής / Συντήρησης:</b></label>
            <textarea name="repair_report" rows="2" placeholder="Εργασίες τεχνικής αποκατάστασης..."></textarea>
            <button type="submit" class="btn-link btn-success" style="margin-top:10px;">💾 Καταχώρηση Ημερολογίου Σκάφους</button>
        </form>

        <h3>📜 Ιστορικό Καταχωρήσεων Σκάφους</h3>
        <table>
            <tr>
                <th>Ημερομηνία</th>
                <th>Ώρες</th>
                <th>Περιστατικό</th>
                <th>Βλάβες</th>
                <th>Συντήρηση</th>
                <th>Συντάκτης</th>
            </tr>
            {% for l in logs %}
            <tr>
                <td>{{ l['entry_date'] }}</td>
                <td><b>{{ l['working_hours'] }}</b></td>
                <td>{{ l['incident'] }}</td>
                <td><span style="color:#dc2626;">{{ l['damage'] }}</span></td>
                <td>{{ l['repair_report'] }}</td>
                <td><small>{{ l['author'] }}<br>{{ l['timestamp'] }}</small></td>
            </tr>
            {% else %}
            <tr><td colspan="6">Δεν υπάρχουν καταχωρήσεις για το σκάφος αυτό.</td></tr>
            {% endfor %}
        </table>
    </div>
    """, vessel_name=vessel_name, logs=logs, today=datetime.now().strftime('%Y-%m-%d'))

# =====================================================================
# 5. ΨΨΦΙΑΚΗ ΒΙΒΛΙΟΘΗΚΗ & ROSTER
# =====================================================================
@app.route('/library', methods=['GET', 'POST'])
def library():
    if 'user_phone' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        title = request.form.get('title')
        category = request.form.get('category')
        file = request.files.get('file')
        uploader = session.get('user_name', 'Χρήστης')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if file and file.filename != '':
            if allowed_file(file.filename):
                filename = f"lib_{uuid.uuid4()}_{secure_filename(file.filename)}"
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(file_path)
                mega_link = upload_file_to_supabase(file_path, filename)
                try: os.remove(file_path)
                except: pass

                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO library (title, category, filename, mega_link, uploader, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                                   (title, category, filename, mega_link, uploader, timestamp))
                    conn.commit()
                sync_database_to_supabase()
                flash("Το αρχείο ανέβηκε επιτυχώς στη βιβλιοθήκη.")
            else:
                flash("Μη έγκυρος τύπος αρχείου. Επιτρέπονται μόνο PDF, DOC, DOCX, XLS, XLSX.")
        return redirect(url_for('library'))

    with get_db_connection() as conn:
        cursor = conn.cursor()
        docs = cursor.execute("SELECT * FROM library ORDER BY id DESC").fetchall()

    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>📚 Ψηφιακή Βιβλιοθήκη & Αρχείο Εγγράφων</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link btn-home">🏠 Αρχική Σελίδα</a>
            <a href="/logout" class="btn-link btn-danger">🚪 Αποσύνδεση</a>
        </div>
        <hr>
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; border-radius:8px; margin-bottom:20px;">
            <h3>📤 Ανάρτηση Νέου Εγγράφου</h3>
            <div style="display:flex; gap:10px; flex-wrap:wrap;">
                <div style="flex:2; min-width:200px;">
                    <label><b>Τίτλος Εγγράφου:</b></label>
                    <input type="text" name="title" placeholder="π.χ. Εγκύκλιος Λιμενικής Αστυνομίας 2026" required>
                </div>
                <div style="flex:1; min-width:200px;">
                    <label><b>Κατηγορία:</b></label>
                    <select name="category" required>
                        <option value="Νομοθεσία">Νομοθεσία / Κανονισμοί</option>
                        <option value="Εγκύκλιοι">Εγκύκλιοι Διαταγές</option>
                        <option value="Εγχειρίδια">Εγχειρίδια & SOPs</option>
                        <option value="Έντυπα">Υπηρεσιακά Έντυπα</option>
                    </select>
                </div>
            </div>
            <label style="margin-top:8px; display:block;"><b>Αρχείο (Word, Excel, PDF):</b></label>
            <input type="file" name="file" accept=".pdf,.doc,.docx,.xls,.xlsx" required>
            <button type="submit" class="btn-link btn-success" style="margin-top:10px;">💾 Μεταφόρτωση στη Βιβλιοθήκη</button>
        </form>

        <h3>📂 Διαθέσιμα Έγγραφα</h3>
        <table>
            <tr>
                <th>Τίτλος</th>
                <th>Κατηγορία</th>
                <th>Αναρτήθηκε από</th>
                <th>Ημερομηνία</th>
                <th>Ενέργειες</th>
            </tr>
            {% for d in docs %}
            <tr>
                <td style="text-align:left; font-weight:bold;">{{ d['title'] }}</td>
                <td>{{ d['category'] }}</td>
                <td>{{ d['uploader'] }}</td>
                <td><small>{{ d['timestamp'] }}</small></td>
                <td>
                    {% if d['mega_link'] %}
                        <a href="{{ d['mega_link'] }}" target="_blank" class="btn-link" style="padding:5px 10px; font-size:11px;">👁️ Προβολή</a>
                    {% endif %}
                </td>
            </tr>
            {% else %}
            <tr><td colspan="5">Δεν υπάρχουν έγγραφα στη βιβλιοθήκη.</td></tr>
            {% endfor %}
        </table>
    </div>
    """, docs=docs)

@app.route('/roster')
def roster():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user_check = cursor.execute("SELECT role, admin_level FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        is_admin_or_officer = (user_check['role'] == 'Admin' or user_check['admin_level'] >= 1)
        if not is_roster_active() and not is_admin_or_officer:
            return render_template_string(BASE_STYLE + "<div class='container'><h3>Το Roster Βάρδιας είναι ανενεργό από τη Διοίκηση.</h3><p><a href='/' class='btn-link btn-home'>🏠 Επιστροφή</a></p></div>")

    current_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    year, month = map(int, current_month.split('-'))
    import calendar
    num_days = calendar.monthrange(year, month)[1]
    days = list(range(1, num_days + 1))

    with get_db_connection() as conn:
        cursor = conn.cursor()
        users = cursor.execute("SELECT id, name, station FROM users WHERE status = 'Approved' ORDER BY station ASC, name ASC").fetchall()
        entries_raw = cursor.execute("SELECT user_id, day, symbol FROM roster_entries WHERE year_month = ?", (current_month,)).fetchall()
        roster_map = {(e['user_id'], e['day']): e['symbol'] for e in entries_raw}

    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width:1400px; overflow-x:auto;">
        <h2>📋 Roster Βάρδιας Προσωπικού (Μήνας: {{ current_month }})</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link btn-home">🏠 Αρχική Σελίδα</a>
            {% if is_admin_or_officer %}
                <a href="/roster/export?month={{ current_month }}" class="btn-link btn-success">📥 Εξαγωγή & Αποστολή Excel</a>
            {% endif %}
            <a href="/logout" class="btn-link btn-danger">🚪 Αποσύνδεση</a>
        </div>
        <hr>
        <form method="GET" action="/roster" style="margin-bottom:15px; display:flex; gap:10px; align-items:center;">
            <label><b>Επιλογή Μήνα:</b></label>
            <input type="month" name="month" value="{{ current_month }}" style="width:200px;" onchange="this.form.submit()">
        </form>
        
        <table style="font-size:11px;">
            <tr>
                <th style="position:sticky; left:0; background:#f2f2f2; z-index:2;">Ονοματεπώνυμο / Σταθμός</th>
                {% for d in days %}
                    <th>{{ d }}</th>
                {% endfor %}
            </tr>
            {% for u in users %}
            <tr>
                <td style="text-align:left; position:sticky; left:0; background:#fff; font-weight:bold; z-index:1;">
                    {{ u['name'] }} <small style="color:#666;">({{ u['station'] }})</small>
                </td>
                {% for d in days %}
                    <td style="padding:2px;">
                        {% set sym = roster_map.get((u['id'], d), '') %}
                        {% if is_admin_or_officer %}
                            <select onchange="updateRoster({{ u['id'] }}, '{{ current_month }}', {{ d }}, this.value)" style="width:45px; padding:2px; font-size:11px; text-align:center;">
                                <option value=""></option>
                                {% for s in roster_symbols %}
                                    <option value="{{ s }}" {% if sym == s %}selected{% endif %}>{{ s }}</option>
                                {% endfor %}
                            </select>
                        {% else %}
                            <b>{{ sym }}</b>
                        {% endif %}
                    </td>
                {% endfor %}
            </tr>
            {% endfor %}
        </table>
        <p style="margin-top:15px; font-size:12px; color:#555;"><b>Σύμβολα:</b> Μ=Μέρα, Ν=Νύχτα, SL=Άδεια Ασθενείας, Α=Κανονική Άδεια, RD=Repoff, T=Εκπαίδευση, Υ=Υπηρεσία, Π=Περιπολία, Ε=Ειδική, ΑΠ=Απουσία</p>
    </div>
    <script>
        function updateRoster(userId, yearMonth, day, symbol) {
            fetch('/roster/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({user_id: userId, year_month: yearMonth, day: day, symbol: symbol})
            }).then(res => res.json()).then(data => {
                if(!data.success) alert('Σφάλμα ενημέρωσης roster');
            });
        }
    </script>
    """, current_month=current_month, days=days, users=users, roster_map=roster_map, roster_symbols=ROSTER_SYMBOLS, is_admin_or_officer=is_admin_or_officer)

@app.route('/roster/update', methods=['POST'])
def roster_update():
    if 'user_phone' not in session: return jsonify({'success': False})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        u_check = cursor.execute("SELECT role, admin_level FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if not u_check or (u_check['role'] != 'Admin' and u_check['admin_level'] < 1):
            return jsonify({'success': False})

    data = request.get_json()
    user_id = data.get('user_id')
    year_month = data.get('year_month')
    day = data.get('day')
    symbol = data.get('symbol')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO roster_entries (user_id, year_month, day, symbol) VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, year_month, day) DO UPDATE SET symbol = ?
        """, (user_id, year_month, day, symbol, symbol))
        conn.commit()
    sync_database_to_supabase()
    return jsonify({'success': True})

@app.route('/roster/export')
def roster_export():
    if 'user_phone' not in session: return redirect(url_for('login'))
    current_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Roster {current_month}"
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        users = cursor.execute("SELECT id, name, station, email FROM users WHERE status = 'Approved' ORDER BY station ASC").fetchall()
        year, month = map(int, current_month.split('-'))
        import calendar
        num_days = calendar.monthrange(year, month)[1]
        
        header = ["Ονοματεπώνυμο", "Σταθμός"] + list(range(1, num_days + 1))
        ws.append(header)
        
        for u in users:
            row = [u['name'], u['station']]
            for d in range(1, num_days + 1):
                sym = cursor.execute("SELECT symbol FROM roster_entries WHERE user_id = ? AND year_month = ? AND day = ?", (u['id'], current_month, d)).fetchone()
                row.append(sym['symbol'] if sym else '')
            ws.append(row)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for u in users:
            if u['email']:
                send_real_email(u['email'], f"Υπηρεσιακό Roster Βάρδιας - {current_month}", "Επισυνάπτεται το επίσημο roster βάρδιας σε αρχείο Excel.", output, f"Roster_{current_month}.xlsx")
                output.seek(0)

    flash("Το Roster εξήχθη και εστάλη επιτυχώς σε όλους τους χρήστες με email.")
    return redirect(url_for('roster', month=current_month))

# =====================================================================
# 6. AUTHENTICATION, PASSWORD SETUP & USER SETTINGS
# =====================================================================
@app.route('/set_password', methods=['GET', 'POST'])
def set_password():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT password, email FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if user and user['password'] and user['email']: return redirect(url_for('index'))

    if request.method == 'POST':
        password = request.form.get('password')
        email = request.form.get('email')
        if password and email:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET password = ?, email = ? WHERE phone = ?", (password, email, session['user_phone']))
                conn.commit()
            sync_database_to_supabase()
            return redirect(url_for('index'))
        flash("Συμπληρώστε κωδικό και email.")

    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width:500px; margin-top:50px;">
        <h2>🔐 Ορισμός Κωδικού & Email Πρόσβασης</h2>
        <form method="POST">
            <label><b>Νέος Κωδικός Πρόσβασης:</b></label>
            <input type="password" name="password" required>
            <label><b>Υπηρεσιακό Email (για αποστολή ειδοποιήσεων):</b></label>
            <input type="email" name="email" placeholder="name@police.gov.cy" required>
            <button type="submit" class="btn-link btn-success" style="width:100%; margin-top:15px;">💾 Αποθήκευση & Είσοδος</button>
        </form>
    </div>
    """)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '').strip()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            user = cursor.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
            if not user:
                user_count = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                if user_count == 0:
                    role = 'Admin'
                    status = 'Approved'
                    admin_level = 1
                else:
                    admin_phone, _ = get_super_admin_phone_and_id()
                    role = 'Admin' if phone == admin_phone else 'User'
                    status = 'Approved' if role == 'Admin' else 'Pending'
                    admin_level = 1 if role == 'Admin' else 0

                cursor.execute("INSERT INTO users (phone, name, station, role, status, admin_level) VALUES (?, ?, ?, ?, ?, ?)",
                               (phone, 'Διαχειριστής' if role == 'Admin' else 'Νέος Χρήστης', 'Λάρνακα', role, status, admin_level))
                conn.commit()
                user = cursor.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()

        if user['role'] == 'Admin' and password:
            if password == DEFAULT_ADMIN_PASSWORD or check_password_hash(get_current_admin_password_hash(), password):
                session['user_phone'] = user['phone']
                session['user_name'] = user['name']
                return redirect(url_for('set_password') if not user['password'] or not user['email'] else url_for('index'))
            else:
                flash("Λανθασμένος κωδικός διαχειριστή.")
        else:
            session['user_phone'] = user['phone']
            session['user_name'] = user['name']
            return redirect(url_for('set_password') if not user['password'] or not user['email'] else url_for('index'))

    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width:400px; margin-top:80px;">
        <h2 style="text-align:center;">⚓ Πύλη Εισόδου Λιμενικής</h2>
        <form method="POST">
            <label><b>Αριθμός Τηλεφώνου:</b></label>
            <input type="text" name="phone" placeholder="π.χ. 99123456" required>
            <label><b>Κωδικός Πρόσβασης (μόνο για Admin / Υπόλοιποι κενό):</b></label>
            <input type="password" name="password" placeholder="Password">
            <button type="submit" class="btn-link btn-success" style="width:100%; margin-top:15px;">🔐 Είσοδος / Εγγραφή</button>
        </form>
    </div>
    """)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/user_settings', methods=['GET', 'POST'])
def user_settings():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT * FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()

    if request.method == 'POST':
        name = request.form.get('name')
        station = request.form.get('station')
        email = request.form.get('email')
        new_pass = request.form.get('new_password')

        with get_db_connection() as conn:
            cursor = conn.cursor()
            if new_pass:
                cursor.execute("UPDATE users SET name = ?, station = ?, email = ?, password = ? WHERE phone = ?", (name, station, email, new_pass, session['user_phone']))
            else:
                cursor.execute("UPDATE users SET name = ?, station = ?, email = ? WHERE phone = ?", (name, station, email, session['user_phone']))
            conn.commit()
        sync_database_to_supabase()
        session['user_name'] = name
        flash("Οι ρυθμίσεις ενημερώθηκαν επιτυχώς.")
        return redirect(url_for('index'))

    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width:600px;">
        <h2>⚙️ Ρυθμίσεις Χρήστη</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link btn-home">🏠 Αρχική Σελίδα</a>
            <a href="/logout" class="btn-link btn-danger">🚪 Αποσύνδεση</a>
        </div>
        <hr>
        <form method="POST">
            <label><b>Ονοματεπώνυμο:</b></label>
            <input type="text" name="name" value="{{ user['name'] }}" required>
            <label><b>Υπηρεσιακός Σταθμός:</b></label>
            <input type="text" name="station" value="{{ user['station'] }}" required>
            <label><b>Email:</b></label>
            <input type="email" name="email" value="{{ user['email'] }}" required>
            <label><b>Νέος Κωδικός Πρόσβασης (αφήστε κενό αν δεν θέλετε αλλαγή):</b></label>
            <input type="password" name="new_password">
            <button type="submit" class="btn-link btn-success" style="margin-top:15px; width:100%;">💾 Αποθήκευση Αλλαγών</button>
        </form>
    </div>
    """, user=user)

# =====================================================================
# 7. ADMIN PANEL & GUIDE
# =====================================================================
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT role, admin_level FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if not user or (user['role'] != 'Admin' and user['admin_level'] < 1):
            return "Δεν έχετε δικαιώματα πρόσβασης διαχειριστή.", 403

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'approve':
            u_id = request.form.get('user_id')
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET status = 'Approved' WHERE id = ?", (u_id,))
                conn.commit()
            sync_database_to_supabase()
        elif action == 'delete_user':
            u_id = request.form.get('user_id')
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users WHERE id = ?", (u_id,))
                conn.commit()
            sync_database_to_supabase()
        elif action == 'toggle_roster':
            current_st = is_roster_active()
            new_st = '0' if current_st else '1'
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE settings SET value = ? WHERE key = 'roster_enabled'", (new_st,))
                conn.commit()
            sync_database_to_supabase()
        elif action == 'update_smtp':
            server = request.form.get('smtp_server')
            port = request.form.get('smtp_port')
            user_s = request.form.get('smtp_user')
            pass_s = request.form.get('smtp_pass')
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE settings SET value = ? WHERE key = 'smtp_server'", (server,))
                cursor.execute("UPDATE settings SET value = ? WHERE key = 'smtp_port'", (port,))
                cursor.execute("UPDATE settings SET value = ? WHERE key = 'smtp_user'", (user_s,))
                if pass_s: cursor.execute("UPDATE settings SET value = ? WHERE key = 'smtp_pass'", (pass_s,))
                conn.commit()
            sync_database_to_supabase()
        elif action == 'update_external':
            ext_url = request.form.get('external_station_url')
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE settings SET value = ? WHERE key = 'external_station_url'", (ext_url,))
                conn.commit()
            sync_database_to_supabase()
        elif action == 'add_channel':
            ch_name = request.form.get('channel_name').strip()
            if ch_name:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("INSERT OR IGNORE INTO channels (name, is_custom) VALUES (?, 1)", (ch_name,))
                    conn.commit()
                sync_database_to_supabase()

        return redirect(url_for('admin'))

    with get_db_connection() as conn:
        cursor = conn.cursor()
        users = cursor.execute("SELECT * FROM users ORDER BY status DESC, name ASC").fetchall()
        channels = cursor.execute("SELECT * FROM channels").fetchall()
        cursor.execute("SELECT key, value FROM settings")
        settings = {r['key']: r['value'] for r in cursor.fetchall()}

    storage_usage = get_supabase_storage_usage() / (1024 * 1024)

    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width:1200px;">
        <h2>🛠️ Πάνελ Διαχείρισης Συστήματος (Admin Panel)</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link btn-home">🏠 Αρχική Σελίδα</a>
            <a href="/logout" class="btn-link btn-danger">🚪 Αποσύνδεση</a>
        </div>
        <hr>
        
        <div style="display:flex; gap:20px; flex-wrap:wrap;">
            <div style="flex:1; min-width:300px; background:#f8fafc; padding:15px; border-radius:8px; border:1px solid #cbd5e1;">
                <h3>⚙️ Ρυθμίσεις Συστήματος & SMTP</h3>
                <form method="POST">
                    <input type="hidden" name="action" value="toggle_roster">
                    <p><b>Roster Βάρδιας:</b> {% if roster_active %}<span style="color:green;">Ενεργό</span>{% else %}<span style="color:red;">Ανενεργό</span>{% endif %}</p>
                    <button type="submit" class="btn-link btn-warning">{% if roster_active %}Απενεργοποίηση Roster{% else %}Ενεργοποίηση Roster{% endif %}</button>
                </form>
                <hr>
                <form method="POST">
                    <input type="hidden" name="action" value="update_smtp">
                    <label><b>SMTP Server:</b></label>
                    <input type="text" name="smtp_server" value="{{ settings.get('smtp_server', '') }}">
                    <label><b>SMTP Port:</b></label>
                    <input type="text" name="smtp_port" value="{{ settings.get('smtp_port', '587') }}">
                    <label><b>SMTP User (Email):</b></label>
                    <input type="text" name="smtp_user" value="{{ settings.get('smtp_user', '') }}">
                    <label><b>SMTP Password:</b></label>
                    <input type="password" name="smtp_pass" placeholder="••••••••">
                    <button type="submit" class="btn-link btn-success" style="margin-top:10px;">💾 Αποθήκευση SMTP</button>
                </form>
                <hr>
                <form method="POST">
                    <input type="hidden" name="action" value="update_external">
                    <label><b>Ad-hoc Gateway (External Station URL):</b></label>
                    <input type="text" name="external_station_url" value="{{ settings.get('external_station_url', '') }}" placeholder="https://other-station.onrender.com/webhook">
                    <button type="submit" class="btn-link btn-success" style="margin-top:10px;">💾 Αποθήκευση Gateway</button>
                </form>
                <hr>
                <p><b>Supabase Storage Usage:</b> {{ "%.2f" | format(storage_usage) }} MB</p>
            </div>

            <div style="flex:2; min-width:400px; background:#f8fafc; padding:15px; border-radius:8px; border:1px solid #cbd5e1;">
                <h3>👥 Διαχείριση Προσωπικού & Χρηστών</h3>
                <div style="max-height:400px; overflow-y:auto;">
                    <table>
                        <tr>
                            <th>Όνομα</th>
                            <th>Τηλέφωνο</th>
                            <th>Σταθμός</th>
                            <th>Κατάσταση</th>
                            <th>Ενέργειες</th>
                        </tr>
                        {% for u in users %}
                        <tr>
                            <td><b>{{ u['name'] }}</b><br><small>{{ u['role'] }}</small></td>
                            <td>{{ u['phone'] }}</td>
                            <td>{{ u['station'] }}</td>
                            <td>{% if u['status'] == 'Approved' %}<span style="color:green;">Εγκριμένος</span>{% else %}<span style="color:orange;">Εκκρεμεί</span>{% endif %}</td>
                            <td>
                                {% if u['status'] != 'Approved' %}
                                <form method="POST" style="display:inline;">
                                    <input type="hidden" name="action" value="approve">
                                    <input type="hidden" name="user_id" value="{{ u['id'] }}">
                                    <button type="submit" class="btn-link btn-success" style="padding:3px 6px; font-size:11px;">✔️ Έγκριση</button>
                                </form>
                                {% endif %}
                                <form method="POST" style="display:inline;" onsubmit="return confirm('Διαγραφή χρήστη;');">
                                    <input type="hidden" name="action" value="delete_user">
                                    <input type="hidden" name="user_id" value="{{ u['id'] }}">
                                    <button type="submit" class="btn-link btn-danger" style="padding:3px 6px; font-size:11px;">🗑️</button>
                                </form>
                            </td>
                        </tr>
                        {% endfor %}
                    </table>
                </div>
                <hr>
                <h3>💬 Προσθήκη Νέου Καναλιού</h3>
                <form method="POST" style="display:flex; gap:10px;">
                    <input type="hidden" name="action" value="add_channel">
                    <input type="text" name="channel_name" placeholder="Όνομα καναλιού..." required>
                    <button type="submit" class="btn-link btn-success">➕ Προσθήκη</button>
                </form>
            </div>
        </div>
    </div>
    """, users=users, channels=channels, settings=settings, roster_active=is_roster_active(), storage_usage=storage_usage)

@app.route('/guide')
def guide():
    if 'user_phone' not in session: return redirect(url_for('login'))
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width:900px;">
        <h2>📖 Επιχειρησιακός Οδηγός Χρήσης Συστήματος v7.4</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link btn-home">🏠 Αρχική Σελίδα</a>
            <a href="/logout" class="btn-link btn-danger">🚪 Αποσύνδεση</a>
        </div>
        <hr>
        <h3>1. Επισκόπηση Συστήματος</h3>
        <p>Το σύστημα <b>Σταθμοί Λάρνακας v7.4</b> παρέχει πλήρη επιχειρησιακή υποστήριξη για την Λιμενική Αστυνομία, διασφαλίζοντας την αδιάλειπτη καταγραφή συμβάντων, διαχείριση σκαφών, ψηφιακή βιβλιοθήκη εγγράφων και διαχείριση βαρδιών (Roster).</p>
        
        <h3>2. Χάρτης Μεσογείου (Mediterranean Map)</h3>
        <p>Ο επιχειρησιακός χάρτης επιτρέπει την άμεση τοποθέτηση συμβάντων SAR, παράνομης μετανάστευσης, ρύπανσης ή παραβιάσεων ISPS με ακριβείς συντεταγμένες GPS (Lat/Lng). Με κλικ στον χάρτη συμπληρώνονται αυτόματα οι συντεταγμένες.</p>

        <h3>3. Logbooks & Κανάλια</h3>
        <p>Κάθε κανάλι λειτουργεί σαν ασφαλής ροή μηνυμάτων (chat) με δυνατότητα ανάρτησης εγγράφων Word, Excel και Adobe Acrobat (PDF). Τα αρχεία προβάλλονται απευθείας στον browser σε λειτουργία view-only (`target="_blank"`) χωρίς μόνιμη τοπική αποθήκευση.</p>

        <h3>4. Εξαγωγή Roster & Email</h3>
        <p>Οι υπεύθυνοι μπορούν να εξάγουν το roster σε αρχείο Excel και να το αποστέλλουν αυτόματα μέσω SMTP σε όλο το προσωπικό.</p>
    </div>
    """)

# =====================================================================
# 8. WEBHOOK ΓΙΑ AD-HOC GATEWAY
# =====================================================================
@app.route('/webhook', methods=['POST'])
def webhook():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({"error": "Unauthorized"}), 401
    
    token = auth_header.split(' ')[1]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        admin_user = cursor.execute("SELECT id FROM users WHERE phone = ? AND role = 'Admin'", (token,)).fetchone()
        if not admin_user:
            return jsonify({"error": "Invalid token"}), 403

    data = request.get_json()
    if not data: return jsonify({"error": "No data"}), 400

    author = data.get('author', 'Εξωτερικός Σταθμός')
    content = data.get('content', '')
    timestamp = data.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO logbook_entries (channel_name, author_name, incident_category, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                       ('Ad-hoc', f"[Gateway] {author}", 'Γενικό', content, timestamp))
        conn.commit()
    sync_database_to_supabase()
    return jsonify({"success": True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)