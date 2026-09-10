import os
import sqlite3
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import openpyxl
from io import BytesIO

# Εισαγωγή του επίσημου SDK του Supabase
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
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE NOT NULL, name TEXT NOT NULL, station TEXT NOT NULL, status TEXT DEFAULT 'Pending', role TEXT DEFAULT 'User', password TEXT, email TEXT, admin_level INTEGER DEFAULT 0, custom_permissions TEXT DEFAULT '')''')
        for col, col_type in [("password", "TEXT"), ("email", "TEXT"), ("admin_level", "INTEGER DEFAULT 0"), ("custom_permissions", "TEXT DEFAULT ''")]:
            try: cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type};")
            except: pass

        cursor.execute('''CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, is_custom INTEGER DEFAULT 0)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS channel_members (channel_id INTEGER, user_id INTEGER, FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, UNIQUE(channel_id, user_id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS user_notification_settings (user_id INTEGER, channel_id INTEGER, enabled INTEGER DEFAULT 1, PRIMARY KEY(user_id, channel_id), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS logbook_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_name TEXT NOT NULL, author_name TEXT NOT NULL, content TEXT NOT NULL, filename TEXT, mega_link TEXT, timestamp TEXT NOT NULL)''')
        try: cursor.execute("ALTER TABLE logbook_entries ADD COLUMN mega_link TEXT;")
        except: pass

        cursor.execute('''CREATE TABLE IF NOT EXISTS vessel_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, vessel_name TEXT NOT NULL, entry_date TEXT NOT NULL, incident TEXT, damage TEXT, working_hours TEXT, repair_report TEXT, author TEXT NOT NULL, timestamp TEXT NOT NULL)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS library (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, category TEXT NOT NULL, filename TEXT NOT NULL, mega_link TEXT, uploader TEXT NOT NULL, timestamp TEXT NOT NULL)''')
        try: cursor.execute("ALTER TABLE library ADD COLUMN mega_link TEXT;")
        except: pass

        cursor.execute('''CREATE TABLE IF NOT EXISTS roster_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, year_month TEXT NOT NULL, day INTEGER NOT NULL, symbol TEXT NOT NULL, UNIQUE(user_id, year_month, day), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
        
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password', ?)", (generate_password_hash(DEFAULT_ADMIN_PASSWORD),))
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('roster_enabled', '0')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_server', 'smtp.gmail.com')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_port', '587')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_user', '')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_pass', '')")
        
        initial_channels = ['Γενικό Κανάλι', 'Ναυτικός Σταθμός', 'Μαρίνα', 'Λιμάνι', 'Τεχνικοί', 'Κυβερνήτες Α\'', 'Ad-hoc', 'Αρχείο', 'E-Καθήκοντα', 'Ψηφιακή Βιβλιοθήκη', 'Roster Βάρδιας']
        for ch in initial_channels:
            cursor.execute("INSERT OR IGNORE INTO channels (name, is_custom) VALUES (?, 0)", (ch,))
            
        cursor.execute("UPDATE channels SET name = 'Ad-hoc' WHERE name = 'Διοίκηση'")
        cursor.execute("UPDATE logbook_entries SET channel_name = 'Ad-hoc' WHERE channel_name = 'Διοίκηση'")

        for i in range(1, 11):
            cursor.execute("INSERT OR IGNORE INTO channels (name, is_custom) VALUES (?, 0)", (f'Σκάφος {i}',))
        
        conn.commit()

init_db()
sync_database_to_supabase()
ROSTER_SYMBOLS = ['', 'Μ', 'Ν', 'SL', 'Α', 'RD', 'T', 'Υ', 'Π', 'Ε', 'ΑΠ']

def upload_file_to_supabase(file_path, filename, overwrite=False):
    if not supabase: return None
    try:
        # Αν ζητηθεί overwrite, διαγράφουμε πρώτα το αρχείο αν υπάρχει ήδη στο cloud
        if overwrite:
            try:
                supabase.storage.from_(BUCKET_NAME).remove([filename])
            except:
                pass
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

BASE_STYLE = """
<style>
    body { background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 50%, #0369a1 100%); background-attachment: fixed; font-family: Arial, sans-serif; color: #fff; margin: 0; padding: 20px; }
    .container { max-width: 1100px; margin: 0 auto; background: rgba(255, 255, 255, 0.96); color: #333; padding: 25px; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.6); }
    a { color: #0056b3; text-decoration: none; } a:hover { text-decoration: underline; }
    input, select, textarea { padding: 8px; margin: 5px 0; border: 1px solid #ccc; border-radius: 4px; width: 100%; box-sizing: border-box; }
    button, .btn-link { display: inline-block; background: #0056b3; color: white !important; border: none; padding: 8px 14px; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 13px; text-decoration: none; margin: 2px 0; }
    button:hover, .btn-link:hover { background: #004085; }
    .btn-danger { background: #dc2626 !important; } .btn-success { background: #16a34a !important; } .btn-warning { background: #ca8a04 !important; }
    .nav-buttons { display: flex; flex-wrap: wrap; gap: 8px; margin: 15px 0; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; background: #fff; font-size: 13px; }
    th, td { border: 1px solid #ddd; padding: 6px; text-align: center; color: #333; } th { background: #f2f2f2; }
    hr { border: 0; height: 1px; background: #ccc; margin: 20px 0; }
    .viber-chat-container { background: #e7ebf0; padding: 15px; border-radius: 8px; max-height: 500px; overflow-y: auto; margin-bottom: 15px; }
    .chat-bubble { background: #ffffff; padding: 10px 14px; border-radius: 12px; margin-bottom: 10px; max-width: 80%; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .chat-author { font-size: 12px; font-weight: bold; color: #075e54; margin-bottom: 3px; }
    .chat-time { font-size: 10px; color: #888; float: right; margin-left: 10px; }
    .chat-text { font-size: 13px; color: #222; word-break: break-word; }
</style>
<div style="max-width:1100px; margin:0 auto 10px; display:flex; justify-content:space-between; color:#e2e8f0; font-weight:bold;">
    <span>⚓ Σταθμοί Λάρνακας version 6.1 (Cloud Overwrite & Orphan File Cleaner)</span>
    {% if session.get('user_phone') %}<a href="/user_settings" style="color:white;">⚙️ Ρυθμίσεις Χρήστη</a>{% endif %}
</div>
"""

@app.route('/')
def index():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status, name, role, password, email FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        if not user: session.clear(); return redirect(url_for('login'))
        if not user['password'] or not user['email']: return redirect(url_for('set_password'))
        if user['status'] != 'Approved' and user['role'] != 'Admin':
            return render_template_string(BASE_STYLE + "<div class='container'><h3>Αναμονή έγκρισης.</h3><a href='/logout' class='btn-danger'>Αποσύνδεση</a></div>")
        
        if user['role'] == 'Admin': cursor.execute("SELECT name FROM channels")
        else: cursor.execute("SELECT c.name FROM channels c JOIN channel_members cm ON c.id = cm.channel_id WHERE cm.user_id = ?", (user['id'],))
        channels = sorted(list(set([r[0] for r in cursor.fetchall()])))
    
    vessel_channels = [c for c in channels if "Σκάφος" in c or "Ταχύπλοο" in c]
    standard_channels = [c for c in channels if c not in vessel_channels and c not in ['Ψηφιακή Βιβλιοθήκη', 'Roster Βάρδιας']]

    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Καλώς ορίσατε, {{ name }}</h2>
        <div class="nav-buttons">
            <a href="/admin" class="btn-link btn-warning">Κεντρικό Πάνελ Διαχείρισης</a>
            <a href="/library" class="btn-link">Ψηφιακή Βιβλιοθήκη</a>
            {% if roster_active or role == 'Admin' %}<a href="/roster" class="btn-link">Roster Βάρδιας</a>{% endif %}
            <a href="/logout" class="btn-link btn-danger">Αποσύνδεση</a>
        </div>
        <hr>
        <h3>Επιχειρησιακά Κανάλια & Logbooks</h3>
        <ul>{% for ch in standard_channels %}<li><a href="/logbook/{{ ch }}">{% if ch == 'Γενικό Κανάλι' %}<b>📢 {{ ch }}</b>{% else %}{{ ch }}{% endif %}</a></li>{% endfor %}</ul>
        <hr>
        <h3>Logbooks Σκαφών</h3>
        <ul>{% for v in vessel_channels %}<li><a href="/vessel_log/{{ v }}"><b>🛥️ {{ v }}</b></a></li>{% endfor %}</ul>
    </div>
    """, name=user['name'], standard_channels=standard_channels, vessel_channels=vessel_channels, roster_active=is_roster_active(), role=user['role'])

@app.route('/api/check_new/<channel_name>/<int:last_id>')
def check_new_messages(channel_name, last_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(id) FROM logbook_entries WHERE channel_name = ?", (channel_name,))
        max_id = cursor.fetchone()[0] or 0
    return jsonify({"has_new": max_id > last_id, "new_max_id": max_id})

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
            else: error_msg = "Λάθος κωδικός."
        else: error_msg = "Ο αριθμός δεν βρέθηκε."
    return render_template_string(BASE_STYLE + "<div class='container' style='max-width:400px; margin-top:50px;'><h2>Σύνδεση</h2><p style='color:red;'>{{ error_msg }}</p><form method='POST'>Τηλέφωνο: <input name='phone' required><br>Κωδικός: <input type='password' name='password'><br><br><button style='width:100%;'>Σύνδεση</button></form></div>", error_msg=error_msg)

@app.route('/set_password', methods=['GET', 'POST'])
def set_password():
    if 'user_phone' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        if request.form.get('new_password') == request.form.get('confirm_password') and request.form.get('email'):
            with get_db_connection() as conn:
                conn.cursor().execute("UPDATE users SET password = ?, email = ? WHERE phone = ?", (generate_password_hash(request.form.get('new_password')), request.form.get('email').strip(), session['user_phone']))
                conn.commit()
            sync_database_to_supabase()
            return redirect(url_for('index'))
    return render_template_string(BASE_STYLE + "<div class='container'><h2>Ορισμός Κωδικού</h2><form method='POST'>Email: <input type='email' name='email' required><br>Νέος Κωδικός: <input type='password' name='new_password' required><br>Επιβεβαίωση: <input type='password' name='confirm_password' required><br><button>Αποθήκευση</button></form></div>")

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        phone, name, station, email = request.form.get('phone'), request.form.get('name'), request.form.get('station'), request.form.get('email')
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                uc = cursor.cursor().execute("SELECT COUNT(*) FROM users").fetchone()[0]
                role, status, lvl = ('Admin', 'Approved', 1) if uc == 0 else ('User', 'Pending', 0)
                cursor.execute("INSERT INTO users (phone, name, station, status, role, email, admin_level) VALUES (?, ?, ?, ?, ?, ?, ?)", (phone, name, station, status, role, email, lvl))
                conn.commit()
            sync_database_to_supabase()
            session['user_phone'] = phone
            return redirect(url_for('set_password'))
        except sqlite3.IntegrityError: flash('Το τηλέφωνο υπάρχει ήδη.')
    return render_template_string(BASE_STYLE + "<div class='container' style='max-width:400px;'><h2>Εγγραφή</h2><form method='POST'>Τηλέφωνο: <input name='phone' required><br>Όνομα: <input name='name' required><br>Σταθμός: <input name='station' required><br>Email: <input name='email' required><br><button>Εγγραφή</button></form></div>")

@app.route('/logbook/<channel_name>', methods=['GET', 'POST'])
def logbook(channel_name):
    if 'user_phone' not in session: return redirect(url_for('login'))
    clean_old_channel_entries()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT name, role FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if request.method == 'POST':
            content = request.form.get('content', '')
            file = request.files.get('file')
            overwrite_flag = True if request.form.get('overwrite_file') == 'on' else False
            filename, cloud_link = None, None
            if file and file.filename != '':
                raw_name = secure_filename(file.filename)
                # Αν ζητηθεί overwrite κρατάμε το αυτούσιο όνομα, αλλιώς βάζουμε UUID
                filename = raw_name if overwrite_flag else f"{uuid.uuid4().hex}_{raw_name}"
                local_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(local_path)
                cloud_link = upload_file_to_supabase(local_path, filename, overwrite=overwrite_flag)
            cursor.execute("INSERT INTO logbook_entries (channel_name, author_name, content, filename, mega_link, timestamp) VALUES (?, ?, ?, ?, ?, ?)", (channel_name, user['name'], content, filename, cloud_link, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
            sync_database_to_supabase()
            
        entries = cursor.execute("SELECT id, author_name, content, filename, mega_link, timestamp FROM logbook_entries WHERE channel_name = ? ORDER BY id DESC", (channel_name,)).fetchall()
        max_id = entries[0]['id'] if entries else 0
    
    is_archive = (channel_name == 'Αρχείο')
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Κανάλι: {{ channel_name }}</h2><a href="/" class="btn-link" style="background:#475569;">Αρχική</a>
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; margin: 15px 0;">
            <textarea name="content" required></textarea><br>
            Αρχείο: <input type="file" name="file"><br>
            <label style="font-size:12px; color:#333; cursor:pointer;"><input type="checkbox" name="overwrite_file" style="width:auto;"> <b>Αντικατάσταση υπάρχοντος αρχείου (ίδιο όνομα στο Cloud)</b></label><br><br>
            <button>Καταχώρηση</button>
        </form>
        <div class="{% if not is_archive %}viber-chat-container{% endif %}">
            {% for e in entries %}
                <div class="chat-bubble" style="{% if is_archive %}background:#fff; margin-bottom:10px; max-width:100%; border:1px solid #ddd;{% endif %}">
                    <div class="chat-author">{{ e['author_name'] }} <span class="chat-time">{{ e['timestamp'] }}</span></div>
                    <div class="chat-text">{{ e['content'] }}</div>
                    {% if e['mega_link'] %}<div><a href="{{ e['mega_link'] }}" target="_blank" class="btn-link" style="font-size:11px;">📥 Λήψη Αρχείου</a></div>{% endif %}
                    {% if role == 'Admin' %}<form method="POST" action="/delete_logbook_message" style="margin-top:8px;"><input type="hidden" name="entry_id" value="{{ e['id'] }}"><input type="hidden" name="channel_name" value="{{ channel_name }}"><button class="btn-danger" style="font-size:10px;">🗑️ Διαγραφή</button></form>{% endif %}
                </div>
            {% endfor %}
        </div>
    </div>
    {% if not is_archive %}
    <script>
        let lastId = {{ max_id }};
        setInterval(() => {
            fetch(`/api/check_new/{{ channel_name }}/${lastId}`).then(r => r.json()).then(data => {
                if(data.has_new) { new Audio('https://actions.google.com/sounds/v1/alarms/beep_short.ogg').play(); setTimeout(() => location.reload(), 1000); }
            });
        }, 10000);
    </script>
    {% endif %}
    """, channel_name=channel_name, entries=entries, is_archive=is_archive, role=user['role'], max_id=max_id)

@app.route('/delete_logbook_message', methods=['POST'])
def delete_logbook_message():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT filename FROM logbook_entries WHERE id = ?", (request.form.get('entry_id'),)).fetchone()
        if row and row['filename']: delete_file_from_supabase(row['filename'])
        cursor.execute("DELETE FROM logbook_entries WHERE id = ?", (request.form.get('entry_id'),))
        conn.commit()
    sync_database_to_supabase()
    return redirect(url_for('logbook', channel_name=request.form.get('channel_name')))

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'user_phone' not in session: return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT id, role FROM users WHERE phone = ?", (session['user_phone'],)).fetchone()
        if not user or user['role'] != 'Admin': return "Μη εξουσιοδοτημένη πρόσβαση"
        
        msg = ""
        if request.method == 'POST':
            entered_pass = request.form.get('admin_password')
            if check_password_hash(get_current_admin_password_hash(), entered_pass):
                action = request.form.get('action')
                if action == 'delete_orphan':
                    fname = request.form.get('filename')
                    delete_file_from_supabase(fname)
                    msg = f"Το ορφανό αρχείο '{fname}' διεγράφη."
                elif action == 'rename_channel':
                    old_name, new_name = request.form.get('old_name'), request.form.get('new_name')
                    cursor.execute("UPDATE channels SET name = ? WHERE name = ?", (new_name, old_name))
                    cursor.execute("UPDATE logbook_entries SET channel_name = ? WHERE channel_name = ?", (new_name, old_name))
                    cursor.execute("UPDATE vessel_logs SET vessel_name = ? WHERE vessel_name = ?", (new_name, old_name))
                    conn.commit()
                    sync_database_to_supabase()
                    msg = f"Μετονομάστηκε σε '{new_name}'."
                elif action == 'hard_delete_user':
                    uid = request.form.get('user_id')
                    uname = cursor.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()['name']
                    for table in ['library', 'logbook_entries']:
                        col = 'uploader' if table == 'library' else 'author_name'
                        for r in cursor.execute(f"SELECT filename FROM {table} WHERE {col} = ?", (uname,)).fetchall():
                            if r['filename']: delete_file_from_supabase(r['filename'])
                        cursor.execute(f"DELETE FROM {table} WHERE {col} = ?", (uname,))
                    cursor.execute("DELETE FROM users WHERE id = ?", (uid,))
                    conn.commit()
                    sync_database_to_supabase()
                    msg = "Ο χρήστης και τα αρχεία του διαγράφηκαν οριστικά."
            else: msg = "Λάθος κωδικός διαχειριστή."

        users = cursor.execute("SELECT id, name, phone, role, status FROM users").fetchall()
        channels = cursor.execute("SELECT name FROM channels").fetchall()
        
        # Εντοπισμός Ορφανών Αρχείων (Orphan Files στο Supabase Storage)
        orphan_files = []
        try:
            if supabase:
                all_cloud_files = supabase.storage.from_(BUCKET_NAME).list()
                # Συλλογή όλων των filenames από DB
                db_files = set()
                for r in cursor.execute("SELECT filename FROM logbook_entries WHERE filename IS NOT NULL").fetchall(): db_files.add(r['filename'])
                for r in cursor.execute("SELECT filename FROM library WHERE filename IS NOT NULL").fetchall(): db_files.add(r['filename'])
                db_files.add(DB_NAME) # Εξαίρεση της βάσης δεδομένων
                
                for f in all_cloud_files:
                    if isinstance(f, dict) and 'name' in f:
                        fname = f['name']
                        if fname not in db_files:
                            orphan_files.append({'name': fname, 'size': f.get('metadata', {}).get('size', 0)})
        except Exception as e:
            logging.error(f"[ORPHAN CHECK ERROR] {e}")

        used_mb = get_supabase_storage_usage() / (1024 * 1024)
        usage_percent = (used_mb / 1024) * 100
        
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Κεντρικό Πάνελ Διαχείρισης (v6.1)</h2>
        <a href="/" class="btn-link" style="background:#475569;">Επιστροφή στην Αρχική</a>
        <p style="color:red; font-weight:bold;">{{ msg }}</p>

        <div style="background:#f0fdf4; border:1px solid #22c55e; padding:10px; border-radius:5px; margin-bottom:20px; font-weight:bold; color:#15803d;">
            📊 Στατιστικά Cloud Storage: Χρήση {{ "%.1f"|format(usage_percent) }}% ({{ "%.1f"|format(used_mb) }} MB / 1024 MB)
        </div>

        {% if orphan_files %}
        <div style="background:#fef2f2; border:1px solid #ef4444; padding:15px; border-radius:5px; margin-bottom:20px;">
            <h3 style="color:#b91c1c; margin-top:0;">🧹 Διαχείριση Ορφανών Αρχείων (Cloud Storage Cleanup)</h3>
            <p style="color:#991b1b; font-size:12px;">Τα παρακάτω αρχεία υπάρχουν φυσικά στον Cloud Server αλλά δεν συνδέονται με καμία ενεργή εγγραφή στη βάση:</p>
            <table>
                <tr><th>Όνομα Αρχείου</th><th>Μέγεθος</th><th>Ενέργεια Καθαρισμού</th></tr>
                {% for of in orphan_files %}
                <tr>
                    <td style="text-align:left; font-family:monospace;">{{ of['name'] }}</td>
                    <td>{{ "%.1f"|format(of['size'] / 1024) }} KB</td>
                    <td>
                        <form method="POST" onsubmit="return confirm('Οριστική διαγραφή ορφανού αρχείου;');">
                            <input type="hidden" name="action" value="delete_orphan"><input type="hidden" name="filename" value="{{ of['name'] }}">
                            <button class="btn-danger" style="font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Διαχειριστή:');">🗑️ Διαγραφή</button><input type="hidden" name="admin_password" value="">
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>
        {% endif %}
        
        <hr>
        <h3>🗑️ Διαχείριση Χρηστών & Ολική Διαγραφή (GDPR)</h3>
        <table>
            <tr><th>Όνομα</th><th>Τηλέφωνο</th><th>Κατάσταση</th><th>Ολική Διαγραφή</th></tr>
            {% for u in users %}
            <tr>
                <td>{{ u['name'] }}</td><td>{{ u['phone'] }}</td><td>{{ u['status'] }}</td>
                <td>
                    <form method="POST" onsubmit="return confirm('ΠΡΟΣΟΧΗ: Διαγραφή χρήστη και αρχείων;');">
                        <input type="hidden" name="action" value="hard_delete_user"><input type="hidden" name="user_id" value="{{ u['id'] }}">
                        <button class="btn-danger" style="font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Διαχειριστή:');">Ολική Διαγραφή</button><input type="hidden" name="admin_password" value="">
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    """, msg=msg, users=users, channels=channels, used_mb=used_mb, usage_percent=usage_percent, orphan_files=orphan_files)

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

if __name__ == "__main__": app.run(host='0.0.0.0', port=10000)