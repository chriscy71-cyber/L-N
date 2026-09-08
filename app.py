import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import uuid

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
            password TEXT
        )
        """)
        
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN password TEXT;")
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
        
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password', ?)", (generate_password_hash(DEFAULT_ADMIN_PASSWORD),))
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('roster_enabled', '0')",)
        
        initial_channels = [
            'Ναυτικός Σταθμός',
            'Μαρίνα',
            'Λιμάνι',
            'Τεχνικοί',
            'Κυβερνήτες Α\'',
            'Διοίκηση',
            'Αρχείο',
            'E-Καθήκοντα',
            'Ψηφιακή Βιβλιοθήκη',
            'Roster Βαρδιών'
        ]
        
        for ch in initial_channels:
            cursor.execute("INSERT OR IGNORE INTO channels (name, is_custom) VALUES (?, 0)", (ch,))
        
        conn.commit()

init_db()

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
        background: linear-gradient(rgba(0, 0, 0, 0.4), rgba(0, 0, 0, 0.4)), 
                    url('https://images.unsplash.com/photo-1570481662006-a1c2e77b0d4d?auto=format&fit=crop&w=1920&q=80') no-repeat center center fixed;
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
</style>
"""

@app.route('/')
def index():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status, name, role, password FROM users WHERE phone = ?", (session['user_phone'],))
        user = cursor.fetchone()
        
        if not user:
            session.clear()
            return redirect(url_for('login'))
            
        user_id, status, name, role, password = user
        
        if not password:
            return redirect(url_for('set_password'))
            
        if status != 'Approved' and role != 'Admin':
            return render_template_string(BASE_STYLE + """
            <div class='container'>
                <h3>Ο λογαριασμός σας αναμένει έγκριση από τον Διαχειριστή.</h3>
                <div class="nav-buttons">
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
    
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Καλώς ορίσατε, {{ name }}</h2>
        <div class="nav-buttons">
            <a href="/admin" class="btn-link">Κεντρικό Πάνελ Διαχείρισης</a>
            <a href="/library" class="btn-link">Ψηφιακή Βιβλιοθήκη</a>
            {% if roster_active or role == 'Admin' %}
                <a href="/roster" class="btn-link">Roster Βαρδιών (XL)</a>
            {% endif %}
            <a href="/set_password" class="btn-link btn-warning">Αλλαγή Κωδικού</a>
            <a href="/logout" class="btn-link btn-danger">Αποσύνδεση</a>
        </div>
        <hr>
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
    error_msg = ""
    if request.method == 'POST':
        phone = request.form.get('phone')
        password = request.form.get('password', '')
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT phone, password FROM users WHERE phone = ?", (phone,))
            user = cursor.fetchone()
        
        if user:
            db_pass = user['password']
            session['user_phone'] = phone
            if not db_pass:
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
        if new_pass and new_pass == confirm_pass:
            hashed_pass = generate_password_hash(new_pass)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET password = ? WHERE phone = ?", (hashed_pass, session['user_phone']))
                conn.commit()
            return redirect(url_for('index'))
        else:
            msg = "Οι κωδικοί δεν ταιριάζουν ή είναι κενοί."
            
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width: 400px; margin-top: 50px;">
        <h2>Ορισμός / Αλλαγή Κωδικού Πρόσβασης</h2>
        <p style="color:#1d4ed8; font-weight:bold;">⚠️ Υποχρεωτικός ορισμός κωδικού πρόσβασης.</p>
        <p style="color:red;">{{ msg }}</p>
        <form method="POST">
            Νέος Κωδικός: <input type="password" name="new_password" required><br>
            Επιβεβαίωση Νέου Κωδικού: <input type="password" name="confirm_password" required><br><br>
            <button type="submit" style="width:100%;">Αποθήκευση Κωδικού</button>
        </form>
        <div style="margin-top:15px; text-align:center;">
            <a href="/" class="btn-link" style="width:100%; box-sizing:border-box; background:#475569;">Επιστροφή</a>
        </div>
    </div>
    """, msg=msg)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        phone = request.form.get('phone')
        name = request.form.get('name')
        station = request.form.get('station')
        
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users")
                user_count = cursor.fetchone()[0]
                
                if user_count == 0:
                    cursor.execute("INSERT INTO users (phone, name, station, status, role, password) VALUES (?, ?, ?, 'Approved', 'Admin', NULL)",
                                   (phone, name, station))
                else:
                    cursor.execute("INSERT INTO users (phone, name, station, status, role, password) VALUES (?, ?, ?, 'Pending', 'User', NULL)",
                                   (phone, name, station))
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
            Σταθμός: <input type="text" name="station" required><br><br>
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
                file.save(os.path.join(UPLOAD_FOLDER, unique_filename))
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute("INSERT INTO library (title, category, filename, uploader, timestamp) VALUES (?, ?, ?, ?, ?)",
                               (title, category, unique_filename, author_name, timestamp))
                conn.commit()
        
        cursor.execute("SELECT title, category, filename, uploader, timestamp FROM library ORDER BY id DESC")
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
            <tr><th>Τίτλος</th><th>Κατηγορία</th><th>Ανάρτηση Από</th><th>Ημερομηνία</th><th>Λήψη</th></tr>
            {% for title, cat, fname, up, time in items %}
            <tr>
                <td><b>{{ title }}</b></td>
                <td>{{ cat }}</td>
                <td>{{ up }}</td>
                <td>{{ time }}</td>
                <td><a href="/uploads/{{ fname }}" target="_blank" class="btn-link" style="padding:4px 8px; font-size:11px;">📥 Λήψη</a></td>
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
            return render_template_string(BASE_STYLE + """
            <div class='container'>
                <h3>Το Roster Βαρδιών δεν είναι ακόμη ενεργό.</h3>
                <div class="nav-buttons"><a href='/' class="btn-link" style="background:#475569;">Αρχική</a></div>
            </div>
            """)
        
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
        <div class="nav-buttons">
            <a href="/" class="btn-link" style="background:#475569;">Αρχική</a>
        </div>
        
        <form method="GET" style="margin-bottom:15px; display:flex; gap:10px; align-items:flex-end;">
            <div>
                Επιλογή Μήνα (ΕΕΕΕ-ΜΜ): <input type="text" name="ym" value="{{ current_ym }}" style="width:150px;">
            </div>
            <div>
                <button type="submit">Προβολή</button>
            </div>
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
                return render_template_string(BASE_STYLE + """
                <div class='container'>
                    <h3>Δεν έχετε εξουσιοδότηση πρόσβασης σε αυτό το κανάλι (Μητρώο).</h3>
                    <div class="nav-buttons"><a href='/' class="btn-link" style="background:#475569;">Αρχική</a></div>
                </div>
                """)

        if request.method == 'POST':
            if channel_name == 'E-Καθήκοντα':
                admin_pass = request.form.get('admin_password', '')
                if not check_password_hash(get_current_admin_password_hash(), admin_pass):
                    return render_template_string(BASE_STYLE + """
                    <div class='container'>
                        <h3 style='color:red;'>Σφάλμα: Απαιτείται έγκυρος Κωδικός Διαχειριστή για καταχώρηση στα E-Καθήκοντα.</h3>
                        <div class="nav-buttons"><a href='/logbook/E-Καθήκοντα' class="btn-link" style="background:#475569;">Επιστροφή</a></div>
                    </div>
                    """)

            content = request.form.get('content', '')
            file = request.files.get('file')
            filename = None
            
            if file and file.filename != '':
                raw_filename = secure_filename(file.filename)
                filename = f"{uuid.uuid4().hex}_{raw_filename}"
                file.save(os.path.join(UPLOAD_FOLDER, filename))
                
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute("INSERT INTO logbook_entries (channel_name, author_name, content, filename, timestamp) VALUES (?, ?, ?, ?, ?)",
                           (channel_name, author_name, content, filename, timestamp))
            conn.commit()
            
        cursor.execute("SELECT author_name, content, filename, timestamp FROM logbook_entries WHERE channel_name = ? ORDER BY id DESC", (channel_name,))
        entries = cursor.fetchall()
    
    is_e_kathikonta = (channel_name == 'E-Καθήκοντα')
    
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Κανάλι / Logbook: {{ channel_name }}</h2>
        <div class="nav-buttons">
            <a href="/" class="btn-link" style="background:#475569;">Αρχική</a>
        </div>
        
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; border:1px solid #cbd5e1; border-radius:5px;">
            {% if is_e_kathikonta %}
                <p style="color:#1d4ed8; font-weight:bold;">⚠️ Τα E-Καθήκοντα απαιτούν Κωδικό Διαχειριστή για καταχώρηση/επισύναψη.</p>
                Κωδικός Διαχειριστή: <input type="password" name="admin_password" required><br>
            {% endif %}
            <textarea name="content" placeholder="Καταχώρηση..." rows="3" required></textarea><br>
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
                        <br>📎 <i>Αρχείο:</i> <a href="/uploads/{{ filename }}" target="_blank" class="btn-link" style="padding:2px 6px; font-size:11px; margin-top:5px;">📥 Λήψη Αρχείου</a>
                    {% endif %}
                </li>
            {% else %}
                <li>Δεν υπάρχουν καταχωρήσεις.</li>
            {% endfor %}
        </ul>
    </div>
    """, channel_name=channel_name, entries=entries, is_e_kathikonta=is_e_kathikonta)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'user_phone' not in session:
        return redirect(url_for('login'))
        
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role FROM users WHERE phone = ?", (session['user_phone'],))
        res = cursor.fetchone()
        
        if not res or res[0] != 'Admin':
            return "Μη εξουσιοδοτημένη πρόσβαση."
            
        msg = ""
        if request.method == 'POST':
            entered_pass = request.form.get('admin_password')
            current_pass_hash = get_current_admin_password_hash()
            
            if check_password_hash(current_pass_hash, entered_pass):
                action = request.form.get('action')
                if action == 'approve':
                    uid = request.form.get('user_id')
                    cursor.execute("UPDATE users SET status = 'Approved' WHERE id = ?", (uid,))
                    conn.commit()
                    msg = "Ο χρήστης εγκρίθηκε επιτυχώς."
                elif action == 'revoke':
                    uid = request.form.get('user_id')
                    cursor.execute("UPDATE users SET status = 'Pending' WHERE id = ?", (uid,))
                    conn.commit()
                    msg = "Η έγκριση αφαιρέθηκε."
                elif action == 'reset_password':
                    uid = request.form.get('user_id')
                    cursor.execute("UPDATE users SET password = NULL WHERE id = ?", (uid,))
                    conn.commit()
                    msg = "Ο κωδικός του χρήστη μηδενίστηκε επιτυχώς."
                elif action == 'toggle_roster':
                    new_state = '1' if not is_roster_active() else '0'
                    cursor.execute("UPDATE settings SET value = ? WHERE key = 'roster_enabled'", (new_state,))
                    conn.commit()
                    msg = "Η κατάσταση του Roster ενημερώθηκε."
                elif action == 'create_channel':
                    new_ch_name = request.form.get('new_channel_name')
                    try:
                        cursor.execute("INSERT INTO channels (name, is_custom) VALUES (?, 1)", (new_ch_name,))
                        conn.commit()
                        msg = "Το νέο κανάλι δημιουργήθηκε."
                    except sqlite3.IntegrityError:
                        msg = "Το κανάλι υπάρχει ήδη."
                elif action == 'delete_channel':
                    ch_id = request.form.get('channel_id')
                    cursor.execute("SELECT name, is_custom FROM channels WHERE id = ?", (ch_id,))
                    ch_row = cursor.fetchone()
                    if ch_row and ch_row[1] == 1:
                        cursor.execute("DELETE FROM logbook_entries WHERE channel_name = ?", (ch_row[0],))
                        cursor.execute("DELETE FROM channel_members WHERE channel_id = ?", (ch_id,))
                        cursor.execute("DELETE FROM channels WHERE id = ?", (ch_id,))
                        conn.commit()
                        msg = "Το κανάλι διαγράφηκε."
                    else:
                        msg = "Δεν επιτρέπεται η διαγραφή των μόνιμων καναλιών."
                elif action == 'add_member':
                    ch_id = request.form.get('channel_id')
                    uid = request.form.get(f'target_user_id_{ch_id}')
                    if uid:
                        try:
                            cursor.execute("INSERT INTO channel_members (channel_id, user_id) VALUES (?, ?)", (ch_id, uid))
                            conn.commit()
                            msg = "Ο χρήστης προστέθηκε στο μητρώο."
                        except sqlite3.IntegrityError:
                            msg = "Ο χρήστης υπάρχει ήδη στο μητρώο."
                elif action == 'remove_member':
                    ch_id = request.form.get('channel_id')
                    uid = request.form.get('user_id')
                    cursor.execute("DELETE FROM channel_members WHERE channel_id = ? AND user_id = ?", (ch_id, uid))
                    conn.commit()
                    msg = "Ο χρήστης αφαιρέθηκε από το μητρώο."
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
                        msg = "Η μετονομασία ολοκληρώθηκε."
                elif action == 'change_password':
                    new_pass = request.form.get('new_password')
                    if new_pass:
                        hashed_admin_pass = generate_password_hash(new_pass)
                        cursor.execute("UPDATE settings SET value = ? WHERE key = 'admin_password'", (hashed_admin_pass,))
                        conn.commit()
                        msg = "Ο κωδικός διαχείρισης άλλαξε επιτυχώς."
            else:
                msg = "Λάθος κωδικός διαχειριστικής πρόσβασης."
                
        cursor.execute("SELECT id, phone, name, station, status FROM users")
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
            
        cursor.execute("SELECT channel_name, author_name, content, filename, timestamp FROM logbook_entries ORDER BY id DESC")
        all_logbook_entries = cursor.fetchall()
    
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
            <h3>🔑 Ενεργοποίηση Roster Βαρδιών</h3>
            <p>Κατάσταση Roster: <b>{% if roster_active %}ΕΝΕΡΓΟ{% else %}ΑΝΕΝΕΡΓΟ{% endif %}</b></p>
            <button type="submit" name="action" value="toggle_roster" class="{% if roster_active %}btn-danger{% else %}btn-success{% endif %}">{% if roster_active %}Απενεργοποίηση Roster{% else %}Ενεργοποίηση Roster{% endif %}</button>
        </form>

        <hr>
        <h3>1. Έλεγχος Μελών, Εγκρίσεων & Reset Κωδικού</h3>
        <table>
            <tr><th>Όνομα</th><th>Τηλέφωνο</th><th>Σταθμός</th><th>Κατάσταση</th><th>Ενέργειες</th></tr>
            {% for u in users %}
            <tr>
                <td>{{ u[2] }}</td><td>{{ u[1] }}</td><td>{{ u[3] }}</td><td>{{ u[4] }}</td>
                <td>
                    <form method="POST" style="display:inline;">
                        <input type="hidden" name="admin_password" value="" id="pwd_action_{{ u[0] }}">
                        <input type="hidden" name="user_id" value="{{ u[0] }}">
                        {% if u[4] == 'Pending' %}
                            <button type="submit" name="action" value="approve" class="btn-success" style="padding:4px 8px; font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Διαχειριστή:');">Έγκριση</button>
                        {% else %}
                            <button type="submit" name="action" value="revoke" class="btn-danger" style="padding:4px 8px; font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Διαχειριστή:');">Αφαίρεση</button>
                        {% endif %}
                        <button type="submit" name="action" value="reset_password" class="btn-warning" style="padding:4px 8px; font-size:11px;" onclick="this.form.admin_password.value=prompt('Κωδικός Διαχειριστή:'); return confirm('Μηδενισμός κωδικού χρήστη;');">Reset Κωδικού</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>

        <hr>
        <h3>2. Δημιουργία Νέου Καναλιού</h3>
        <form method="POST">
            <input type="hidden" name="admin_password" id="pwd_create_channel">
            <input type="text" name="new_channel_name" placeholder="Όνομα Νέου Καναλιού" style="width:250px; display:inline-block;" required>
            <button type="submit" name="action" value="create_channel" onclick="document.getElementById('pwd_create_channel').value=prompt('Κωδικός Διαχειριστή:');">Δημιουργία</button>
        </form>

        <hr>
        <h3>3. Διαχείριση & Μητρώο Πρόσβασης Καναλιών</h3>
        {% for ch_id, ch_name, is_custom in channels_raw %}
        <div style="border: 1px solid #cbd5e1; padding: 12px; margin-bottom: 15px; background:#fff; border-radius:4px;">
            <b>{{ ch_name }}</b><br><br>
            <form method="POST">
                <input type="hidden" name="admin_password" id="pwd_ch_{{ ch_id }}">
                <input type="hidden" name="channel_id" value="{{ ch_id }}">
                Νέο Όνομα: <input type="text" name="new_name" value="{{ ch_name }}" style="width:200px; display:inline-block;">
                <button type="submit" name="action" value="rename_channel" onclick="document.getElementById('pwd_ch_{{ ch_id }}').value=prompt('Κωδικός Διαχειριστή:');">Αλλαγή</button>
                {% if is_custom == 1 %}
                <button type="submit" name="action" value="delete_channel" class="btn-danger" onclick="document.getElementById('pwd_ch_{{ ch_id }}').value=prompt('Κωδικός Διαχειριστή:'); return confirm('Διαγραφή καναλιού;');">Διαγραφή</button>
                {% endif %}
            </form>
            <p style="margin:10px 0 5px 0;"><b>Μητρώο Πρόσβασης Μελών:</b></p>
            <ul style="margin:0 0 10px 20px;">
                {% for member in channel_rosters[ch_id] %}
                    <li>
                        {{ member[1] }} ({{ member[2] }}) 
                        <form method="POST" style="display:inline;">
                            <input type="hidden" name="admin_password" id="pwd_rem_{{ ch_id }}_{{ member[0] }}">
                            <input type="hidden" name="channel_id" value="{{ ch_id }}">
                            <input type="hidden" name="user_id" value="{{ member[0] }}">
                            <button type="submit" name="action" value="remove_member" style="padding:2px 6px; font-size:11px;" class="btn-danger" onclick="document.getElementById('pwd_rem_{{ ch_id }}_{{ member[0] }}').value=prompt('Κωδικός Διαχειριστή:');">Αφαίρεση</button>
                        </form>
                    </li>
                {% else %}
                    <li>Κανένα μέλος.</li>
                {% endfor %}
            </ul>
            <form method="POST">
                <input type="hidden" name="admin_password" id="pwd_add_{{ ch_id }}">
                <input type="hidden" name="channel_id" value="{{ ch_id }}">
                Προσθήκη μέλους: 
                <select name="target_user_id_{{ ch_id }}" style="width:200px; display:inline-block;" required>
                    <option value="">-- Επιλογή χρήστη --</option>
                    {% for u in users %}{% if u[4] == 'Approved' %}
                        <option value="{{ u[0] }}">{{ u[2] }} ({{ u[1] }})</option>
                    {% endif %}{% endfor %}
                </select>
                <button type="submit" name="action" value="add_member" onclick="document.getElementById('pwd_add_{{ ch_id }}').value=prompt('Κωδικός Διαχειριστή:');">Προσθήκη</button>
            </form>
        </div>
        {% endfor %}

        <hr>
        <h3>4. Συνολικό Μητρώο Καταχωρήσεων (Όλα τα Κανάλια)</h3>
        <table>
            <tr><th>Κανάλι</th><th>Συντάκτης</th><th>Περιεχόμενο</th><th>Αρχείο</th><th>Ημερομηνία</th></tr>
            {% for ch_n, author, content, fname, time in all_logbook_entries %}
            <tr>
                <td><b>{{ ch_n }}</b></td>
                <td>{{ author }}</td>
                <td>{{ content }}</td>
                <td>{% if fname %}<a href="/uploads/{{ fname }}" target="_blank" class="btn-link" style="padding:2px 6px; font-size:11px;">📥 Λήψη</a>{% else %}-{% endif %}</td>
                <td>{{ time }}</td>
            </tr>
            {% else %}
            <tr><td colspan="5">Δεν υπάρχουν καταχωρήσεις στο σύστημα.</td></tr>
            {% endfor %}
        </table>

        <hr>
        <h3>5. Αλλαγή Κωδικού Διαχείρισης</h3>
        <form method="POST">
            <input type="hidden" name="admin_password" id="pwd_change_admin">
            Νέος Κωδικός: <input type="password" name="new_password" placeholder="Νέος Κωδικός" style="width:200px; display:inline-block;" required>
            <button type="submit" name="action" value="change_password" onclick="document.getElementById('pwd_change_admin').value=prompt('Τρέχων Κωδικός Διαχειριστή:');">Αλλαγή</button>
        </form>
    </div>
    """, users=users, channels_raw=channels_raw, channel_rosters=channel_rosters, all_logbook_entries=all_logbook_entries, msg=msg, roster_active=is_roster_active())

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)