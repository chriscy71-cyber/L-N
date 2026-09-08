```python
import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, session, send_file, flash
import openpyxl
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'lkn_ast_larnakas_secure_key_2026')

UPLOAD_FOLDER = 'uploads'
EXCEL_FOLDER = 'excel_store'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXCEL_FOLDER, exist_ok=True)

DB_NAME = 'lkn_secure_mobile.db'
ADMIN_ACTION_PASSWORD = "LKN_ADMIN_2026"  # Υπηρεσιακός κωδικός ασφαλείας για κρίσιμες ενέργειες

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            station TEXT NOT NULL,
            role TEXT DEFAULT 'User'
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            max_members INTEGER,
            is_special INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_name TEXT NOT NULL,
            sender_phone TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            content TEXT,
            image_url TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            boat_name TEXT NOT NULL,
            sender_phone TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            entry TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logbook_permissions (
            boat_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            PRIMARY KEY (boat_name, phone)
        )
    ''')

    channels = [
        ('Γενικό Κανάλι', 80, 0),
        ('Ναυτικός Σταθμός', 50, 0),
        ('Λιμενικός Σταθμός', 10, 0),
        ('Λιμενικό Μαρίνας', 10, 0),
        ('Διοίκηση', 0, 1),
        ('Μηχανικοί & Ηλεκτρολόγοι', 0, 1),
        ('Κυβερνήτες Α Κατηγορίας', 0, 1),
        ('Αρχείο', 0, 1),
        ('Υπεύθυνοι Σταθμών & Βραδιών', 0, 1)
    ]
    for ch, mx, sp in channels:
        cursor.execute("INSERT OR IGNORE INTO channels (name, max_members, is_special) VALUES (?, ?, ?)", (ch, mx, sp))
        
    boats = [f"Σκάφος 0{i}" if i<10 else f"Σκάφος {i}" for i in range(1, 11)]
    for b in boats:
        cursor.execute("INSERT OR IGNORE INTO channels (name, max_members, is_special) VALUES (?, ?, ?)", (f"LogBook_{b}", 0, 1))

    conn.commit()
    conn.close()

init_db()

def cleanup_old_messages():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("DELETE FROM messages WHERE timestamp < ?", (thirty_days_ago,))
    conn.commit()
    conn.close()

@app.before_request
def before_request():
    cleanup_old_messages()

MOBILE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="el">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>ΛκΝ Αστ Λάρνακας</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #f0f2f5; margin: 0; padding: 0; color: #1c1e21; }
        header { background-color: #1b365d; color: white; padding: 12px 15px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 1000; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        header h1 { margin: 0; font-size: 18px; }
        .mobile-container { max-width: 600px; margin: 0 auto; padding: 10px; padding-bottom: 70px; }
        .card { background: white; padding: 15px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 12px; }
        .btn { background-color: #1b365d; color: white; border: none; padding: 12px; border-radius: 6px; cursor: pointer; font-weight: bold; width: 100%; font-size: 16px; text-align: center; display: block; text-decoration: none; margin-top: 8px; }
        .btn:active { background-color: #2c4d7e; }
        input, select, textarea { width: 100%; padding: 12px; margin: 6px 0 12px 0; border: 1px solid #ccd0d5; border-radius: 6px; font-size: 16px; background: #fff; }
        .chat-box { height: calc(100vh - 280px); min-height: 250px; border: 1px solid #ddd; border-radius: 6px; padding: 10px; overflow-y: scroll; background: #fafafa; margin-bottom: 10px; }
        .message { margin-bottom: 10px; border-bottom: 1px solid #eee; padding-bottom: 6px; font-size: 14px; }
        .message .meta { font-size: 11px; color: #65676b; margin-bottom: 2px; }
        .menu-grid { display: grid; grid-template-columns: 1fr; gap: 10px; margin-top: 10px; }
        .menu-btn { background: #fff; border: 1px solid #1b365d; color: #1b365d; padding: 15px; border-radius: 8px; text-align: center; text-decoration: none; font-weight: bold; font-size: 15px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
        .badge { font-size: 12px; background: #e4e6eb; padding: 3px 8px; border-radius: 12px; color: #4b4f56; display: inline-block; margin-top: 4px; }
    </style>
</head>
<body>
    <header>
        <h1>ΛκΝ Αστ Λάρνακας</h1>
        <div>
            {% if session.get('phone') %}
                <a href="{{ url_for('logout') }}" style="color: #ffcccc; text-decoration: none; font-size: 14px; font-weight: bold;">Έξοδος</a>
            {% endif %}
        </div>
    </header>
    <div class="mobile-container">
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                <div style="background: #e7f3ff; color: #1877f2; padding: 10px; border-radius: 6px; margin-bottom: 10px; font-size: 14px;">
                    {{ messages[0] }}
                </div>
            {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </div>
</body>
</html>
'''

LOGIN_PAGE = MOBILE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
<div class="card" style="margin-top: 20px;">
    <h2>Ταυτοποίηση Στελέχους</h2>
    <form method="POST">
        <label>Αριθμός Κινητού:</label>
        <input type="tel" name="phone" placeholder="π.χ. 99123456" required>
        <label>Ονοματεπώνυμο:</label>
        <input type="text" name="name" placeholder="π.χ. Αστυνόμος Χ. Παναγή" required>
        <label>Σταθμός / Ομάδα:</label>
        <select name="station" required>
            <option value="Γενικό">Γενικό Κανάλι (Όλοι - 80)</option>
            <option value="Ναυτικός Σταθμός">Ναυτικός Σταθμός (50)</option>
            <option value="Λιμενικός Σταθμός">Λιμενικός Σταθμός (10)</option>
            <option value="Λιμενικό Μαρίνας">Λιμενικό Μαρίνας (10)</option>
        </select>
        <button type="submit" class="btn">Είσοδος στην Εφαρμογή</button>
    </form>
</div>
''')

INDEX_PAGE = MOBILE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
<div class="card">
    <div style="font-size: 14px; color: #65676b;">Χρήστης: <b>{{ session.get('name') }}</b></div>
    <div class="badge">Σταθμός: {{ session.get('station') }}</div>
    <div class="menu-grid" style="margin-top: 15px;">
        <a href="{{ url_for('channels_list') }}" class="menu-btn">💬 Κανάλια & Αίθουσες Επικοινωνίας</a>
        <a href="{{ url_for('logbooks_list') }}" class="menu-btn">⚓ Ψηφιακά Log Books Σκαφών (1-10)</a>
        <a href="{{ url_for('excel_management') }}" class="menu-btn">📊 Ηλεκτρονική Διοίκηση (Excel)</a>
        <a href="{{ url_for('admin_panel') }}" class="menu-btn" style="border-color: #d9534f; color: #d9534f;">⚙️ Διαχείριση & Αλλαγές (PIN)</a>
    </div>
</div>
''')

CHANNELS_PAGE = MOBILE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
<div class="card">
    <a href="{{ url_for('index') }}" style="color: #1b365d; text-decoration: none; font-weight: bold; font-size: 14px; display: inline-block; margin-bottom: 10px;">← Επιστροφή</a>
    <h3 style="margin-top: 0;">Κανάλια & Αίθουσες</h3>
    <div style="display: flex; gap: 6px; overflow-x: auto; padding-bottom: 8px; margin-bottom: 10px;">
        {% for ch in channels %}
            <a href="{{ url_for('chat_room', channel_name=ch[1]) }}" style="white-space: nowrap; padding: 6px 12px; border-radius: 15px; font-size: 13px; text-decoration: none; border: 1px solid #1b365d; {% if current_channel == ch[1] %}background: #1b365d; color: white;{% else %}background: white; color: #1b365d;{% endif %}">
                {{ ch[1] }}
            </a>
        {% endfor %}
    </div>
    {% if current_channel %}
        <div style="border-top: 1px solid #eee; padding-top: 10px;">
            <div style="font-size: 12px; color: #888; margin-bottom: 6px;">Κανάλι: <b>{{ current_channel }}</b> (Διαγραφή μηνυμάτων στις 30 ημέρες)</div>
            <div class="chat-box" id="chatBox">
                {% for m in messages %}
                    <div class="message">
                        <div class="meta"><b>{{ m[3] }}</b> - {{ m[5] }}</div>
                        <div>{{ m[4] }}</div>
                        {% if m[6] %}
                            <div style="margin-top: 4px;"><img src="{{ url_for('static_file', filename=m[6]) }}" style="max-width: 150px; border-radius: 4px;"></div>
                        {% endif %}
                    </div>
                {% endfor %}
            </div>
            <form method="POST" enctype="multipart/form-data">
                <input type="text" name="content" placeholder="Μήνυμα..." autocomplete="off" style="margin-bottom: 6px;">
                <div style="display: flex; gap: 6px;">
                    <input type="file" name="image" accept="image/*" style="padding: 6px; margin: 0; font-size: 13px; flex: 1;">
                    <button type="submit" class="btn" style="margin: 0; width: 90px; padding: 8px;">Αποστολή</button>
                </div>
            </form>
        </div>
    {% endif %}
</div>
<script>
    var chatBox = document.getElementById("chatBox");
    if(chatBox) { chatBox.scrollTop = chatBox.scrollHeight; }
</script>
''')

LOGBOOKS_PAGE = MOBILE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
<div class="card">
    <a href="{{ url_for('index') }}" style="color: #1b365d; text-decoration: none; font-weight: bold; font-size: 14px; display: inline-block; margin-bottom: 10px;">← Επιστροφή</a>
    <h3 style="margin-top: 0;">Log Books Σκαφών (1-10)</h3>
    <p style="font-size: 13px; color: #65676b;">Μόνιμο αρχείο καταγραφής (απαλλάσσεται από διαγραφή 30 ημερών).</p>
    <div style="display: flex; gap: 6px; overflow-x: auto; padding-bottom: 8px; margin-bottom: 10px;">
        {% for b in boats %}
            <a href="{{ url_for('view_logbook', boat_name=b) }}" style="white-space: nowrap; padding: 6px 12px; border-radius: 15px; font-size: 13px; text-decoration: none; border: 1px solid #1b365d; {% if current_boat == b %}background: #1b365d; color: white;{% else %}background: white; color: #1b365d;{% endif %}">
                ⚓ {{ b }}
            </a>
        {% endfor %}
    </div>
    {% if current_boat %}
        <div style="border-top: 1px solid #eee; padding-top: 10px;">
            <h4 style="margin: 0 0 8px 0;">{{ current_boat }}</h4>
            <form method="POST">
                <label style="font-size: 12px;">Υπηρεσιακός Κωδικός Ενέργειας (PIN):</label>
                <input type="password" name="action_pin" placeholder="PIN εγγραφής..." required style="padding: 8px; margin-bottom: 8px;">
                <textarea name="entry" rows="2" placeholder="Καταχώριση συμβάντος / ωρών..." required></textarea>
                <button type="submit" class="btn" style="padding: 10px;">Καταχώριση με PIN</button>
            </form>
            <div style="max-height: 250px; overflow-y: scroll; margin-top: 10px; border: 1px solid #ddd; padding: 8px; border-radius: 6px; background: #fff;">
                {% for entry in entries %}
                    <div style="border-bottom: 1px solid #eee; padding: 6px 0; font-size: 13px;">
                        <div style="font-size: 10px; color: #666;"><b>{{ entry[3] }}</b> - {{ entry[4] }}</div>
                        <div style="white-space: pre-wrap; margin-top: 2px;">{{ entry[5] }}</div>
                    </div>
                {% endfor %}
            </div>
        </div>
    {% endif %}
</div>
''')

EXCEL_PAGE = MOBILE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
<div class="card">
    <a href="{{ url_for('index') }}" style="color: #1b365d; text-decoration: none; font-weight: bold; font-size: 14px; display: inline-block; margin-bottom: 10px;">← Επιστροφή</a>
    <h3 style="margin-top: 0;">📊 Ηλεκτρονική Διοίκηση</h3>
    <p style="font-size: 13px; color: #65676b;">Απαίτηση εισαγωγής PIN για ανάρτηση/τροποποίηση κεντρικού αρχείου Excel (κλείδωμα τύπων).</p>
    <div style="background: #e2f0d9; padding: 10px; border-radius: 6px; margin-bottom: 10px;">
        <h4 style="margin: 0 0 6px 0; font-size: 14px;">Ανάρτηση Master Excel</h4>
        <form method="POST" enctype="multipart/form-data">
            <label style="font-size: 12px;">Κωδικός Διοίκησης (PIN):</label>
            <input type="password" name="action_pin" placeholder="PIN ανάρτησης..." required style="padding: 8px; margin-bottom: 8px;">
            <input type="file" name="excel_file" accept=".xlsx" required style="font-size: 13px; padding: 6px;">
            <button type="submit" class="btn" style="background-color: #28a745; padding: 10px; font-size: 14px;">Ανάρτηση & Κλείδωμα Τύπων</button>
        </form>
    </div>
    <div>
        <h4 style="margin: 10px 0 6px 0; font-size: 14px;">Διαθέσιμα Αρχεία</h4>
        {% if excel_files %}
            {% for f in excel_files %}
                <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px; border: 1px solid #ddd; border-radius: 6px; margin-bottom: 6px; font-size: 13px;">
                    <span><b>{{ f }}</b></span>
                    <a href="{{ url_for('download_excel', filename=f) }}" class="btn" style="width: auto; padding: 6px 12px; font-size: 12px; margin: 0;">📥 Λήψη</a>
                </div>
            {% endfor %}
        {% else %}
            <p style="font-size: 13px; color: #888;">Δεν υπάρχουν διαθέσιμα αρχεία.</p>
        {% endif %}
    </div>
</div>
''')

ADMIN_PAGE = MOBILE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
<div class="card">
    <a href="{{ url_for('index') }}" style="color: #1b365d; text-decoration: none; font-weight: bold; font-size: 14px; display: inline-block; margin-bottom: 10px;">← Επιστροφή</a>
    <h3 style="margin-top: 0;">⚙️ Διαχείριση Προσωπικού & Ομάδων</h3>
    <form method="POST">
        <label style="font-size: 12px;">Κωδικός Διοίκησης (PIN):</label>
        <input type="password" name="action_pin" placeholder="PIN διαχείρισης..." required style="padding: 8px; margin-bottom: 8px;">
        <label>Τηλέφωνο Μέλους προς Προσθήκη/Τροποποίηση:</label>
        <input type="text" name="target_phone" placeholder="π.χ. 99123456" required>
        <label>Νέος Σταθμός / Ομάδα:</label>
        <select name="new_station">
            <option value="Γενικό">Γενικό Κανάλι</option>
            <option value="Ναυτικός Σταθμός">Ναυτικός Σταθμός</option>
            <option value="Λιμενικός Σταθμός">Λιμενικός Σταθμός</option>
            <option value="Λιμενικό Μαρίνας">Λιμενικό Μαρίνας</option>
            <option value="Διοίκηση">Διοίκηση</option>
            <option value="Μηχανικοί & Ηλεκτρολόγοι">Μηχανικοί & Ηλεκτρολόγοι</option>
            <option value="Κυβερνήτες Α Κατηγορίας">Κυβερνήτες Α Κατηγορίας</option>
            <option value="Αρχείο">Αρχείο</option>
            <option value="Υπεύθυνοι Σταθμών & Βραδιών">Υπεύθυνοι Σταθμών & Βραδιών</option>
        </select>
        <button type="submit" class="btn" style="background-color: #d9534f;">Εφαρμογή Αλλαγής με PIN</button>
    </form>
    <h4 style="margin-top: 20px; font-size: 14px;">Καταχωρημένο Προσωπικό</h4>
    <ul style="padding-left: 15px; font-size: 13px;">
        {% for u in users %}
            <li><b>{{ u[2] }}</b> ({{ u[1] }}) - Σταθμός: {{ u[3] }}</li>
        {% endfor %}
    </ul>
</div>
''')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        name = request.form.get('name', '').strip()
        station = request.form.get('station', '').strip()
        if phone and name:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO users (phone, name, station) VALUES (?, ?, ?)", (phone, name, station))
            conn.commit()
            conn.close()
            session['phone'] = phone
            session['name'] = name
            session['station'] = station
            return redirect(url_for('index'))
    return LOGIN_PAGE

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    if 'phone' not in session:
        return redirect(url_for('login'))
    return render_template_string(INDEX_PAGE)

@app.route('/channels')
def channels_list():
    if 'phone' not in session:
        return redirect(url_for('login'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, max_members, is_special FROM channels")
    channels = cursor.fetchall()
    conn.close()
    return render_template_string(CHANNELS_PAGE, channels=channels, current_channel=None, messages=[])

@app.route('/chat/<path:channel_name>', methods=['GET', 'POST'])
def chat_room(channel_name):
    if 'phone' not in session:
        return redirect(url_for('login'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                file.save(filepath)
                image_filename = filename
        if content or image_filename:
            cursor.execute("INSERT INTO messages (channel_name, sender_phone, sender_name, content, image_url) VALUES (?, ?, ?, ?, ?)",
                           (channel_name, session['phone'], session['name'], content, image_filename))
            conn.commit()
    cursor.execute("SELECT id, name, max_members, is_special FROM channels")
    channels = cursor.fetchall()
    cursor.execute("SELECT id, channel_name, sender_phone, sender_name, content, timestamp, image_url FROM messages WHERE channel_name = ? ORDER BY timestamp ASC", (channel_name,))
    messages = cursor.fetchall()
    conn.close()
    return render_template_string(CHANNELS_PAGE, channels=channels, current_channel=channel_name, messages=messages)

@app.route('/logbooks')
def logbooks_list():
    if 'phone' not in session:
        return redirect(url_for('login'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM channels WHERE name LIKE 'LogBook_%'")
    boats = [row[0].replace('LogBook_', '') for row in cursor.fetchall()]
    conn.close()
    return render_template_string(LOGBOOKS_PAGE, boats=boats, current_boat=None, entries=[])

@app.route('/logbook/<boat_name>', methods=['GET', 'POST'])
def view_logbook(boat_name):
    if 'phone' not in session:
        return redirect(url_for('login'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if request.method == 'POST':
        entered_pin = request.form.get('action_pin', '')
        entry = request.form.get('entry', '').strip()
        if entered_pin == ADMIN_ACTION_PASSWORD and entry:
            cursor.execute("INSERT INTO logbooks (boat_name, sender_phone, sender_name, entry) VALUES (?, ?, ?, ?)",
                           (boat_name, session['phone'], session['name'], entry))
            conn.commit()
            flash('Επιτυχής καταχώριση στο Log Book.', 'success')
        else:
            flash('Λανθασμένος υπηρεσιακός κωδικός PIN.', 'error')
    cursor.execute("SELECT name FROM channels WHERE name LIKE 'LogBook_%'")
    boats = [row[0].replace('LogBook_', '') for row in cursor.fetchall()]
    cursor.execute("SELECT id, boat_name, sender_phone, sender_name, timestamp, entry FROM logbooks WHERE boat_name = ? ORDER BY timestamp DESC", (boat_name,))
    entries = cursor.fetchall()
    conn.close()
    return render_template_string(LOGBOOKS_PAGE, boats=boats, current_boat=boat_name, entries=entries)

@app.route('/excel', methods=['GET', 'POST'])
def excel_management():
    if 'phone' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        entered_pin = request.form.get('action_pin', '')
        if entered_pin == ADMIN_ACTION_PASSWORD:
            if 'excel_file' in request.files:
                file = request.files['excel_file']
                if file and file.filename.endswith('.xlsx'):
                    filename = secure_filename(file.filename)
                    filepath = os.path.join(EXCEL_FOLDER, filename)
                    file.save(filepath)
                    wb = openpyxl.load_workbook(filepath)
                    for sheet in wb.sheetnames:
                        ws = wb[sheet]
                        ws.protection.sheet = True
                        ws.protection.password = "lkn_secure_2026"
                    wb.save(filepath)
                    flash('Το αρχείο Excel αναρτήθηκε και οι φόρμουλες κλειδώθηκαν επιτυχώς.', 'success')
        else:
            flash('Λανθασμένος κωδικός PIN ανάρτησης.', 'error')
    excel_files = [f for f in os.listdir(EXCEL_FOLDER) if f.endswith('.xlsx')]
    return render_template_string(EXCEL_PAGE, excel_files=excel_files)

@app.route('/download_excel/<filename>')
def download_excel(filename):
    if 'phone' not in session:
        return redirect(url_for('login'))
    return send_file(os.path.join(EXCEL_FOLDER, filename), as_attachment=True)

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if 'phone' not in session:
        return redirect(url_for('login'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if request.method == 'POST':
        entered_pin = request.form.get('action_pin', '')
        target_phone = request.form.get('target_phone', '').strip()
        new_station = request.form.get('new_station', '').strip()
        if entered_pin == ADMIN_ACTION_PASSWORD:
            cursor.execute("UPDATE users SET station = ? WHERE phone = ?", (new_station, target_phone))
            conn.commit()
            flash('Η μετάθεση / ομάδα του μέλους ενημερώθηκε επιτυχώς.', 'success')
        else:
            flash('Λανθασμένος κωδικός PIN διαχείρισης.', 'error')
    cursor.execute("SELECT id, phone, name, station FROM users")
    users = cursor.fetchall()
    conn.close()
    return render_template_string(ADMIN_PAGE, users=users)

@app.route('/uploads/<filename>')
def static_file(filename):
    return send_file(os.path.join(UPLOAD_FOLDER, filename))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

