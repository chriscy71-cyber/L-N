import os
import sqlite3
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import openpyxl
from io import BytesIO

try:
    from mega import Mega
    MEGA_AVAILABLE = True
except ImportError:
    MEGA_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'lkn_ast_larnakas_secure_key_2026')

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DB_NAME = 'lkn_secure_mobile.db'
DEFAULT_ADMIN_PASSWORD = 'LKN_ADMIN_2026'

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
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
        )
        """)
        
        for col, col_type in [("password", "TEXT"), ("email", "TEXT"), ("admin_level", "INTEGER DEFAULT 0"), ("custom_permissions", "TEXT DEFAULT ''")]:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type};")
            except sqlite3.OperationalError:
                pass

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            is_custom INTEGER DEFAULT 0
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS channel_members (
            channel_id INTEGER,
            user_id INTEGER,
            FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(channel_id, user_id)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_notification_settings (
            user_id INTEGER,
            channel_id INTEGER,
            enabled INTEGER DEFAULT 1,
            PRIMARY KEY(user_id, channel_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS logbook_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_name TEXT NOT NULL,
            author_name TEXT NOT NULL,
            content TEXT NOT NULL,
            filename TEXT,
            mega_link TEXT,
            timestamp TEXT NOT NULL
        )
        """)
        
        try:
            cursor.execute("ALTER TABLE logbook_entries ADD COLUMN mega_link TEXT;")
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS vessel_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vessel_name TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            incident TEXT,
            damage TEXT,
            working_hours TEXT,
            repair_report TEXT,
            author TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            filename TEXT NOT NULL,
            mega_link TEXT,
            uploader TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
        """)
        try:
            cursor.execute("ALTER TABLE library ADD COLUMN mega_link TEXT;")
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS roster_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            year_month TEXT NOT NULL,
            day INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            UNIQUE(user_id, year_month, day),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)
        
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password', ?)", (generate_password_hash(DEFAULT_ADMIN_PASSWORD),))
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('roster_enabled', '0')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_server', 'smtp.gmail.com')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_port', '587')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_user', '')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('smtp_pass', '')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('mega_email', '')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('mega_pass', '')")
        
        initial_channels = [
            'Γενικό Κανάλι', 'Ναυτικός Σταθμός', 'Μαρίνα', 'Λιμάνι', 
            'Τεχνικοί', 'Κυβερνήτες Α\'', 'Διοίκηση', 'Αρχείο', 
            'E-Καθήκοντα', 'Ψηφιακή Βιβλιοθήκη', 'Roster Βάρδιας'
        ]
        
        for ch in initial_channels:
            cursor.execute("INSERT OR IGNORE INTO channels (name, is_custom) VALUES (?, 0)", (ch,))

        initial_vessels = [f'Σκάφος {i}' for i in range(1, 11)]
        for v in initial_vessels:
            cursor.execute("INSERT OR IGNORE INTO channels (name, is_custom) VALUES (?, 0)", (v,))
        
        conn.commit()

init_db()

ROSTER_SYMBOLS = ['', 'Μ', 'Ν', 'SL', 'Α', 'RD', 'T', 'Υ', 'Π', 'Ε', 'ΑΠ']

def clean_old_channel_entries():
    target_channels = ['Ναυτικός Σταθμός', 'Λιμάνι', 'Μαρίνα']
    two_months_ago = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d %H:%M:%S')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for ch in target_channels:
            cursor.execute("DELETE FROM logbook_entries WHERE channel_name = ? AND timestamp < ?", (ch, two_months_ago))
        conn.commit()

def upload_to_mega(file_path, original_filename):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key IN ('mega_email', 'mega_pass')")
        m_cfg = {row['key']: row['value'] for row in cursor.fetchall()}
    
    m_email = m_cfg.get('mega_email', '')
    m_pass = m_cfg.get('mega_pass', '')

    if MEGA_AVAILABLE and m_email and m_pass:
        try:
            mega = Mega()
            m = mega.login(m_email, m_pass)
            file = m.upload(file_path)
            link = m.get_link(file)
            return link
        except Exception as e:
            logging.error(f"[MEGA ERROR] {e}")
    return None

def get_current_admin_password_hash():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'admin_password'")
        row = cursor.fetchone()
        return row[0] if row else generate_password_hash(DEFAULT_ADMIN_PASSWORD)

def is_roster_active():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'roster_enabled'")
        row = cursor.fetchone()
        return row[0] == '1' if row else False

def get_super_admin_id():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM users 
            WHERE role = 'Admin' AND status = 'Approved' AND admin_level = 1
            ORDER BY id ASC LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            return row['id']
        # Fallback αν δεν υπάρχει κανείς με level 1
        cursor.execute("""
            SELECT id FROM users 
            WHERE role = 'Admin' AND status = 'Approved' 
            ORDER BY admin_level ASC, id ASC LIMIT 1
        """)
        row2 = cursor.fetchone()
        return row2['id'] if row2 else None

def handle_admin_departure(departed_user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT admin_level, role FROM users WHERE id = ?", (departed_user_id,))
        res = cursor.fetchone()
        if res and res['role'] == 'Admin' and res['admin_level'] == 1:
            cursor.execute("""
                SELECT id FROM users 
                WHERE role = 'Admin' AND status = 'Approved' AND id != ?
                ORDER BY admin_level ASC, id ASC LIMIT 1
            """, (departed_user_id,))
            next_admin = cursor.fetchone()
            if next_admin:
                cursor.execute("UPDATE users SET admin_level = 1, custom_permissions = '' WHERE id = ?", (next_admin['id'],))
                conn.commit()

def check_admin_permission(user_id, required_permission):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role, admin_level, custom_permissions FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or row['role'] != 'Admin':
            return False
        level = row['admin_level']
        if level <= 3:
            return True
        if level == 4:
            perms = row['custom_permissions'].split(',') if row['custom_permissions'] else []
            return required_permission in perms
    return False

def send_real_email(to_email, subject, body, attachment_bytes=None, attachment_name="roster.xlsx"):
    if not to_email or '@' not in to_email:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings WHERE key IN ('smtp_server', 'smtp_port', 'smtp_user', 'smtp_pass')")
        s_settings = {row['key']: row['value'] for row in cursor.fetchall()}
    
    server_host = s_settings.get('smtp_server', 'smtp.gmail.com')
    port_val = s_settings.get('smtp_port', '587')
    smtp_user = s_settings.get('smtp_user', '')
    smtp_pass = s_settings.get('smtp_pass', '')

    if not smtp_user or not smtp_pass:
        logging.info(f"[SIMULATED SMTP] To: {to_email} | Subj: {subject} | Body: {body}")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        if attachment_bytes:
            from email.mime.application import MIMEApplication
            att = MIMEApplication(attachment_bytes.read(), Name=attachment_name)
            att['Content-Disposition'] = f'attachment; filename="{attachment_name}"'
            msg.attach(att)

        port = int(port_val)
        server = smtplib.SMTP(server_host, port)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, msg.as_string())
        server.quit()
    except Exception as e:
        logging.error(f"[SMTP ERROR] Failed to send email to {to_email}: {e}")

BASE_STYLE = """
<style>
    body {
        background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 50%, #0369a1 100%);
        background-attachment: fixed;
        font-family: Arial, sans-serif;
        color: #fff;
        margin: 0;
        padding: 20px;
    }
    .app-brand-bar {
        max-width: 1100px;
        margin: 0 auto 10px auto;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 15px;
        font-weight: bold;
        color: #e2e8f0;
        text-shadow: 0 2px 4px rgba(0,0,0,0.5);
    }
    .settings-gear {
        background: rgba(255, 255, 255, 0.2);
        color: white;
        padding: 6px 12px;
        border-radius: 4px;
        text-decoration: none;
        font-size: 13px;
        font-weight: bold;
        transition: background 0.2s;
    }
    .settings-gear:hover {
        background: rgba(255, 255, 255, 0.4);
        text-decoration: none;
    }
    .container {
        max-width: 1100px;
        margin: 0 auto;
        background: rgba(255, 255, 255, 0.96);
        color: #333;
        padding: 25px;
        border-radius: 8px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.6);
    }
    a { color: #0056b3; text-decoration: none; }
    a:hover { text-decoration: underline; }
    input, select, textarea {
        padding: 8px;
        margin: 5px 0;
        border: 1px solid #ccc;
        border-radius: 4px;
        width: 100%;
        box-sizing: border-box;
    }
    button, .btn-link {
        display: inline-block;
        background: #0056b3;
        color: white !important;
        border: none;
        padding: 8px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-weight: bold;
        text-decoration: none;
        font-size: 13px;
        text-align: center;
        margin: 2px 0;
    }
    button:hover, .btn-link:hover { background: #004085; text-decoration: none; }
    .btn-danger { background: #dc2626 !important; }
    .btn-danger:hover { background: #991b1b !important; }
    .btn-success { background: #16a34a !important; }
    .btn-success:hover { background: #15803d !important; }
    .btn-warning { background: #ca8a04 !important; }
    .btn-warning:hover { background: #a16207 !important; }
    .nav-buttons {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin: 15px 0;
    }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; background: #fff; font-size: 13px; }
    th, td { border: 1px solid #ddd; padding: 6px; text-align: center; color: #333; }
    th { background: #f2f2f2; }
    hr { border: 0; height: 1px; background: #ccc; margin: 20px 0; }
    
    .viber-chat-container {
        background: #e7ebf0;
        padding: 15px;
        border-radius: 8px;
        max-height: 500px;
        overflow-y: auto;
        margin-bottom: 15px;
    }
    .chat-bubble {
        background: #ffffff;
        padding: 10px 14px;
        border-radius: 12px;
        margin-bottom: 10px;
        max-width: 80%;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        position: relative;
    }
    .chat-author {
        font-size: 12px;
        font-weight: bold;
        color: #075e54;
        margin-bottom: 3px;
    }
    .chat-time {
        font-size: 10px;
        color: #888;
        float: right;
        margin-left: 10px;
    }
    .chat-text {
        font-size: 13px;
        color: #222;
        word-break: break-word;
    }
</style>
<div class="app-brand-bar">
    <span>⚓ Σταθμοί Λάρνακας version 4.1 (Super Admin Double Protection)</span>
    {% if session.get('user_phone') %}
        <a href="/user_settings" class="settings-gear">⚙️ Ρυθμίσεις Χρήστη</a>
    {% endif %}
</div>
"""

@app.route('/')
def index():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status, name, role, password, email FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        
        if not user:
            session.clear()
            return redirect(url_for('login'))
            
        user_id, status, name, role, password, email = user
        
        if not password or not email:
            return redirect(url_for('set_password'))
            
        if status != 'Approved' and role != 'Admin':
            return render_template_string(BASE_STYLE + """
            <div class='container'>
                <h3>Ο λογαριασμός σας αναμένει έγκριση από τον Διαχειριστή.</h3>
                <div class="nav-buttons">
                    <a href='/user_settings' class="btn-link btn-warning">⚙️ Ρυθμίσεις Χρήστη</a>
                    <a href='/logout' class="btn-link btn-danger">Αποσύνδεση</a>
                </div>
            </div>
            """)
        
        if role == 'Admin':
            cursor.execute("SELECT name FROM channels")
        else:
            cursor.execute("""
                SELECT c.name FROM channels c
                JOIN channel_members cm ON c.id = cm.channel_id
                WHERE cm.user_id = ?
            """, (user_id,))
        
        channels = sorted(list(set([row[0] for row in cursor.fetchall()])))
    
    vessel_channels = [f'Σκάφος {i}' for i in range(1, 11)]
    standard_channels = [ch for ch in channels if ch not in vessel_channels and ch != 'Ψηφιακή Βιβλιοθήκη' and ch != 'Roster Βάρδιας']

    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Καλώς ορίσατε, {{ name }}</h2>
        <div class="nav-buttons">
            <a href="/admin" class="btn-link">Κεντρικό Πάνελ Διαχείρισης</a>
            <a href="/library" class="btn-link">Ψηφιακή Βιβλιοθήκη</a>
            {% if roster_active or role == 'Admin' %}
                <a href="/roster" class="btn-link">Roster Βάρδιας (XL & Email)</a>
            {% endif %}
            <a href="/user_settings" class="btn-link btn-warning">⚙️ Ρυθμίσεις Χρήστη</a>
            <a href="/logout" class="btn-link btn-danger">Αποσύνδεση</a>
        </div>
        <hr>
        <h3>Επιχειρησιακά Κανάλια & Logbooks</h3>
        <ul>
            {% for ch in standard_channels %}
                {% if ch == 'Γενικό Κανάλι' %}
                    <li><a href="/logbook/{{ ch }}"><b>📢 {{ ch }} (Γενική Ειδοποίηση)</b></a></li>
                {% else %}
                    <li><a href="/logbook/{{ ch }}">{{ ch }}</a></li>
                {% endif %}
            {% endfor %}
        </ul>
        <hr>
        <h3>Logbooks 10 Σκαφών</h3>
        <ul>
            {% for v in vessel_channels %}
                {% if role == 'Admin' or v in channels %}
                    <li><a href="/vessel_log/{{ v }}"><b>🛥️ {{ v }}</b></a></li>
                {% endif %}
            {% endfor %}
        </ul>
    </div>
    """, name=name, standard_channels=standard_channels, vessel_channels=vessel_channels, channels=channels, roster_active=is_roster_active(), role=role)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error_msg = ""
    if request.method == 'POST':
        phone = request.form.get('phone')
        password = request.form.get('password', '')
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT phone, password, email FROM users WHERE phone = ?", (phone,))
            user = cursor.fetchone()
        
        if user:
            db_pass = user['password']
            db_email = user['email']
            session['user_phone'] = phone
            if not db_pass or not db_email:
                return redirect(url_for('set_password'))
            else:
                if check_password_hash(db_pass, password):
                    return redirect(url_for('index'))
                else:
                    error_msg = "Λάθος κωδικός πρόσβασης."
        else:
            error_msg = "Ο αριθμός δεν βρέθηκε. Παρακαλώ εγγραφείτε."
            
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width: 400px; margin-top: 50px;">
        <h2>Σύνδεση</h2>
        {% if error_msg %}
            <p style="color:red; font-weight:bold;">{{ error_msg }}</p>
        {% endif %}
        <form method="POST">
            Τηλέφωνο: <input type="text" name="phone" required><br>
            Κωδικός Πρόσβασης: <input type="password" name="password" placeholder="Απαραίτητος μετά την 1η είσοδο"><br><br>
            <button type="submit" style="width:100%;">Σύνδεση</button>
        </form>
        <div style="margin-top:15px; text-align:center;">
            <a href="/register" class="btn-link" style="width:100%; box-sizing:border-box;">Εγγραφή Νέου Χρήστη</a>
        </div>
    </div>
    """, error_msg=error_msg)

@app.route('/set_password', methods=['GET', 'POST'])
def set_password():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
        
    msg = ""
    if request.method == 'POST':
        new_pass = request.form.get('new_password')
        confirm_pass = request.form.get('confirm_password')
        email = request.form.get('email', '').strip()
        
        if not email:
            msg = "Το email είναι υποχρεωτικό για τη λειτουργία των ειδοποιήσεων."
        elif new_pass and new_pass == confirm_pass:
            hashed_pass = generate_password_hash(new_pass)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET password = ?, email = ? WHERE phone = ?", (hashed_pass, email, session['user_phone']))
                conn.commit()
            return redirect(url_for('index'))
        else:
            msg = "Οι κωδικοί δεν ταιριάζουν ή είναι κενοί."
            
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE phone = ?", (session['user_phone'],))
        row = cursor.fetchone()
        current_email = row[0] if row and row[0] else ''

    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width: 400px; margin-top: 50px;">
        <h2>Ορισμός Κωδικού & Email</h2>
        <p style="color:#1d4ed8; font-weight:bold;">⚠️ Απαιτείται υποχρεωτικά η καταχώρηση έγκυρου Email και Κωδικού.</p>
        <p style="color:red;">{{ msg }}</p>
        <form method="POST">
            Email επικοινωνίας: <input type="email" name="email" value="{{ current_email }}" required><br>
            Νέος Κωδικός: <input type="password" name="new_password" required><br>
            Επιβεβαίωση Νέου Κωδικού: <input type="password" name="confirm_password" required><br><br>
            <button type="submit" style="width:100%;">Αποθήκευση</button>
        </form>
    </div>
    """, msg=msg, current_email=current_email)

@app.route('/user_settings', methods=['GET', 'POST'])
def user_settings():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
        
    msg = ""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, email FROM users WHERE phone = ?", (session['user_phone'],))
        user_row = cursor.fetchone()
        if not user_row:
            return redirect(url_for('login'))
        user_id, user_name, current_email = user_row[0], user_row[1], user_row[2]

        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'update_profile':
                new_email = request.form.get('email', '').strip()
                current_pass = request.form.get('current_password', '')
                new_pass = request.form.get('new_password', '')
                confirm_pass = request.form.get('confirm_password', '')

                cursor.execute("SELECT password FROM users WHERE id = ?", (user_id,))
                db_pwd = cursor.fetchone()[0]

                if new_pass:
                    if not check_password_hash(db_pwd, current_pass):
                        msg = "Λάθος τρέχων κωδικός πρόσβασης."
                    elif new_pass != confirm_pass:
                        msg = "Οι νέοι κωδικοί δεν ταιριάζουν."
                    else:
                        hashed_p = generate_password_hash(new_pass)
                        cursor.execute("UPDATE users SET email = ?, password = ? WHERE id = ?", (new_email, hashed_p, user_id))
                        conn.commit()
                        msg = "Το προφίλ και ο κωδικός ενημερώθηκαν επιτυχώς."
                        current_email = new_email
                else:
                    cursor.execute("UPDATE users SET email = ? WHERE id = ?", (new_email, user_id))
                    conn.commit()
                    msg = "Το email ενημερώθηκε επιτυχώς."
                    current_email = new_email

            elif action == 'update_notifications':
                cursor.execute("""
                    SELECT c.id FROM channels c
                    JOIN channel_members cm ON c.id = cm.channel_id
                    WHERE cm.user_id = ?
                    UNION
                    SELECT id FROM channels WHERE name = 'Γενικό Κανάλι'
                """, (user_id,))
                user_ch_rows = cursor.fetchall()
                
                for ch in user_ch_rows:
                    ch_id = ch[0]
                    is_enabled = 1 if request.form.get(f'notif_{ch_id}') == 'on' else 0
                    cursor.execute("""
                        INSERT INTO user_notification_settings (user_id, channel_id, enabled)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id, channel_id) DO UPDATE SET enabled = ?
                    """, (user_id, ch_id, is_enabled, is_enabled))
                conn.commit()
                msg = "Οι ρυθμίσεις ειδοποιήσεων καναλιών αποθηκεύτηκαν."

        cursor.execute("""
            SELECT c.id, c.name FROM channels c
            JOIN channel_members cm ON c.id = cm.channel_id
            WHERE cm.user_id = ?
            UNION
            SELECT id, name FROM channels WHERE name = 'Γενικό Κανάλι'
        """, (user_id,))
        channels_accessible = cursor.fetchall()

        channel_notif_prefs = {}
        for ch in channels_accessible:
            ch_id = ch[0]
            cursor.execute("SELECT enabled FROM user_notification_settings WHERE user_id = ? AND channel_id = ?", (user_id, ch_id))
            ns = cursor.fetchone()
            channel_notif_prefs[ch_id] = ns['enabled'] if ns else 1

    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>⚙️ Πάνελ Ρυθμίσεων Χρήστη</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link" style="background:#475569;">Επιστροφή στην Αρχική</a>
        </div>
        <p style="color:red; font-weight:bold;">{{ msg }}</p>

        <form method="POST" style="background:#f1f5f9; padding:15px; border:1px solid #cbd5e1; border-radius:5px; margin-bottom:20px;">
            <input type="hidden" name="action" value="update_profile">
            <h3>👤 Στοιχεία Λογαριασμού & Κωδικός</h3>
            Email Ειδοποιήσεων: <input type="email" name="email" value="{{ current_email }}" required><br><br>
            <p style="font-size:12px; color:#475569;">Συμπληρώστε τα παρακάτω πεδία μόνο αν επιθυμείτε αλλαγή κωδικού:</p>
            Τρέχων Κωδικός Πρόσβασης: <input type="password" name="current_password"><br>
            Νέος Κωδικός Πρόσβασης: <input type="password" name="new_password"><br>
            Επιβεβαίωση Νέου Κωδικού: <input type="password" name="confirm_password"><br><br>
            <button type="submit">Αποθήκευση Αλλαγών</button>
        </form>

        <form method="POST" style="background:#f1f5f9; padding:15px; border:1px solid #cbd5e1; border-radius:5px;">
            <input type="hidden" name="action" value="update_notifications">
            <h3>🔔 Προτιμήσεις Ειδοποιήσεων ανά Κανάλι</h3>
            <ul style="list-style:none; padding:0;">
                {% for ch in channels_accessible %}
                    <li style="margin-bottom:8px; background:#fff; padding:8px; border-radius:4px; border:1px solid #e2e8f0;">
                        <label>
                            <input type="checkbox" name="notif_{{ ch[0] }}" {% if channel_notif_prefs[ch[0]] == 1 %}checked{% endif %} style="width:auto; margin-right:8px;">
                            <b>{{ ch[1] }}</b>
                        </label>
                    </li>
                {% endfor %}
            </ul>
            <button type="submit" class="btn-success">Αποθήκευση Προτιμήσεων Ειδοποιήσεων</button>
        </form>
    </div>
    """, current_email=current_email, channels_accessible=channels_accessible, channel_notif_prefs=channel_notif_prefs, msg=msg)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        phone = request.form.get('phone')
        name = request.form.get('name')
        station = request.form.get('station')
        email = request.form.get('email')
        
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users")
                user_count = cursor.fetchone()[0]
                
                if user_count == 0:
                    cursor.execute("INSERT INTO users (phone, name, station, status, role, password, email, admin_level) VALUES (?, ?, ?, 'Approved', 'Admin', NULL, ?, 1)",
                                   (phone, name, station, email))
                else:
                    cursor.execute("INSERT INTO users (phone, name, station, status, role, password, email, admin_level) VALUES (?, ?, ?, 'Pending', 'User', NULL, ?, 0)",
                                   (phone, name, station, email))
                conn.commit()
            session['user_phone'] = phone
            return redirect(url_for('set_password'))
        except sqlite3.IntegrityError:
            flash('Το τηλέφωνο υπάρχει ήδη.')
        return redirect(url_for('login'))
        
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width: 400px; margin-top: 50px;">
        <h2>Εγγραφή</h2>
        <form method="POST">
            Τηλέφωνο: <input type="text" name="phone" required><br>
            Όνομα: <input type="text" name="name" required><br>
            Σταθμός: <input type="text" name="station" required><br>
            Email: <input type="email" name="email" required><br><br>
            <button type="submit" style="width:100%;">Εγγραφή & Ορισμός Κωδικού</button>
        </form>
        <div style="margin-top:15px; text-align:center;">
            <a href="/login" class="btn-link" style="width:100%; box-sizing:border-box; background:#475569;">Επιστροφή στη Σύνδεση</a>
        </div>
    </div>
    """)

@app.route('/library', methods=['GET', 'POST'])
def library():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, status, role FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        
        if not user or (user[2] != 'Approved' and user[3] != 'Admin'):
            return "Μη εξουσιοδοτημένη πρόσβαση."
        
        user_id, author_name, status, role = user
        
        if request.method == 'POST' and role == 'Admin':
            title = request.form.get('title')
            category = request.form.get('category')
            file = request.files.get('file')
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{filename}"
                local_path = os.path.join(UPLOAD_FOLDER, unique_filename)
                file.save(local_path)
                
                mega_link = upload_to_mega(local_path, unique_filename)
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute("INSERT INTO library (title, category, filename, mega_link, uploader, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                               (title, category, unique_filename, mega_link, author_name, timestamp))
                conn.commit()
        
        cursor.execute("SELECT id, title, category, filename, mega_link, uploader, timestamp FROM library ORDER BY id DESC")
        items = cursor.fetchall()
        
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Ψηφιακή Βιβλιοθήκη & Αρχείο</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link" style="background:#475569;">Αρχική</a>
        </div>
        
        {% if role == 'Admin' %}
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; border:1px solid #cbd5e1; border-radius:5px;">
            <h3>📤 Μεταφόρτωση Υλικού (Διαχειριστής)</h3>
            Τίτλος / Περιγραφή: <input type="text" name="title" required><br>
            Κατηγορία: 
            <select name="category" required>
                <option value="Βιβλίο">Βιβλίο</option>
                <option value="Ανακοίνωση">Ανακοίνωση</option>
                <option value="Έγγραφο">Έγγραφο</option>
            </select><br>
            Αρχείο: <input type="file" name="file" required><br><br>
            <button type="submit">Μεταφόρτωση</button>
        </form>
        <hr>
        {% endif %}
        
        <h3>Διαθέσιμα Αρχεία & Βιβλία</h3>
        <table>
            <tr><th>Τίτλος</th><th>Κατηγορία</th><th>Ανάρτηση Από</th><th>Ημερομηνία</th><th>Λήψη</th>{% if role == 'Admin' %}<th>Διαγραφή</th>{% endif %}</tr>
            {% for item_id, title, cat, fname, mlink, up, time in items %}
            <tr>
                <td><b>{{ title }}</b></td>
                <td>{{ cat }}</td>
                <td>{{ up }}</td>
                <td>{{ time }}</td>
                <td>
                    {% if mlink %}
                        <a href="{{ mlink }}" target="_blank" class="btn-link" style="padding:4px 8px; font-size:11px;">📥 Cloud (Mega)</a>
                    {% else %}
                        <a href="/uploads/{{ fname }}" target="_blank" class="btn-link" style="padding:4px 8px; font-size:11px;">📥 Τοπική Λήψη</a>
                    {% endif %}
                </td>
                {% if role == 'Admin' %}
                <td>
                    <form method="POST" action="/delete_library_item" onsubmit="return confirm('Διαγραφή αρχείου;');">
                        <input type="hidden" name="item_id" value="{{ item_id }}">
                        <button type="submit" class="btn-danger" style="padding:2px 6px; font-size:11px;">🗑️</button>
                    </form>
                </td>
                {% endif %}
            </tr>
            {% else %}
            <tr><td colspan="6">Δεν υπάρχουν καταχωρημένα αρχεία στη βιβλιοθήκη.</td></tr>
            {% endfor %}
        </table>
    </div>
    """, items=items, role=role)

@app.route('/delete_library_item', methods=['POST'])
def delete_library_item():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, role FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        if not user or user['role'] != 'Admin':
            return "Δεν έχετε εξουσιοδότηση."
        item_id = request.form.get('item_id')
        cursor.execute("DELETE FROM library WHERE id = ?", (item_id,))
        conn.commit()
    return redirect(url_for('library'))

@app.route('/roster', methods=['GET', 'POST'])
def roster():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, status, role FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        
        if not user or (user[2] != 'Approved' and user[3] != 'Admin'):
            return "Μη εξουσιοδοτημένη πρόσβαση."
        
        user_id, author_name, status, role = user
        
        if not is_roster_active() and role != 'Admin':
            return render_template_string(BASE_STYLE + """
            <div class='container'>
                <h3>Το Roster Βάρδιας δεν είναι ακόμη ενεργό.</h3>
                <div class="nav-buttons"><a href='/' class="btn-link" style="background:#475569;">Αρχική</a></div>
            </div>
            """)
        
        current_ym = request.args.get('ym', datetime.now().strftime('%Y-%m'))
        
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'save_roster':
                ym = request.form.get('year_month', current_ym)
                for day in range(1, 32):
                    sym = request.form.get(f'day_{day}', '').strip().upper()
                    if sym in ROSTER_SYMBOLS:
                        cursor.execute("""
                            INSERT INTO roster_entries (user_id, year_month, day, symbol)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(user_id, year_month, day) DO UPDATE SET symbol = ?
                        """, (user_id, ym, day, sym, sym))
                conn.commit()
                return redirect(url_for('roster', ym=ym))
            
            elif action == 'send_excel_email' and role == 'Admin':
                ym = request.form.get('year_month', current_ym)
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.title = f"Roster {ym}"
                
                headers = ['Ονοματεπώνυμο'] + [str(d) for d in range(1, 32)]
                ws.append(headers)
                
                cursor.execute("SELECT id, name FROM users WHERE status = 'Approved' ORDER BY name")
                u_list = cursor.fetchall()
                
                cursor.execute("SELECT user_id, day, symbol FROM roster_entries WHERE year_month = ?", (ym,))
                r_rows = cursor.fetchall()
                r_data = {u['id']: {d: '' for d in range(1, 32)} for u in u_list}
                for uid, day, sym in r_rows:
                    if uid in r_data:
                        r_data[uid][day] = sym
                
                for u in u_list:
                    row_data = [u['name']] + [r_data[u['id']][d] for d in range(1, 32)]
                    ws.append(row_data)
                
                excel_io = BytesIO()
                wb.save(excel_io)
                excel_io.seek(0)
                
                cursor.execute("SELECT email FROM users WHERE role = 'Admin' AND email IS NOT NULL AND email != ''")
                admins = cursor.fetchall()
                for adm in admins:
                    send_real_email(adm['email'], f"Μηνιαίο Roster Βάρδιας - {ym}", f"Επισυνάπτεται το αρχείο Excel για το Roster του μήνα {ym}.", excel_io, f"Roster_{ym}.xlsx")
                    excel_io.seek(0)
                
                flash("Το Roster σε μορφή Excel στάλθηκε επιτυχώς στους Διαχειριστές μέσω email!")
        
        cursor.execute("SELECT id, name FROM users WHERE status = 'Approved' ORDER BY name LIMIT 50")
        users_list = cursor.fetchall()
        
        cursor.execute("SELECT user_id, day, symbol FROM roster_entries WHERE year_month = ?", (current_ym,))
        rows = cursor.fetchall()
        
        roster_data = {u[0]: {d: '' for d in range(1, 32)} for u in users_list}
        for uid, day, sym in rows:
            if uid in roster_data:
                roster_data[uid][day] = sym
                    
    my_row = roster_data.get(user_id, {})
    
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width:100%;">
        <h2>Ψηφιακό Roster Βάρδιας (XL Matrix & Email)</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link" style="background:#475569;">Αρχική</a>
        </div>
        
        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <p style="color:green; font-weight:bold;">{{ messages[0] }}</p>
          {% endif %}
        {% endwith %}
        
        <form method="GET" style="margin-bottom:15px; display:flex; gap:10px; align-items:flex-end;">
            <div>
                Επιλογή Μήνα (ΕΕΕΕ-ΜΜ): <input type="text" name="ym" value="{{ current_ym }}" style="width:150px;">
            </div>
            <div>
                <button type="submit">Προβολή</button>
            </div>
        </form>

        {% if role == 'Admin' %}
        <form method="POST" style="margin-bottom:15px;">
            <input type="hidden" name="action" value="send_excel_email">
            <input type="hidden" name="year_month" value="{{ current_ym }}">
            <button type="submit" class="btn-success">📧 Αποστολή Roster Μήνα σε Excel (Email)</button>
        </form>
        {% endif %}
        
        <div style="background:#e0f2fe; padding:10px; border-radius:4px; margin-bottom:15px; font-size:12px;">
            <b>Σύμβολα:</b> Μ = Μέρα | Ν = Νύχτα | SL = Sick Leave | Α = Άδεια Απουσίας | RD = Rest Day | T = Τρομοκρατία | Υ = Υπερωριακό | Π = Πλοίο | Ε = Εκπαίδευση | ΑΠ = Αποστολή
        </div>
        
        <form method="POST">
            <input type="hidden" name="action" value="save_roster">
            <input type="hidden" name="year_month" value="{{ current_ym }}">
            <h3>Η προσωπική μου συμπλήρωση για τον μήνα: {{ current_ym }}</h3>
            <div style="overflow-x:auto;">
            <table>
                <tr>
                    {% for d in range(1, 32) %}
                    <th>{{ d }}</th>
                    {% endfor %}
                </tr>
                <tr>
                    {% for d in range(1, 32) %}
                    <td>
                        <select name="day_{{ d }}" style="width:50px; text-align:center; padding:2px; font-size:12px;">
                            {% set current_val = my_row.get(d, '') %}
                            {% for sym in roster_symbols %}
                                <option value="{{ sym }}" {% if current_val == sym %}selected{% endif %}>{{ sym if sym else '-' }}</option>
                            {% endfor %}
                        </select>
                    </td>
                    {% endfor %}
                </tr>
            </table>
            </div>
            <button type="submit" style="margin-top:10px;">Αποθήκευση Δικών μου Βαρδιών</button>
        </form>
        
        <hr>
        <h3>Συγκεντρωτικός Πίνακας Προσωπικού (XL View)</h3>
        <div style="overflow-x:auto;">
        <table>
            <tr>
                <th style="position:sticky; left:0; background:#eee; z-index:2;">Ονοματεπώνυμο</th>
                {% for d in range(1, 32) %}
                <th>{{ d }}</th>
                {% endfor %}
            </tr>
            {% for u in users_list %}
            <tr>
                <td style="position:sticky; left:0; background:#f9f9f9; text-align:left; font-weight:bold;">{{ u[1] }}</td>
                {% for d in range(1, 32) %}
                <td>{{ roster_data[u[0]][d] }}</td>
                {% endfor %}
            </tr>
            {% endfor %}
        </table>
        </div>
    </div>
    """, users_list=users_list, roster_data=roster_data, current_ym=current_ym, my_row=my_row, roster_symbols=ROSTER_SYMBOLS, role=role)

@app.route('/logbook/<channel_name>', methods=['GET', 'POST'])
def logbook(channel_name):
    if 'user_phone' not in session:
        return redirect(url_for('login'))
        
    clean_old_channel_entries()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, status, role, admin_level FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        
        if not user or (user['status'] != 'Approved' and user['role'] != 'Admin'):
            return "Μη εξουσιοδοτημένη πρόσβαση."
            
        user_id, author_name, role, admin_level = user['id'], user['name'], user['role'], user['admin_level']
        
        cursor.execute("SELECT id FROM channels WHERE name = ?", (channel_name,))
        ch_row = cursor.fetchone()
        if not ch_row:
            return "Το κανάλι δεν βρέθηκε."
            
        ch_id = ch_row[0]
        
        if role != 'Admin' and channel_name != 'Γενικό Κανάλι':
            cursor.execute("SELECT 1 FROM channel_members WHERE channel_id = ? AND user_id = ?", (ch_id, user_id))
            if not cursor.fetchone():
                return render_template_string(BASE_STYLE + """
                <div class='container'>
                    <h3 style='color:red;'>⚠️ Δεν έχετε εξουσιοδότηση πρόσβασης σε αυτό το κανάλι.</h3>
                    <div class="nav-buttons"><a href='/' class="btn-link" style="background:#475569;">Αρχική</a></div>
                </div>
                """)

        if request.method == 'POST':
            if channel_name == 'E-Καθήκοντα' and role != 'Admin':
                return render_template_string(BASE_STYLE + """
                <div class='container'>
                    <h3 style='color:red;'>⚠️ Δεν έχετε εξουσιοδότηση: Τα E-Καθήκοντα απαιτούν δικαιώματα Διαχειριστή.</h3>
                    <div class="nav-buttons"><a href='/logbook/E-Καθήκοντα' class="btn-link" style="background:#475569;">Επιστροφή</a></div>
                </div>
                """)

            content = request.form.get('content', '')
            file = request.files.get('file')
            filename = None
            mega_link = None
            
            if file and file.filename != '':
                raw_filename = secure_filename(file.filename)
                filename = f"{uuid.uuid4().hex}_{raw_filename}"
                local_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(local_path)
                mega_link = upload_to_mega(local_path, filename)
                
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute("INSERT INTO logbook_entries (channel_name, author_name, content, filename, mega_link, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                           (channel_name, author_name, content, filename, mega_link, timestamp))
            conn.commit()
            
        cursor.execute("SELECT id, author_name, content, filename, mega_link, timestamp FROM logbook_entries WHERE channel_name = ? ORDER BY id DESC", (channel_name,))
        entries = cursor.fetchall()
    
    is_e_kathikonta = (channel_name == 'E-Καθήκοντα')
    is_archive = (channel_name == 'Αρχείο')
    
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Κανάλι / Logbook: {{ channel_name }}</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link" style="background:#475569;">Αρχική</a>
        </div>
        
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; border:1px solid #cbd5e1; border-radius:5px;">
            {% if is_e_kathikonta %}
                <p style="color:#1d4ed8; font-weight:bold;">⚠️ Τα E-Καθήκοντα επιτρέπονται μόνο σε εξουσιοδοτημένους Διαχειριστές.</p>
            {% endif %}
            <textarea name="content" placeholder="Καταχώρηση..." rows="3" required></textarea><br>
            Επισύναψη Αρχείου (Cloud Mega): <input type="file" name="file"><br><br>
            <button type="submit">Καταχώρηση</button>
        </form>
        <hr>
        <h3>Ιστορικό Καταχωρήσεων</h3>

        {% if not is_archive %}
            <div class="viber-chat-container">
                {% for entry_id, author, content, filename, mlink, time in entries %}
                    <div class="chat-bubble">
                        <div class="chat-author">{{ author }} <span class="chat-time">{{ time }}</span></div>
                        <div class="chat-text">{{ content }}</div>
                        {% if mlink %}
                            <div style="margin-top:6px;"><a href="{{ mlink }}" target="_blank" class="btn-link" style="padding:2px 6px; font-size:11px;">📥 Λήψη Αρχείου (Mega)</a></div>
                        {% elif filename %}
                            <div style="margin-top:6px;"><a href="/uploads/{{ filename }}" target="_blank" class="btn-link" style="padding:2px 6px; font-size:11px;">📥 Τοπική Λήψη</a></div>
                        {% endif %}
                        
                        {% if role == 'Admin' %}
                            <div style="margin-top:8px; border-top:1px dashed #ccc; padding-top:4px;">
                                <form method="POST" action="/delete_logbook_message" style="display:inline;" onsubmit="return confirm('Διαγραφή μηνύματος;');">
                                    <input type="hidden" name="entry_id" value="{{ entry_id }}">
                                    <input type="hidden" name="channel_name" value="{{ channel_name }}">
                                    <button type="submit" class="btn-danger" style="padding:2px 6px; font-size:10px;">🗑️ Διαγραφή (Admin)</button>
                                </form>
                            </div>
                        {% endif %}
                    </div>
                {% else %}
                    <p style="color:#555; text-align:center;">Δεν υπάρχουν μηνύματα.</p>
                {% endfor %}
            </div>
        {% else %}
            <ul>
                {% for entry_id, author, content, filename, mlink, time in entries %}
                    <li style="margin-bottom: 10px; background:#fff; padding:10px; border-radius:4px; border:1px solid #e2e8f0;">
                        <b>[{{ time}}] {{ author }}:</b> {{ content }}
                        {% if mlink %}
                            <br>📎 <i>Αρχείο (Mega):</i> <a href="{{ mlink }}" target="_blank" class="btn-link" style="padding:2px 6px; font-size:11px; margin-top:5px;">📥 Λήψη</a>
                        {% elif filename %}
                            <br>📎 <i>Αρχείο:</i> <a href="/uploads/{{ filename }}" target="_blank" class="btn-link" style="padding:2px 6px; font-size:11px; margin-top:5px;">📥 Λήψη</a>
                        {% endif %}
                        {% if role == 'Admin' %}
                            <form method="POST" action="/delete_logbook_message" style="margin-top:5px;" onsubmit="return confirm('Διαγραφή μηνύματος;');">
                                <input type="hidden" name="entry_id" value="{{ entry_id }}">
                                <input type="hidden" name="channel_name" value="{{ channel_name }}">
                                <button type="submit" class="btn-danger" style="padding:2px 6px; font-size:10px;">🗑️ Διαγραφή (Admin)</button>
                            </form>
                        {% endif %}
                    </li>
                {% else %}
                    <li>Δεν υπάρχουν καταχωρήσεις.</li>
                {% endfor %}
            </ul>
        {% endif %}
    </div>
    """, channel_name=channel_name, entries=entries, is_e_kathikonta=is_e_kathikonta, is_archive=is_archive, role=role)

@app.route('/delete_logbook_message', methods=['POST'])
def delete_logbook_message():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        if not user or user['role'] != 'Admin':
            return "Δεν έχετε εξουσιοδότηση."
        entry_id = request.form.get('entry_id')
        channel_name = request.form.get('channel_name')
        cursor.execute("DELETE FROM logbook_entries WHERE id = ?", (entry_id,))
        conn.commit()
    return redirect(url_for('logbook', channel_name=channel_name))

@app.route('/vessel_log/<vessel_name>', methods=['GET', 'POST'])
def vessel_log(vessel_name):
    if 'user_phone' not in session:
        return redirect(url_for('login'))
        
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, status, role FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        
        if not user or (user[2] != 'Approved' and user[3] != 'Admin'):
            return "Μη εξουσιοδοτημένη πρόσβαση."
            
        user_id, author_name, status, role = user[0], user[1], user[2], user[3]
        
        cursor.execute("SELECT id FROM channels WHERE name = ?", (vessel_name,))
        ch_row = cursor.fetchone()
        if not ch_row:
            return "Το σκάφος δεν βρέθηκε."
            
        ch_id = ch_row[0]
        
        if role != 'Admin':
            cursor.execute("SELECT 1 FROM channel_members WHERE channel_id = ? AND user_id = ?", (ch_id, user_id))
            if not cursor.fetchone():
                return render_template_string(BASE_STYLE + """
                <div class='container'>
                    <h3 style='color:red;'>⚠️ Δεν έχετε εξουσιοδότηση πρόσβασης σε αυτό το σκάφος.</h3>
                    <div class="nav-buttons"><a href='/' class="btn-link" style="background:#475569;">Αρχική</a></div>
                </div>
                """)

        if request.method == 'POST':
            entry_date = request.form.get('entry_date', datetime.now().strftime('%Y-%m-%d'))
            incident = request.form.get('incident', '')
            damage = request.form.get('damage', '')
            working_hours = request.form.get('working_hours', '')
            repair_report = request.form.get('repair_report', '')
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute("""
                INSERT INTO vessel_logs (vessel_name, entry_date, incident, damage, working_hours, repair_report, author, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (vessel_name, entry_date, incident, damage, working_hours, repair_report, author_name, timestamp))
            conn.commit()
            
        cursor.execute("SELECT id, entry_date, incident, damage, working_hours, repair_report, author, timestamp FROM vessel_logs WHERE vessel_name = ? ORDER BY id DESC", (vessel_name,))
        entries = cursor.fetchall()
        
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Logbook Σκάφους: {{ vessel_name }}</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link" style="background:#475569;">Αρχική</a>
        </div>
        
        <form method="POST" style="background:#f1f5f9; padding:15px; border:1px solid #cbd5e1; border-radius:5px;">
            <h3>📝 Νέα Καταχώρηση Logbook</h3>
            Ημερομηνία Καταχώρισης: <input type="date" name="entry_date" value="{{ today }}" required><br>
            Περιστατικό: <textarea name="incident" rows="2" placeholder="Περιγραφή περιστατικού..." required></textarea><br>
            Βλάβη: <textarea name="damage" rows="2" placeholder="Αναφορά βλάβης..." required></textarea><br>
            Ώρες Εργασίας: <input type="text" name="working_hours" placeholder="π.χ. 4.5 ώρες" required><br>
            Αναφορά Επιδιόρθωσης: <textarea name="repair_report" rows="2" placeholder="Λεπτομέρειες αποκατάστασης..." required></textarea><br><br>
            <button type="submit">Καταχώρηση στο Logbook Σκάφους</button>
        </form>
        <hr>
        <h3>Ιστορικό Καταχωρήσεων Σκάφους</h3>
        <ul>
            {% for entry_id, edate, incident, damage, whours, repair, author, time in entries %}
                <li style="margin-bottom: 12px; background:#fff; padding:12px; border-radius:4px; border:1px solid #e2e8f0;">
                    <b>[{{ edate}}] Συντάκτης: {{ author }}</b><br>
                    <b>Περιστατικό:</b> {{ incident }}<br>
                    <b>Βλάβη:</b> {{ damage }}<br>
                    <b>Ώρες Εργασίας:</b> {{ whours }}<br>
                    <b>Αναφορά Επιδιόρθωσης:</b> {{ repair }}
                    <div style="font-size:10px; color:#64748b; margin-top:4px;">Καταχωρήθηκε στις: {{ time }}</div>
                    
                    {% if role == 'Admin' %}
                        <form method="POST" action="/delete_vessel_message" style="margin-top:6px;" onsubmit="return confirm('Διαγραφή καταχώρησης σκάφους;');">
                            <input type="hidden" name="entry_id" value="{{ entry_id }}">
                            <input type="hidden" name="vessel_name" value="{{ vessel_name }}">
                            <button type="submit" class="btn-danger" style="padding:2px 6px; font-size:10px;">🗑️ Διαγραφή (Admin)</button>
                        </form>
                    {% endif %}
                </li>
            {% else %}
                <li>Δεν υπάρχουν καταχωρήσεις για αυτό το σκάφος.</li>
            {% endfor %}
        </ul>
    </div>
    """, vessel_name=vessel_name, entries=entries, today=datetime.now().strftime('%Y-%m-%d'), role=role)

@app.route('/delete_vessel_message', methods=['POST'])
def delete_vessel_message():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        if not user or user['role'] != 'Admin':
            return "Δεν έχετε εξουσιοδότηση."
        entry_id = request.form.get('entry_id')
        vessel_name = request.form.get('vessel_name')
        cursor.execute("DELETE FROM vessel_logs WHERE id = ?", (entry_id,))
        conn.commit()
    return redirect(url_for('vessel_log', vessel_name=vessel_name))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
        
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, role, admin_level FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        
        if not user or user['role'] != 'Admin':
            return render_template_string(BASE_STYLE + """
            <div class='container'>
                <h3 style='color:red;'>⚠️ Δεν έχετε εξουσιοδότηση: Απαιτείται πρόσβαση Διαχειριστή.</h3>
                <div class="nav-buttons"><a href='/' class="btn-link" style="background:#475569;">Αρχική</a></div>
            </div>
            """)
            
        current_admin_id = user['id']
        current_admin_level = user['admin_level']
        is_super_admin = (current_admin_id == get_super_admin_id() or current_admin_level == 1)

        msg = ""
        if request.method == 'POST':
            entered_pass = request.form.get('admin_password')
            current_pass_hash = get_current_admin_password_hash()
            
            if check_password_hash(current_pass_hash, entered_pass):
                action = request.form.get('action')
                if action == 'approve':
                    if not check_admin_permission(current_admin_id, 'manage_users'):
                        msg = "Δεν έχετε εξουσιοδότηση για διαχείριση χρηστών."
                    else:
                        uid = request.form.get('user_id')
                        cursor.execute("UPDATE users SET status = 'Approved' WHERE id = ?", (uid,))
                        conn.commit()
                        msg = "Ο χρήστης εγκρίθηκε επιτυχώς."
                elif action == 'revoke':
                    if not check_admin_permission(current_admin_id, 'manage_users'):
                        msg = "Δεν έχετε εξουσιοδότηση για διαχείριση χρηστών."
                    else:
                        uid = request.form.get('user_id')
                        # Έλεγχος αποτροπής διαγραφής του τελευταίου Super Admin
                        cursor.execute("SELECT admin_level FROM users WHERE id = ?", (uid,))
                        target_u = cursor.fetchone()
                        if target_u and target_u['admin_level'] == 1:
                            msg = "Σφάλμα: Δεν μπορείτε να αφαιρέσετε τον Super Admin αν δεν ορίσετε πρώτα άλλον!"
                        else:
                            handle_admin_departure(uid)
                            cursor.execute("UPDATE users SET status = 'Pending', role = 'User', admin_level = 0, custom_permissions = '' WHERE id = ?", (uid,))
                            conn.commit()
                            msg = "Η έγκριση και τα δικαιώματα αφαιρέθηκαν."
                elif action == 'set_admin_role':
                    if is_super_admin:
                        uid = request.form.get('target_user_id')
                        lvl = int(request.form.get('admin_level_val', 2))
                        perms = request.form.getlist('custom_perms')
                        perms_str = ",".join(perms) if lvl == 4 else ""
                        
                        # Αν ορίζεται νέος Super Admin (Level 1), εξασφαλίζουμε ότι ο προηγούμενος γίνεται Level 2 (για να υπάρχει μοναδικότητα)
                        if lvl == 1:
                            cursor.execute("UPDATE users SET admin_level = 2 WHERE admin_level = 1")
                            
                        cursor.execute("UPDATE users SET role = 'Admin', admin_level = ?, custom_permissions = ? WHERE id = ?", (lvl, perms_str, uid))
                        conn.commit()
                        msg = "Ο ρόλος διαχειριστή ενημερώθηκε επιτυχώς με διπλή επικύρωση."
                    else:
                        msg = "Μόνο ο Super Admin μπορεί να ορίσει ρόλους διαχειριστών."
                elif action == 'toggle_roster':
                    if not check_admin_permission(current_admin_id, 'manage_roster'):
                        msg = "Δεν έχετε εξουσιοδότηση για διαχείριση Roster."
                    else:
                        new_state = '1' if not is_roster_active() else '0'
                        cursor.execute("UPDATE settings SET value = ? WHERE key = 'roster_enabled'", (new_state,))
                        conn.commit()
                        msg = "Η κατάσταση του Roster ενημερώθηκε."
                elif action == 'save_mega':
                    if not is_super_admin:
                        msg = "Μόνο ο Super Admin μπορεί να ρυθμίσει τα στοιχεία Mega.nz."
                    else:
                        m_email = request.form.get('mega_email')
                        m_pass = request.form.get('mega_pass')
                        cursor.execute("UPDATE settings SET value = ? WHERE key = 'mega_email'", (m_email,))
                        if m_pass:
                            cursor.execute("UPDATE settings SET value = ? WHERE key = 'mega_pass'", (m_pass,))
                        conn.commit()
                        msg = "Οι ρυθμίσεις Mega.nz αποθηκεύτηκαν."
                elif action == 'change_password':
                    if not is_super_admin:
                        msg = "Μόνο ο Super Admin μπορεί να αλλάξει τον κεντρικό κωδικό διαχειριστή."
                    else:
                        new_pass = request.form.get('new_password')
                        if new_pass:
                            hashed_admin_pass = generate_password_hash(new_pass)
                            cursor.execute("UPDATE settings SET value = ? WHERE key = 'admin_password'", (hashed_admin_pass,))
                            conn.commit()
                            msg = "Ο κωδικός διαχείρισης άλλαξε επιτυχώς."
            else:
                msg = "Λάθος κωδικός διαχειριστικής πρόσβασης."
                
        cursor.execute("SELECT id, phone, name, station, status, email, role, admin_level, custom_permissions FROM users")
        users = cursor.fetchall()

        cursor.execute("SELECT key, value FROM settings WHERE key IN ('mega_email', 'mega_pass')")
        mega_cfg = {row['key']: row['value'] for row in cursor.fetchall()}
    
    super_admin_id = get_super_admin_id()

    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Κεντρικό Πάνελ Διαχείρισης</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link" style="background:#475569;">Επιστροφή στην Αρχική</a>
        </div>
        <p style="color:red; font-weight:bold;">{{ msg }}</p>
        
        <form method="POST" style="background:#f1f5f9; padding:15px; border:1px solid #cbd5e1; border-radius:5px;">
            <label><b>Εισάγετε τον Κωδικό Διαχειριστή για τις ενέργειες:</b></label><br>
            <input type="password" name="admin_password" placeholder="Κωδικός Διαχειριστή" required style="width:250px;"><br><br>
            
            <hr>
            <h3>☁️ Ρυθμίσεις Mega.nz (Cloud Storage 20 GB)</h3>
            Mega Email: <input type="text" name="mega_email" value="{{ mega_cfg.get('mega_email', '') }}"><br>
            Mega Password: <input type="password" name="mega_pass" placeholder="Αφήστε κενό για διατήρηση"><br>
            <button type="submit" name="action" value="save_mega" class="btn-success">Αποθήκευση Mega</button>

            <hr>
            <h3>🔑 Ενεργοποίηση Roster Βάρδιας</h3>
            <p>Κατάσταση Roster: <b>{% if roster_active %}ΕΝΕΡΓΟ{% else %}ΑΝΕΝΕΡΓΟ{% endif %}</b></p>
            <button type="submit" name="action" value="toggle_roster" class="{% if roster_active %}btn-danger{% else %}btn-success{% endif %}">{% if roster_active %}Απενεργοποίηση Roster{% else %}Ενεργοποίηση Roster{% endif %}</button>
        </form>

        {% if is_super_admin %}
        <hr>
        <h3>👑 Ορισμός Ιεραρχίας Διαχειριστών (1 Super Admin, 5 Κανονικοί, 3 Περιορισμένοι)</h3>
        <!-- Προσθήκη JavaScript διπλής επιβεβαίωσης αν επιλεγεί Super Admin -->
        <form method="POST" id="adminRoleForm" style="background:#eff6ff; padding:15px; border:1px solid #bfdbfe; border-radius:5px;" onsubmit="return handleSuperAdminTransfer(this);">
            <input type="hidden" name="action" value="set_admin_role">
            <label><b>Επιλογή Εγκεκριμένου Χρήστη:</b></label><br>
            <select name="target_user_id" required style="width:250px; display:inline-block;">
                <option value="">-- Επιλογή Χρήστη --</option>
                {% for u in users %}
                    {% if u['status'] == 'Approved' %}
                        <option value="{{ u['id'] }}">{{ u['name'] }} ({{ u['phone'] }}) [Level: {{ u['admin_level'] }}]</option>
                    {% endif %}
                {% endfor %}
            </select><br><br>
            <label><b>Βαθμίδα Διαχειριστή:</b></label><br>
            <select name="admin_level_val" id="adminLevelSelect" required style="width:250px; display:inline-block;">
                <option value="1"><b>Super Admin</b></option>
                <option value="2">2 - Κανονικός Διαχειριστής</option>
                <option value="3">3 - Κανονικός Διαχειριστής</option>
                <option value="4">4 - Περιορισμένος Διαχειριστής (με Checkboxes)</option>
            </select><br><br>
            <div style="font-size:12px; background:#fff; padding:10px; border:1px solid #cbd5e1; border-radius:4px; margin-bottom:10px;">
                <b>Δικαιώματα (Εφαρμόζονται μόνο στον Level 4):</b><br>
                <label><input type="checkbox" name="custom_perms" value="manage_users"> Έγκριση / Διαχείριση Χρηστών</label><br>
                <label><input type="checkbox" name="custom_perms" value="manage_channels"> Διαχείριση Καναλιών & Μελών</label><br>
                <label><input type="checkbox" name="custom_perms" value="manage_roster"> Διαχείριση Roster</label>
            </div>
            Κωδικός Διαχειριστή: <input type="password" name="admin_password" required style="width:250px;"><br><br>
            <button type="submit" class="btn-success">Ανάθεση Ρόλου Διαχειριστή</button>
        </form>
        <script>
        function handleSuperAdminTransfer(form) {
            var level = form.admin_level_val.value;
            if (level === "1") {
                return confirm("⚠️ ΠΡΟΣΟΧΗ: Πρόκειται να μεταβιβάσετε τα προνόμια του Super Admin σε άλλον χρήστη. Είστε απολύτως βέβαιος για αυτή την ενέργεια;");
            }
            return true;
        }
        </script>
        {% endif %}

        <hr>
        <h3>Έλεγχος Μελών & Εγκρίσεων</h3>
        <table>
            <tr><th>Όνομα</th><th>Τηλέφωνο</th><th>Email</th><th>Σταθμός</th><th>Κατάσταση</th><th>Ρόλος / Lvl</th><th>Ενέργειες</th></tr>
            {% for u in users %}
            <tr>
                <td>{{ u['name'] }} {% if u['id'] == super_admin_id %}👑{% endif %}</td>
                <td>{{ u['phone'] }}</td>
                <td>{{ u['email'] or '-' }}</td>
                <td>{{ u['station'] }}</td>
                <td>{{ u['status'] }}</td>
                <td>{{ u['role'] }} (Lvl: {{ u['admin_level'] }})</td>
                <td>
                    <form method="POST" style="display:inline;">
                        <input type="hidden" name="admin_password" value="" id="pwd_action_{{ u['id'] }}">
                        <input type="hidden" name="user_id" value="{{ u['id'] }}">
                        {% if u['status'] == 'Pending' %}
                            <button type="submit" name="action" value="approve" class="btn-success" style="padding:4px 8px; font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Διαχειριστή:');">Έγκριση</button>
                        {% else %}
                            <button type="submit" name="action" value="revoke" class="btn-danger" style="padding:4px 8px; font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Διαχειριστή:');">Αφαίρεση</button>
                        {% endif %}
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>

        {% if is_super_admin %}
        <hr>
        <h3>Αλλαγή Κεντρικού Κωδικού Διαχείρισης</h3>
        <form method="POST">
            <input type="hidden" name="admin_password" id="pwd_change_admin">
            Νέος Κωδικός: <input type="password" name="new_password" placeholder="Νέος Κωδικός" style="width:200px; display:inline-block;" required>
            <button type="submit" name="action" value="change_password" onclick="document.getElementById('pwd_change_admin').value=prompt('Τρέχων Κωδικός Διαχειριστή:');">Αλλαγή</button>
        </form>
        {% endif %}
    </div>
    """, users=users, msg=msg, roster_active=is_roster_active(), mega_cfg=mega_cfg, super_admin_id=super_admin_id, is_super_admin=is_super_admin)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)