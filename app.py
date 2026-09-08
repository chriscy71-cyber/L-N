import os
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(32))
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Όριο 16MB ανά αρχείο

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'xls', 'xlsx', 'txt'}

DB_NAME = 'lkn_secure_mobile.db'
DEFAULT_ADMIN_PASSWORD = 'LKN_ADMIN_2026_SECURE!'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
            email TEXT,
            status TEXT DEFAULT 'Pending',
            role TEXT DEFAULT 'User'
        )
        """)
        
        # Ασφαλής έλεγχος και προσθήκη στήλης email σε υφιστάμενες βάσεις
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN email TEXT;")
        except sqlite3.OperationalError:
            pass  # Η στήλη υπάρχει ήδη

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
        CREATE TABLE IF NOT EXISTS logbook_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_name TEXT NOT NULL,
            author_name TEXT NOT NULL,
            content TEXT NOT NULL,
            filename TEXT,
            timestamp TEXT NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            filename TEXT NOT NULL,
            uploader TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
        """)

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
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
        """)
        
        hashed_default_pass = generate_password_hash(DEFAULT_ADMIN_PASSWORD)
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password', ?)", (hashed_default_pass,))
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('roster_enabled', '0')",)
        
        initial_channels = [
            'Ναυτικός Σταθμός', 'Μαρίνα', 'Λιμάνι', 'Τεχνικοί', 
            'Κυβερνήτες Α\'', 'Διοίκηση', 'Αρχείο', 'E-Καθήκοντα', 
            'Ψηφιακή Βιβλιοθήκη', 'Roster Βαρδιών'
        ]
        
        for ch in initial_channels:
            cursor.execute("INSERT OR IGNORE INTO channels (name, is_custom) VALUES (?, 0)", (ch,))
        
        conn.commit()

init_db()

def log_audit(actor, action):
    with get_db_connection() as conn:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("INSERT INTO audit_logs (actor, action, timestamp) VALUES (?, ?, ?)", (actor, action, timestamp))
        conn.commit()

def send_notification_email(to_emails, subject, body):
    if not to_emails:
        return
    smtp_server = os.environ.get('SMTP_SERVER', 'localhost')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    sender_email = os.environ.get('SMTP_SENDER', 'alerts@portpolice.gov.cy')
    sender_pass = os.environ.get('SMTP_PASSWORD', '')

    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            if smtp_port == 587:
                server.starttls()
            if sender_pass:
                server.login(sender_email, sender_pass)
            for recipient in to_emails:
                if recipient:
                    msg['To'] = recipient
                    server.sendmail(sender_email, recipient, msg.as_string())
    except Exception as e:
        print(f"Σφάλμα αποστολής SMTP email: {e}")

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

BASE_STYLE = """
<style>
    body {
        background: linear-gradient(rgba(0, 0, 0, 0.6), rgba(0, 0, 0, 0.6)), 
                    url('https://images.unsplash.com/photo-1518241353330-0f7941c2d9b5?auto=format&fit=crop&w=1920&q=80') no-repeat center center fixed;
        background-size: cover;
        font-family: Arial, sans-serif;
        color: #fff;
        margin: 0;
        padding: 20px;
    }
    .container {
        max-width: 1100px;
        margin: 0 auto;
        background: rgba(255, 255, 255, 0.95);
        color: #333;
        padding: 25px;
        border-radius: 8px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.5);
    }
    .flash-box {
        background: #fef2f2;
        color: #991b1b;
        border: 1px solid #fecaca;
        padding: 10px;
        border-radius: 4px;
        margin-bottom: 15px;
        font-size: 13px;
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
    button {
        background: #0056b3;
        color: white;
        border: none;
        padding: 10px 15px;
        border-radius: 4px;
        cursor: pointer;
        font-weight: bold;
    }
    button:hover { background: #004085; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; background: #fff; font-size: 13px; }
    th, td { border: 1px solid #ddd; padding: 6px; text-align: center; color: #333; }
    th { background: #f2f2f2; }
    hr { border: 0; height: 1px; background: #ccc; margin: 20px 0; }
</style>
"""

@app.route('/')
def index():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status, name, role FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        
        if not user:
            session.clear()
            return redirect(url_for('login'))
            
        user_id, status, name, role = user
        if status != 'Approved' and role != 'Admin':
            return render_template_string(BASE_STYLE + "<div class='container'><h3>Ο λογαριασμός σας αναμένει έγκριση από τον Διαχειριστή.</h3><a href='/logout'>Αποσύνδεση</a></div>")
        
        if role == 'Admin':
            cursor.execute("SELECT name FROM channels")
        else:
            cursor.execute("""
                SELECT c.name FROM channels c
                JOIN channel_members cm ON c.id = cm.channel_id
                WHERE cm.user_id = ?
            """, (user_id,))
        
        channels = sorted(list(set([row[0] for row in cursor.fetchall()])))
    
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Καλώς ορίσατε, {{ name }}</h2>
        <p><a href="/admin">Κεντρικό Πάνελ Διαχείρισης</a> | <a href="/library">Ψηφιακή Βιβλιοθήκη</a> {% if roster_active or role == 'Admin' %}| <a href="/roster">Roster Βαρδιών (XL)</a>{% endif %} | <a href="/logout">Αποσύνδεση</a></p>
        <h3>Επιχειρησιακά Κανάλια & Logbooks</h3>
        <ul>
            {% for ch in channels %}
                {% if ch == 'Ψηφιακή Βιβλιοθήκη' %}
                    <li><a href="/library"><b>{{ ch }}</b></a></li>
                {% elif ch == 'Roster Βαρδιών' %}
                    {% if roster_active or role == 'Admin' %}
                        <li><a href="/roster"><b>{{ ch }}</b></a></li>
                    {% endif %}
                {% else %}
                    <li><a href="/logbook/{{ ch }}">{{ ch }}</a></li>
                {% endif %}
            {% endfor %}
        </ul>
    </div>
    """, name=name, channels=channels, roster_active=is_roster_active(), role=role)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone')
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT phone FROM users WHERE phone = ?", (phone,))
            user = cursor.fetchone()
        
        if user:
            session['user_phone'] = phone
            return redirect(url_for('index'))
        else:
            flash('Ο αριθμός δεν βρέθηκε. Παρακαλώ εγγραφείτε.')
            
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width: 400px; margin-top: 50px;">
        <h2>Σύνδεση</h2>
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                <div class="flash-box">{% for m in messages %}{{ m }}<br>{% endfor %}</div>
            {% endif %}
        {% endwith %}
        <form method="POST">
            Τηλέφωνο: <input type="text" name="phone" required><br>
            <button type="submit" style="width:100%;">Σύνδεση</button>
        </form>
        <p style="margin-top:15px; text-align:center;"><a href="/register">Εγγραφή Νέου Χρήστη</a></p>
    </div>
    """)

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
                cursor.execute("INSERT INTO users (phone, name, station, email, status, role) VALUES (?, ?, ?, ?, 'Pending', 'User')",
                               (phone, name, station, email))
                conn.commit()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Το τηλέφωνο υπάρχει ήδη.')
        
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width: 400px; margin-top: 50px;">
        <h2>Εγγραφή</h2>
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                <div class="flash-box">{% for m in messages %}{{ m }}<br>{% endfor %}</div>
            {% endif %}
        {% endwith %}
        <form method="POST">
            Τηλέφωνο: <input type="text" name="phone" required><br>
            Όνομα: <input type="text" name="name" required><br>
            Σταθμός: <input type="text" name="station" required><br>
            Ηλεκτρονική Διεύθυνση (Email): <input type="email" name="email" required><br>
            <button type="submit" style="width:100%;">Εγγραφή</button>
        </form>
        <p style="margin-top:15px; text-align:center;"><a href="/login">Επιστροφή στη Σύνδεση</a></p>
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
                if allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    file.save(os.path.join(UPLOAD_FOLDER, filename))
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    cursor.execute("INSERT INTO library (title, category, filename, uploader, timestamp) VALUES (?, ?, ?, ?, ?)",
                                   (title, category, filename, author_name, timestamp))
                    conn.commit()
                    log_audit(author_name, f"Library Upload: {title} ({filename})")
                    
                    # Αποστολή Email Ειδοποίησης για τη Βιβλιοθήκη σε όλους τους εγκεκριμένους χρήστες
                    cursor.execute("SELECT email FROM users WHERE status = 'Approved' AND email IS NOT NULL AND email != ''")
                    recipients = [row[0] for row in cursor.fetchall()]
                    email_subject = f"[LKN Library] Νέο Υλικό: {title}"
                    email_body = f"Ενημέρωση Υπηρεσίας:\n\nΠροστέθηκε νέο αρχείο/βιβλίo στην Ψηφιακή Βιβλιοθήκη.\nΤίτλος: {title}\nΚατηγορία: {category}\nΑνάρτηση από: {author_name}"
                    send_notification_email(recipients, email_subject, email_body)
                    
                    flash('Το υλικό ανέβηκε και ειδοποιήθηκαν τα μέλη.')
                else:
                    flash('Μη επιτρεπόμενος τύπος αρχείου.')
        
        cursor.execute("SELECT title, category, filename, uploader, timestamp FROM library ORDER BY id DESC")
        items = cursor.fetchall()
        
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Ψηφιακή Βιβλιοθήκη & Αρχείο</h2>
        <p><a href="/">Αρχική</a></p>
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                <div class="flash-box">{% for m in messages %}{{ m }}<br>{% endfor %}</div>
            {% endif %}
        {% endwith %}
        
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
            <tr><th>Τίτλος</th><th>Κατηγορία</th><th>Ανάρτηση Από</th><th>Ημερομηνία</th><th>Λήψη</th></tr>
            {% for title, cat, fname, up, time in items %}
            <tr>
                <td><b>{{ title }}</b></td>
                <td>{{ cat }}</td>
                <td>{{ up }}</td>
                <td>{{ time }}</td>
                <td><a href="/uploads/{{ fname }}" target="_blank">📥 Λήψη / Προβολή</a></td>
            </tr>
            {% else %}
            <tr><td colspan="5">Δεν υπάρχουν καταχωρημένα αρχεία στη βιβλιοθήκη.</td></tr>
            {% endfor %}
        </table>
    </div>
    """, items=items, role=role)

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
            return render_template_string(BASE_STYLE + "<div class='container'><h3>Το Roster Βαρδιών δεν είναι ακόμη ενεργό.</h3><a href='/'>Αρχική</a></div>")
        
        current_ym = request.args.get('ym', datetime.now().strftime('%Y-%m'))
        
        if request.method == 'POST':
            ym = request.form.get('year_month', current_ym)
            for day in range(1, 32):
                sym = request.form.get(f'day_{day}', '').strip().upper()
                if sym in ['', 'Μ', 'Ν', 'SL', 'Α', 'RD', 'T', 'Υ', 'Π', 'Ε', 'ΑΠ']:
                    cursor.execute("""
                        INSERT INTO roster_entries (user_id, year_month, day, symbol)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id, year_month, day) DO UPDATE SET symbol = ?
                    """, (user_id, ym, day, sym, sym))
            conn.commit()
            return redirect(url_for('roster', ym=ym))
        
        cursor.execute("SELECT id, name FROM users WHERE status = 'Approved' ORDER BY name LIMIT 50")
        users_list = cursor.fetchall()
        
        cursor.execute("SELECT user_id, day, symbol FROM roster_entries WHERE year_month = ?", (current_ym,))
        rows = cursor.fetchall()
        
        roster_data = {u[0]: {d: '' for d in range(1, 32)} for u in users_list}
        for uid, day, sym in rows:
            if uid in roster_data:
                roster_data[uid][day] = sym
                
        cursor.execute("SELECT DISTINCT year_month FROM roster_entries ORDER BY year_month DESC")
        months = [r[0] for r in cursor.fetchall()]
        if current_ym not in months:
            months.insert(0, current_ym)
            
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width:100%;">
        <h2>Ψηφιακό Roster Βαρδιών (XL Matrix)</h2>
        <p><a href="/">Αρχική</a></p>
        
        <form method="GET" style="margin-bottom:15px;">
            Επιλογή Μήνα (ΕΕΕΕ-ΜΜ): <input type="text" name="ym" value="{{ current_ym }}" style="width:150px; display:inline-block;">
            <button type="submit">Προβολή</button>
        </form>
        
        <div style="background:#e0f2fe; padding:10px; border-radius:4px; margin-bottom:15px; font-size:12px;">
            <b>Σύμβολα:</b> Μ = Μέρα | Ν = Νύχτα | SL = Sick Leave | Α = Άδεια Απουσίας | RD = Rest Day | T = Τρομοκρατία | Υ = Υπερωριακό | Π = Πλοίο | Ε = Εκπαίδευση | ΑΠ = Αποστολή
        </div>
        
        <form method="POST">
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
                    <td><input type="text" name="day_{{ d }}" value="{{ my_row.get(d, '') }}" style="width:35px; text-align:center; padding:2px;" maxlength="3"></td>
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
    """, users_list=users_list, roster_data=roster_data, current_ym=current_ym, my_row=roster_data.get(user_id, {}))

@app.route('/logbook/<channel_name>', methods=['GET', 'POST'])
def logbook(channel_name):
    if 'user_phone' not in session:
        return redirect(url_for('login'))
        
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, status, role FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        
        if not user or (user[2] != 'Approved' and user[3] != 'Admin'):
            return "Μη εξουσιοδοτημένη πρόσβαση."
            
        user_id, author_name, status, role = user
        
        cursor.execute("SELECT id FROM channels WHERE name = ?", (channel_name,))
        ch_row = cursor.fetchone()
        if not ch_row:
            return "Το κανάλι δεν βρέθηκε."
            
        ch_id = ch_row[0]
        
        if role != 'Admin':
            cursor.execute("SELECT 1 FROM channel_members WHERE channel_id = ? AND user_id = ?", (ch_id, user_id))
            if not cursor.fetchone():
                return render_template_string(BASE_STYLE + "<div class='container'><h3>Δεν έχετε εξουσιοδότηση πρόσβασης σε αυτό το κανάλι (Μητρώο).</h3><a href='/'>Αρχική</a></div>")

        saved_content = ""
        if request.method == 'POST':
            content = request.form.get('content', '')
            saved_content = content
            
            if channel_name == 'E-Καθήκοντα':
                admin_pass = request.form.get('admin_password', '')
                if not check_password_hash(get_current_admin_password_hash(), admin_pass):
                    flash('Σφάλμα: Απαιτείται έγκυρος Κωδικός Διαχειριστή για καταχώρηση στα E-Καθήκοντα.')
                    cursor.execute("SELECT author_name, content, filename, timestamp FROM logbook_entries WHERE channel_name = ? ORDER BY id DESC", (channel_name,))
                    entries = cursor.fetchall()
                    is_e_kathikonta = (channel_name == 'E-Καθήκοντα')
                    return render_template_string(BASE_STYLE + """
                    <div class="container">
                        <h2>Κανάλι / Logbook: {{ channel_name }}</h2>
                        <p><a href="/">Αρχική</a></p>
                        {% with messages = get_flashed_messages() %}
                            {% if messages %}
                                <div class="flash-box">{% for m in messages %}{{ m }}<br>{% endfor %}</div>
                            {% endif %}
                        {% endwith %}
                        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; border:1px solid #cbd5e1; border-radius:5px;">
                            {% if is_e_kathikonta %}
                                <p style="color:#1d4ed8; font-weight:bold;">⚠️ Τα E-Καθήκοντα απαιτούν Κωδικό Διαχειριστή για καταχώρηση/επισύναψη.</p>
                                Κωδικός Διαχειριστή: <input type="password" name="admin_password" required><br>
                            {% endif %}
                            <textarea name="content" placeholder="Καταχώρηση..." rows="3" required>{{ saved_content }}</textarea><br>
                            Επισύναψη Αρχείου: <input type="file" name="file"><br><br>
                            <button type="submit">Καταχώρηση</button>
                        </form>
                        <hr>
                        <h3>Ιστορικό Καταχωρήσεων</h3>
                        <ul>
                            {% for author, content, filename, time in entries %}
                                <li style="margin-bottom: 10px; background:#fff; padding:10px; border-radius:4px; border:1px solid #e2e8f0;">
                                    <b>[{{ time}}] {{ author }}:</b> {{ content }}
                                    {% if filename %}
                                        <br>📎 <i>Αρχείο:</i> <a href="/uploads/{{ filename }}" target="_blank">{{ filename }}</a>
                                    {% endif %}
                                </li>
                            {% else %}
                                <li>Δεν υπάρχουν καταχωρήσεις.</li>
                            {% endfor %}
                        </ul>
                    </div>
                    """, channel_name=channel_name, entries=entries, is_e_kathikonta=is_e_kathikonta, saved_content=saved_content)

            file = request.files.get('file')
            filename = None
            if file and file.filename != '':
                if allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    file.save(os.path.join(UPLOAD_FOLDER, filename))
                else:
                    flash('Μη επιτρεπόμενος τύπος αρχείου.')
                    
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("INSERT INTO logbook_entries (channel_name, author_name, content, filename, timestamp) VALUES (?, ?, ?, ?, ?)",
                           (channel_name, author_name, content, filename, timestamp))
            conn.commit()

            # Αποστολή Email Ειδοποίησης αποκλειστικά στα εξουσιοδοτημένα μέλη αυτού του καναλιού
            cursor.execute("""
                SELECT DISTINCT u.email FROM users u
                LEFT JOIN channel_members cm ON u.id = cm.user_id
                WHERE (cm.channel_id = ? OR u.role = 'Admin') AND u.status = 'Approved' AND u.email IS NOT NULL AND u.email != ''
            """, (ch_id,))
            recipients = [row[0] for row in cursor.fetchall()]
            email_subject = f"[LKN Channel] Νέα Ενημέρωση: {channel_name}"
            email_body = f"Υπηρεσιακή Ειδοποίηση:\n\nΥπάρχει νέα καταχώρηση στο επιχειρησιακό κανάλι '{channel_name}' από τον χρήστη {author_name}.\n\nΠεριεχόμενο:\n{content}"
            send_notification_email(recipients, email_subject, email_body)

            return redirect(url_for('logbook', channel_name=channel_name))
            
        cursor.execute("SELECT author_name, content, filename, timestamp FROM logbook_entries WHERE channel_name = ? ORDER BY id DESC", (channel_name,))
        entries = cursor.fetchall()
    
    is_e_kathikonta = (channel_name == 'E-Καθήκοντα')
    
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Κανάλι / Logbook: {{ channel_name }}</h2>
        <p><a href="/">Αρχική</a></p>
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                <div class="flash-box">{% for m in messages %}{{ m }}<br>{% endfor %}</div>
            {% endif %}
        {% endwith %}
        
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; border:1px solid #cbd5e1; border-radius:5px;">
            {% if is_e_kathikonta %}
                <p style="color:#1d4ed8; font-weight:bold;">⚠️ Τα E-Καθήκοντα απαιτούν Κωδικό Διαχειριστή για καταχώρηση/επισύναψη.</p>
                Κωδικός Διαχειριστή: <input type="password" name="admin_password" required><br>
            {% endif %}
            <textarea name="content" placeholder="Καταχώρηση..." rows="3" required>{{ saved_content }}</textarea><br>
            Επισύναψη Αρχείου: <input type="file" name="file"><br><br>
            <button type="submit">Καταχώρηση</button>
        </form>
        <hr>
        <h3>Ιστορικό Καταχωρήσεων</h3>
        <ul>
            {% for author, content, filename, time in entries %}
                <li style="margin-bottom: 10px; background:#fff; padding:10px; border-radius:4px; border:1px solid #e2e8f0;">
                    <b>[{{ time}}] {{ author }}:</b> {{ content }}
                    {% if filename %}
                        <br>📎 <i>Αρχείο:</i> <a href="/uploads/{{ filename }}" target="_blank">{{ filename }}</a>
                    {% endif %}
                </li>
            {% else %}
                <li>Δεν υπάρχουν καταχωρήσεις.</li>
            {% endfor %}
        </ul>
    </div>
    """, channel_name=channel_name, entries=entries, is_e_kathikonta=is_e_kathikonta, saved_content=saved_content)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    if 'user_phone' not in session:
        return redirect(url_for('login'))
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
        
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, role FROM users WHERE phone = ?", (session['user_phone'],))
        res = cursor.fetchone()
        
        if not res or res[1] != 'Admin':
            return "Μη εξουσιοδοτημένη πρόσβαση."
            
        admin_name = res[0]
        
        if request.method == 'POST':
            entered_pass = request.form.get('admin_password')
            current_hash = get_current_admin_password_hash()
            
            if check_password_hash(current_hash, entered_pass):
                action = request.form.get('action')
                if action == 'approve':
                    uid = request.form.get('user_id')
                    cursor.execute("UPDATE users SET status = 'Approved' WHERE id = ?", (uid,))
                    conn.commit()
                    flash("Ο χρήστης εγκρίθηκε επιτυχώς.")
                    log_audit(admin_name, f"Approved user ID: {uid}")
                elif action == 'revoke':
                    uid = request.form.get('user_id')
                    cursor.execute("UPDATE users SET status = 'Pending' WHERE id = ?", (uid,))
                    conn.commit()
                    flash("Η έγκριση αφαιρέθηκε.")
                    log_audit(admin_name, f"Revoked user ID: {uid}")
                elif action == 'toggle_roster':
                    new_state = '1' if not is_roster_active() else '0'
                    cursor.execute("UPDATE settings SET value = ? WHERE key = 'roster_enabled'", (new_state,))
                    conn.commit()
                    flash("Η κατάσταση του Roster ενημερώθηκε.")
                    log_audit(admin_name, f"Toggled Roster status to: {new_state}")
                elif action == 'create_channel':
                    new_ch_name = request.form.get('new_channel_name')
                    try:
                        cursor.execute("INSERT INTO channels (name, is_custom) VALUES (?, 1)", (new_ch_name,))
                        conn.commit()
                        flash("Το νέο κανάλι δημιουργήθηκε.")
                        log_audit(admin_name, f"Created channel: {new_ch_name}")
                    except sqlite3.IntegrityError:
                        flash("Το κανάλι υπάρχει ήδη.")
                elif action == 'delete_channel':
                    ch_id = request.form.get('channel_id')
                    cursor.execute("SELECT name, is_custom FROM channels WHERE id = ?", (ch_id,))
                    ch_row = cursor.fetchone()
                    if ch_row and ch_row[1] == 1:
                        ch_n = ch_row[0]
                        cursor.execute("DELETE FROM logbook_entries WHERE channel_name = ?", (ch_n,))
                        cursor.execute("DELETE FROM channel_members WHERE channel_id = ?", (ch_id,))
                        cursor.execute("DELETE FROM channels WHERE id = ?", (ch_id,))
                        conn.commit()
                        flash("Το κανάλι διαγράφηκε.")
                        log_audit(admin_name, f"Deleted channel: {ch_n}")
                    else:
                        flash("Δεν επιτρέπεται η διαγραφή των μόνιμων καναλιών.")
                elif action == 'add_member':
                    ch_id = request.form.get('channel_id')
                    uid = request.form.get('user_id')
                    try:
                        cursor.execute("INSERT INTO channel_members (channel_id, user_id) VALUES (?, ?)", (ch_id, uid))
                        conn.commit()
                        flash("Ο χρήστης προστέθηκε στο μητρώο.")
                        log_audit(admin_name, f"Added user ID {uid} to channel ID {ch_id}")
                    except sqlite3.IntegrityError:
                        flash("Ο χρήστης υπάρχει ήδη στο μητρώο.")
                elif action == 'remove_member':
                    ch_id = request.form.get('channel_id')
                    uid = request.form.get('user_id')
                    cursor.execute("DELETE FROM channel_members WHERE channel_id = ? AND user_id = ?", (ch_id, uid))
                    conn.commit()
                    flash("Ο χρήστης αφαιρέθηκε από το μητρώο.")
                    log_audit(admin_name, f"Removed user ID {uid} from channel ID {ch_id}")
                elif action == 'rename_channel':
                    ch_id = request.form.get('channel_id')
                    new_name = request.form.get('new_name')
                    cursor.execute("SELECT name FROM channels WHERE id = ?", (ch_id,))
                    old_row = cursor.fetchone()
                    if old_row:
                        old_name = old_row[0]
                        cursor.execute("UPDATE channels SET name = ? WHERE id = ?", (new_name, ch_id))
                        cursor.execute("UPDATE logbook_entries SET channel_name = ? WHERE channel_name = ?", (new_name, old_name))
                        conn.commit()
                        flash("Η μετονομασία ολοκληρώθηκε.")
                        log_audit(admin_name, f"Renamed channel from '{old_name}' to '{new_name}'")
                elif action == 'change_password':
                    new_pass = request.form.get('new_password')
                    if new_pass:
                        new_hashed = generate_password_hash(new_pass)
                        cursor.execute("UPDATE settings SET value = ? WHERE key = 'admin_password'", (new_hashed,))
                        conn.commit()
                        flash("Ο κωδικός διαχείρισης άλλαξε επιτυχώς.")
                        log_audit(admin_name, "Changed administrator password")
            else:
                flash("Λάθος κωδικός διαχειριστικής πρόσβασης.")
                log_audit(admin_name, "FAILED administrator authentication attempt")
            return redirect(url_for('admin'))
                
        cursor.execute("SELECT id, phone, name, station, email, status FROM users")
        users = cursor.fetchall()
        
        cursor.execute("SELECT id, name, is_custom FROM channels")
        channels_raw = cursor.fetchall()
        
        channel_rosters = {}
        for ch_id, ch_name, is_c in channels_raw:
            cursor.execute("""
                SELECT u.id, u.name, u.phone FROM users u
                JOIN channel_members cm ON u.id = cm.user_id
                WHERE cm.channel_id = ?
            """, (ch_id,))
            channel_rosters[ch_id] = cursor.fetchall()
    
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Κεντρικό Πάνελ Διαχείρισης</h2>
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                <div class="flash-box">{% for m in messages %}{{ m }}<br>{% endfor %}</div>
            {% endif %}
        {% endwith %}
        <p><a href="/">Επιστροφή στην Αρχική</a></p>
        
        <form method="POST" style="background:#f1f5f9; padding:15px; border:1px solid #cbd5e1; border-radius:5px;">
            <label><b>Εισάγετε τον Κωδικό Διαχειριστή για τις ενέργειες:</b></label><br>
            <input type="password" name="admin_password" placeholder="Κωδικός Διαχειριστή" required style="width:250px;"><br><br>
            
            <hr>
            <h3>🔑 Ενεργοποίηση Roster Βαρδιών</h3>
            <p>Κατάσταση Roster: <b>{% if roster_active %}ΕΝΕΡΓΟ{% else %}ΑΝΕΝΕΡΓΟ{% endif %}</b></p>
            <button type="submit" name="action" value="toggle_roster" style="background:{% if roster_active %}#dc2626{% else %}#16a34a{% endif %};">{% if roster_active %}Απενεργοποίηση Roster{% else %}Ενεργοποίηση Roster{% endif %}</button>

            <hr>
            <h3>1. Έλεγχος Μελών & Εγκρίσεων</h3>
            <table>
                <tr><th>Όνομα</th><th>Τηλέφωνο</th><th>Σταθμός</th><th>Email</th><th>Κατάσταση</th><th>Ενέργεια</th></tr>
                {% for u in users %}
                <tr>
                    <td>{{ u[2] }}</td><td>{{ u[1] }}</td><td>{{ u[3] }}</td><td>{{ u[4] if u[4] else '-' }}</td><td>{{ u[5] }}</td>
                    <td>
                        <input type="hidden" name="user_id" value="{{ u[0] }}">
                        {% if u[5] == 'Pending' %}
                            <button type="submit" name="action" value="approve">Έγκριση</button>
                        {% else %}
                            <button type="submit" name="action" value="revoke" style="background:#dc2626;">Αφαίρεση</button>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </table>

            <hr>
            <h3>2. Δημιουργία Νέου Καναλιού</h3>
            <input type="text" name="new_channel_name" placeholder="Όνομα Νέου Καναλιού" style="width:250px;">
            <button type="submit" name="action" value="create_channel">Δημιουργία</button>

            <hr>
            <h3>3. Διαχείριση & Μητρώο Πρόσβασης Καναλιών</h3>
            {% for ch_id, ch_name, is_custom in channels_raw %}
            <div style="border: 1px solid #cbd5e1; padding: 12px; margin-bottom: 15px; background:#fff; border-radius:4px;">
                <b>{{ ch_name }}</b><br><br>
                <input type="hidden" name="channel_id" value="{{ ch_id }}">
                Νέο Όνομα: <input type="text" name="new_name" value="{{ ch_name }}" style="width:200px; display:inline-block;">
                <button type="submit" name="action" value="rename_channel">Αλλαγή</button>
                {% if is_custom == 1 %}
                <button type="submit" name="action" value="delete_channel" style="background:#dc2626;" onclick="return confirm('Διαγραφή καναλιού;');">Διαγραφή</button>
                {% endif %}
                <p style="margin:10px 0 5px 0;"><b>Μητρώο Πρόσβασης Μελών:</b></p>
                <ul style="margin:0 0 10px 20px;">
                    {% for member in channel_rosters[ch_id] %}
                        <li>
                            {{ member[1] }} ({{ member[2] }}) 
                            <button type="submit" name="action" value="remove_member" formaction="/admin" formmethod="POST" style="padding:2px 6px; font-size:11px; background:#475569;" onclick="this.form.user_id.value='{{ member[0] }}'; this.form.channel_id.value='{{ ch_id }}';">Αφαίρεση</button>
                        </li>
                    {% else %}
                        <li>Κανένα μέλος.</li>
                    {% endfor %}
                </ul>
                Προσθήκη μέλους: 
                <select name="target_user_id_{{ ch_id }}" style="width:200px; display:inline-block;" onchange="document.getElementById('hidden_uid_{{ ch_id }}').value=this.value;">
                    <option value="">-- Επιλογή χρήστη --</option>
                    {% for u in users %}{% if u[5] == 'Approved' %}
                        <option value="{{ u[0] }}">{{ u[2] }} ({{ u[1] }})</option>
                    {% endif %}{% endfor %}
                </select>
                <input type="hidden" id="hidden_uid_{{ ch_id }}" name="user_id" value="">
                <button type="submit" name="action" value="add_member" onclick="this.form.channel_id.value='{{ ch_id }}';">Προσθήκη</button>
            </div>
            {% endfor %}

            <hr>
            <h3>4. Αλλαγή Κωδικού Διαχείρισης</h3>
            Νέος Κωδικός: <input type="password" name="new_password" placeholder="Νέος Κωδικός" style="width:200px; display:inline-block;">
            <button type="submit" name="action" value="change_password">Αλλαγή</button>
        </form>
    </div>
    """, users=users, channels_raw=channels_raw, channel_rosters=channel_rosters, roster_active=is_roster_active())

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == "__main__":
    app.run(debug=False)