import os
import sqlite3
import json
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
ADMIN_ACTION_PASSWORD = "LKN_ADMIN_2026"

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
            is_sos INTEGER DEFAULT 0,
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
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_name TEXT NOT NULL,
            action_desc TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
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
    cursor.execute("DELETE FROM messages WHERE timestamp < ? AND is_sos = 0", (thirty_days_ago,))
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
        .chat-box { height: calc(100vh - 300px); min-height: 250px; border: 1px solid #ddd; border-radius: 6px; padding: 10px; overflow-y: scroll; background: #fafafa; margin-bottom: 10px; }
        .message { margin-bottom: 10px; border-bottom: 1px solid #eee; padding-bottom: 6px; font-size: 14px; }
        .message.sos { background: #ffe6e6; border-left: 4px solid #d9534f; padding-left: 8px; border-radius: 4px; }
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
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div style="background: {% if category == 'error' %}#f8d7da{% else %}#e7f3ff{% endif %}; color: {% if category == 'error' %}#721c24{% else %}#1877f2{% endif %}; padding: 10px; border-radius: 6px; margin-bottom: 10px; font-size: 14px; border: 1px solid {% if category == 'error' %}#f5c6cb{% else %}#b8daff{% endif %};">
                    {{ message }}
                </div>
                {% endfor %}
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
        <button type="submit" class="btn">Είσοδος στο Γενικό Κανάλι</button>
    </form>
</div>
''')

INDEX_PAGE = MOBILE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
<div class="card">
    <div style="font-size: 14px; color: #65676b;">Χρήστης: <b>{{ session.get('name') }}</b></div>
    <div class="badge">Τοποθέτηση / Σταθμός: {{ session.get('station') }}</div>
    <div class="menu-grid" style="margin-top: 15px;">
        <a href="{{ url_for('channels_list') }}" class="menu-btn">💬 Κανάλια & Αίθουσες Επικοινωνίας</a>
        <a href="{{ url_for('logbooks_list') }}" class="menu-btn">⚓ Ψηφιακά Log Books Σκαφών (1-10)</a>
        <a href="{{ url_for('excel_management') }}" class="menu-btn">📊 Ηλεκτρονική Διοίκηση (Excel)</a>
        <a href="{{ url_for('admin_panel') }}" class="menu-btn" style="border-color: #d9534f; color: #d9534f;">⚙️ Διαχείριση & Ιστορικό (PIN)</a>
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
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <div style="font-size: 12px; color: #888;">Ενεργό Κανάλι: <b>{{ current_channel }}</b></div>
                {% if current_channel == 'Γενικό Κανάλι' and has_access %}
                <form method="POST" style="margin: 0;">
                    <input type="hidden" name="is_sos" value="1">
                    <button type="submit" style="background: #d9534f; color: white; border: none; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; cursor: pointer;">🚨 SOS</button>
                </form>
                {% endif %}
            </div>
            
            {% if has_access %}
                <div class="chat-box" id="chatBox">
                    {% for m in messages %}
                        <div class="message {% if m[6] == 1 %}sos{% endif %}">
                            <div class="meta"><b>{{ m[3] }}</b> - {{ m[5] }} {% if m[6] == 1 %}<span style="color: red; font-weight: bold;">[ΕΚΤΑΚΤΗ ΑΝΑΓΚΗ - SOS]</span>{% endif %}</div>
                            <div>{{ m[4] }}</div>
                            {% if m[7] %}
                                <div style="margin-top: 4px;"><img src="{{ url_for('static_file', filename=m[7]) }}" style="max-width: 150px; border-radius: 4px;"></div>
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
            {% else %}
                <div style="padding: 20px; text-align: center; color: #d9534f; background: #fdf7f7; border-radius: 6px; margin-top: 10px;">
                    <b>Περιορισμένη Πρόσβαση</b><br>Δεν έχετε δικαίωμα συμμετοχής σε αυτό το κανάλι. Η τοποθέτησή σας είναι στο: <b>{{ session.get('station') }}</b>.
                </div>
            {% endif %}
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
    <div style="background: #e2f0d9; padding: 10px; border-radius: 6px; margin-bottom: 10px;">
        <h4 style="margin: 0 0 6px 0; font-size: 14px;">Ανάρτηση Νέου Αρχείου (Master)</h4>
        <form method="POST" action="{{ url_for('excel_management') }}" enctype="multipart/form-data">
            <input type="password" name="action_pin" placeholder="PIN ανάρτησης..." required style="padding: 8px; margin-bottom: 8px; font-size: 13px;">
            <input type="file" name="excel_file" accept=".xlsx" required style="font-size: 13px; padding: 6px;">
            <button type="submit" class="btn" style="background-color: #28a745; padding: 10px; font-size: 14px;">Ανάρτηση Αρχείου</button>
        </form>
    </div>
    <div>
        <h4 style="margin: 10px 0 6px 0; font-size: 14px;">Διαθέσιμα Αρχεία</h4>
        {% if excel_files %}
            {% for f in excel_files %}
                <div style="border: 1px solid #ddd; border-radius: 6px; padding: 10px; margin-bottom: 10px; background: #fff;">
                    <div style="font-weight: bold; margin-bottom: 8px; font-size: 14px;">{{ f }}</div>
                    <div style="display: flex; gap: 8px; flex-direction: column;">
                        <a href="{{ url_for('edit_excel', filename=f) }}" class="btn" style="margin: 0; background-color: #17a2b8; text-align: center; font-size: 14px;">📝 Διαδικτυακή Επεξεργασία</a>
                        <form method="POST" action="{{ url_for('download_excel', filename=f) }}" style="display: flex; gap: 6px; margin-top: 6px;">
                            <input type="password" name="download_pin" placeholder="PIN για λήψη..." required style="margin: 0; padding: 8px; flex: 1; font-size: 13px;">
                            <button type="submit" class="btn" style="margin: 0; padding: 8px; font-size: 13px; width: 80px; background-color: #6c757d;">📥 Λήψη</button>
                        </form>
                    </div>
                </div>
            {% endfor %}
        {% else %}
            <p style="font-size: 13px; color: #888;">Δεν υπάρχουν διαθέσιμα αρχεία.</p>
        {% endif %}
    </div>
</div>
''')

EXCEL_EDITOR_PAGE = MOBILE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
<div class="card" style="overflow-x: auto;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
        <a href="{{ url_for('excel_management') }}" style="color: #1b365d; text-decoration: none; font-weight: bold; font-size: 14px;">← Πίσω στα Αρχεία</a>
        <button onclick="saveExcel()" class="btn" style="background-color: #28a745; margin: 0; width: auto; padding: 8px 15px; font-size: 13px;">💾 Αποθήκευση</button>
    </div>
    <h4 style="margin: 0 0 10px 0; font-size: 14px;">Επεξεργασία: {{ filename }}</h4>
    <table id="excelTable" style="width: 100%; border-collapse: collapse; font-size: 13px; background: white;">
        <tbody id="tableBody"></tbody>
    </table>
    <button onclick="addRow()" class="btn" style="background-color: #5bc0de; margin-top: 10px; font-size: 13px;">+ Προσθήκη Γραμμής</button>
</div>
<script>
    const sheetData = {{ sheet_data|safe }};
    const tbody = document.getElementById('tableBody');
    function renderTable() {
        tbody.innerHTML = '';
        sheetData.forEach((row, rIndex) => {
            const tr = document.createElement('tr');
            row.forEach((cell, cIndex) => {
                const td = document.createElement('td');
                td.style.border = "1px solid #ccd0d5";
                td.style.padding = "2px";
                const input = document.createElement('input');
                input.type = "text";
                input.value = cell === null ? "" : cell;
                input.style.width = "100%";
                input.style.border = "none";
                input.style.margin = "0";
                input.style.padding = "6px";
                input.style.boxSizing = "border-box";
                input.onchange = (e) => { sheetData[rIndex][cIndex] = e.target.value; };
                td.appendChild(input);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
    }
    function addRow() {
        if(sheetData.length > 0) {
            sheetData.push(new Array(sheetData[0].length).fill(""));
        } else {
            sheetData.push(["", "", "", "", ""]);
        }
        renderTable();
    }
    function saveExcel() {
        fetch("{{ url_for('edit_excel', filename=filename) }}", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ data: sheetData })
        })
        .then(res => res.json())
        .then(data => {
            if(data.status === 'success') { alert('Οι αλλαγές αποθηκεύτηκαν επιτυχώς.'); } 
            else { alert('Σφάλμα: ' + data.message); }
        }).catch(err => { alert('Σφάλμα επικοινωνίας.'); });
    }
    renderTable();
</script>
''')

ADMIN_PAGE = MOBILE_TEMPLATE.replace('{% block content %}{% endblock %}', '''
<div class="card">
    <a href="{{ url_for('index') }}" style="color: #1b365d; text-decoration: none; font-weight: bold; font-size: 14px; display: inline-block; margin-bottom: 10px;">← Επιστροφή</a>
    <h3 style="margin-top: 0;">⚙️ Διαχείριση & Ιστορικό</h3>
    <form method="POST">
        <label style="font-size: 12px;">Κωδικός Διοίκησης (PIN):</label>
        <input type="password" name="action_pin" placeholder="PIN διαχείρισης..." required style="padding: 8px; margin-bottom: 8px;">
        <label>Τηλέφωνο Μέλους:</label>
        <input type="text" name="target_phone" placeholder="π.χ. 99123456" required>
        <label>Νέα Τοποθέτηση / Σταθμός:</label>
        <select name="new_station">
            <option value="Γενικό Κανάλι">Γενικό Κανάλι</option>
            <option value="Ναυτικός Σταθμός">Ναυτικός Σταθμός</option>
            <option value="Λιμενικός Σταθμός">Λιμενικός Σταθμός</option>
            <option value="Λιμενικό Μαρίνας">Λιμενικό Μαρίνας</option>
            <option value="Διοίκηση">Διοίκηση</option>
            <option value="Μηχανικοί & Ηλεκτρολόγοι">Μηχανικοί & Ηλεκτρολόγοι</option>
            <option value="Κυβερνήτες Α Κατηγορίας">Κυβερνήτες Α Κατηγορίας</option>
            <option value="Αρχείο">Αρχείο</option>
            <option value="Υπεύθυνοι Σταθμών & Βραδιών">Υπεύθυνοι Σταθμών & Βραδιών</option>
        </select>
        <button type="submit" class="btn" style="background-color: #d9534f;">Ενημέρωση Τοποθέτησης με PIN</button>
    </form>
    
    <h4 style="margin-top: 25px; font-size: 14px;">📋 Ιστορικό Ενεργειών (Audit Trail)</h4>
    <div style="max-height: 150px; overflow-y: scroll; border: 1px solid #ddd; padding: 8px; border-radius: 6px; background: #fafafa; font-size: 12px;">
        {% for log in audit_logs %}
            <div style="border-bottom: 1px solid #eee; padding: 4px 0;">
                <span style="color: #666;">[{{ log[3] }}]</span> <b>{{ log[1] }}</b>: {{ log[2] }}
            </div>
        {% else %}
            <p style="color: #888; text-align: center; margin: 5px 0;">Δεν υπάρχουν καταγεγραμμένες ενέργειες.</p>
        {% endfor %}
    </div>

    <h4 style="margin-top: 20px; font-size: 14px;">Καταχωρημένο Προσωπικό</h4>
    <ul style="padding-left: 15px; font-size: 13px;">
        {% for u in users %}
            <li><b>{{ u[2] }}</b> ({{ u[1] }}) - Τοποθέτηση: <b>{{ u[3] }}</b></li>
        {% endfor %}
    </ul>
</div>
''')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        name = request.form.get('name', '').strip()
        if phone and name:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT station FROM users WHERE phone = ?", (phone,))
            existing = cursor.fetchone()
            
            if existing:
                station = existing[0]
                cursor.execute("UPDATE users SET name = ? WHERE phone = ?", (name, phone))
            else:
                station = 'Γενικό Κανάλι'
                cursor.execute("INSERT INTO users (phone, name, station) VALUES (?, ?, ?)", (phone, name, station))
                
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
    
    cursor.execute("SELECT station FROM users WHERE phone = ?", (session['phone'],))
    user_row = cursor.fetchone()
    user_station = user_row[0] if user_row else 'Γενικό Κανάλι'
    session['station'] = user_station
    
    has_access = (channel_name == 'Γενικό Κανάλι' or user_station == channel_name)
    
    if request.method == 'POST' and has_access:
        is_sos = 1 if request.form.get('is_sos') == '1' else 0
        content = request.form.get('content', '').strip()
        image_filename = None
        
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                file.save(filepath)
                image_filename = filename
                
        if is_sos:
            content = "⚠️ ΣΗΜΑ ΚΙΝΔΥΝΟΥ - ΕΚΤΑΚΤΗ ΑΝΑΓΚΗ (SOS) ΑΠΟ " + session['name']
            
        if content or image_filename:
            cursor.execute("INSERT INTO messages (channel_name, sender_phone, sender_name, content, image_url, is_sos) VALUES (?, ?, ?, ?, ?, ?)",
                           (channel_name, session['phone'], session['name'], content, image_filename, is_sos))
            conn.commit()
            
    cursor.execute("SELECT id, name, max_members, is_special FROM channels")
    channels = cursor.fetchall()
    
    messages = []
    if has_access:
        cursor.execute("SELECT id, channel_name, sender_phone, sender_name, content, timestamp, is_sos, image_url FROM messages WHERE channel_name = ? ORDER BY timestamp ASC", (channel_name,))
        messages = cursor.fetchall()
        
    conn.close()
    return render_template_string(CHANNELS_PAGE, channels=channels, current_channel=channel_name, messages=messages, has_access=has_access)

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
                    
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO audit_logs (admin_name, action_desc) VALUES (?, ?)", (session['name'], f"Ανάρτηση νέου αρχείου Excel: {filename}"))
                    conn.commit()
                    conn.close()
                    
                    flash('Το αρχείο Excel αναρτήθηκε επιτυχώς.', 'success')
        else:
            flash('Λανθασμένος κωδικός PIN ανάρτησης.', 'error')
    excel_files = [f for f in os.listdir(EXCEL_FOLDER) if f.endswith('.xlsx')]
    return render_template_string(EXCEL_PAGE, excel_files=excel_files)

@app.route('/edit_excel/<filename>', methods=['GET', 'POST'])
def edit_excel(filename):
    if 'phone' not in session:
        return redirect(url_for('login'))
        
    filepath = os.path.join(EXCEL_FOLDER, filename)
    if not os.path.exists(filepath):
        flash('Το αρχείο δεν βρέθηκε.', 'error')
        return redirect(url_for('excel_management'))
        
    if request.method == 'POST':
        try:
            payload = request.get_json()
            if not payload or 'data' not in payload:
                return {'status': 'error', 'message': 'Invalid payload'}, 400
                
            data = payload.get('data', [])
            wb = openpyxl.load_workbook(filepath)
            ws = wb.active
            
            ws.remove(ws)
            ws = wb.create_sheet(title="Sheet1")
            
            for row_idx, row_data in enumerate(data, start=1):
                for col_idx, cell_value in enumerate(row_data, start=1):
                    val = cell_value
                    if isinstance(val, str):
                        val_stripped = val.strip()
                        if val_stripped.isdigit():
                            val = int(val_stripped)
                        else:
                            try:
                                val = float(val_stripped)
                            except ValueError:
                                pass
                    ws.cell(row=row_idx, column=col_idx, value=val)
                    
            wb.save(filepath)
            return {'status': 'success'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}, 500
            
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active
    sheet_data = []
    for row in ws.iter_rows(values_only=True):
        sheet_data.append([str(cell) if cell is not None else "" for cell in row])
        
    return render_template_string(EXCEL_EDITOR_PAGE, filename=filename, sheet_data=json.dumps(sheet_data))

@app.route('/download_excel/<filename>', methods=['POST'])
def download_excel(filename):
    if 'phone' not in session:
        return redirect(url_for('login'))
    entered_pin = request.form.get('download_pin', '')
    if entered_pin == ADMIN_ACTION_PASSWORD:
        return send_file(os.path.join(EXCEL_FOLDER, filename), as_attachment=True)
    else:
        flash('Λανθασμένος κωδικός PIN για λήψη.', 'error')
        return redirect(url_for('excel_management'))

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
            cursor.execute("INSERT INTO audit_logs (admin_name, action_desc) VALUES (?, ?)", (session['name'], f"Μετάθεση μέλους ({target_phone}) σε: {new_station}"))
            conn.commit()
            flash('Η τοποθέτηση του μέλους ενημερώθηκε επιτυχώς.', 'success')
        else:
            flash('Λανθασμένος κωδικός PIN διαχείρισης.', 'error')
            
    cursor.execute("SELECT id, phone, name, station FROM users")
    users = cursor.fetchall()
    cursor.execute("SELECT id, admin_name, action_desc, timestamp FROM audit_logs ORDER BY timestamp DESC LIMIT 20")
    audit_logs = cursor.fetchall()
    conn.close()
    return render_template_string(ADMIN_PAGE, users=users, audit_logs=audit_logs)

@app.route('/uploads/<filename>')
def static_file(filename):
    return send_file(os.path.join(UPLOAD_FOLDER, filename))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)