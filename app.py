import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import uuid
import openpyxl
from io import BytesIO
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'lkn_ast_larnakas_secure_key_2026')

# --- SUPABASE CONFIGURATION ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
BUCKET_NAME = "larnaca_files"

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("⚠️ Λείπουν τα SUPABASE_URL και SUPABASE_KEY από τα Environment Variables.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

DEFAULT_ADMIN_PASSWORD = 'LKN_ADMIN_2026'
ROSTER_SYMBOLS = ['', 'Μ', 'Ν', 'SL', 'Α', 'RD', 'T', 'Υ', 'Π', 'Ε', 'ΑΠ']

# --- ΒΟΗΘΗΤΙΚΕΣ ΣΥΝΑΡΤΗΣΕΙΣ ---

def upload_to_supabase(file_obj, filename, content_type):
    if not supabase: return None
    try:
        file_bytes = file_obj.read()
        supabase.storage.from_(BUCKET_NAME).upload(path=filename, file=file_bytes, file_options={"content-type": content_type})
        url = supabase.storage.from_(BUCKET_NAME).get_public_url(filename)
        return url
    except Exception as e:
        logging.error(f"[STORAGE ERROR] {e}")
        return None

def get_setting(key, default_value=""):
    try:
        res = supabase.table('settings').select('value').eq('key', key).execute()
        return res.data[0]['value'] if res.data else default_value
    except: return default_value

def set_setting(key, value):
    try:
        supabase.table('settings').upsert({'key': key, 'value': value}).execute()
    except Exception as e:
        logging.error(f"Error setting {key}: {e}")

def is_roster_active():
    return get_setting('roster_enabled') == '1'

def get_current_user():
    if 'user_phone' not in session: return None
    res = supabase.table('users').select('*').eq('phone', session['user_phone']).execute()
    return res.data[0] if res.data else None

def get_super_admin_id():
    res = supabase.table('users').select('id').eq('role', 'Admin').eq('status', 'Approved').eq('admin_level', 1).execute()
    if res.data: return res.data[0]['id']
    res2 = supabase.table('users').select('id').eq('role', 'Admin').eq('status', 'Approved').order('admin_level').execute()
    return res2.data[0]['id'] if res2.data else None

def check_admin_permission(user, required_permission):
    if not user or user['role'] != 'Admin': return False
    level = user.get('admin_level', 0)
    if level <= 3: return True
    if level == 4:
        perms = user.get('custom_permissions', '').split(',') if user.get('custom_permissions') else []
        return required_permission in perms
    return False

def send_real_email(to_email, subject, body, attachment_bytes=None, attachment_name="roster.xlsx"):
    if not to_email or '@' not in to_email: return
    server_host = get_setting('smtp_server', 'smtp.gmail.com')
    port_val = int(get_setting('smtp_port', '587'))
    smtp_user = get_setting('smtp_user')
    smtp_pass = get_setting('smtp_pass')

    if not smtp_user or not smtp_pass: return

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

        server = smtplib.SMTP(server_host, port_val)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, msg.as_string())
        server.quit()
    except Exception as e:
        logging.error(f"[SMTP ERROR] {e}")

# --- UI STYLES ---
BASE_STYLE = """
<style>
    body { background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 50%, #0369a1 100%); background-attachment: fixed; font-family: Arial, sans-serif; color: #fff; margin: 0; padding: 20px; }
    .app-brand-bar { max-width: 1100px; margin: 0 auto 10px auto; display: flex; justify-content: space-between; align-items: center; font-size: 15px; font-weight: bold; color: #e2e8f0; text-shadow: 0 2px 4px rgba(0,0,0,0.5); }
    .settings-gear { background: rgba(255, 255, 255, 0.2); color: white; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 13px; font-weight: bold; transition: background 0.2s; }
    .settings-gear:hover { background: rgba(255, 255, 255, 0.4); text-decoration: none; }
    .container { max-width: 1100px; margin: 0 auto; background: rgba(255, 255, 255, 0.96); color: #333; padding: 25px; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.6); }
    a { color: #0056b3; text-decoration: none; } a:hover { text-decoration: underline; }
    input, select, textarea { padding: 8px; margin: 5px 0; border: 1px solid #ccc; border-radius: 4px; width: 100%; box-sizing: border-box; }
    button, .btn-link { display: inline-block; background: #0056b3; color: white !important; border: none; padding: 8px 14px; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 13px; text-align: center; margin: 2px 0; }
    button:hover, .btn-link:hover { background: #004085; text-decoration: none; }
    .btn-danger { background: #dc2626 !important; } .btn-success { background: #16a34a !important; } .btn-warning { background: #ca8a04 !important; }
    .nav-buttons { display: flex; flex-wrap: wrap; gap: 8px; margin: 15px 0; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; background: #fff; font-size: 13px; }
    th, td { border: 1px solid #ddd; padding: 6px; text-align: center; color: #333; } th { background: #f2f2f2; }
    hr { border: 0; height: 1px; background: #ccc; margin: 20px 0; }
    .chat-bubble { background: #ffffff; padding: 10px 14px; border-radius: 12px; margin-bottom: 10px; max-width: 80%; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .chat-author { font-size: 12px; font-weight: bold; color: #075e54; margin-bottom: 3px; }
    .chat-time { font-size: 10px; color: #888; float: right; margin-left: 10px; }
    .chat-text { font-size: 13px; color: #222; word-break: break-word; }
</style>
<div class="app-brand-bar">
    <span>⚓ Σταθμοί Λάρνακας V5.0 (Supabase Engine)</span>
    {% if session.get('user_phone') %}<a href="/user_settings" class="settings-gear">⚙️ Ρυθμίσεις Χρήστη</a>{% endif %}
</div>
"""

# --- ROUTES ---

@app.route('/')
def index():
    user = get_current_user()
    if not user:
        session.clear()
        return redirect(url_for('login'))
        
    if not user['password'] or not user['email']:
        return redirect(url_for('set_password'))
        
    if user['status'] != 'Approved' and user['role'] != 'Admin':
        return render_template_string(BASE_STYLE + """
        <div class='container'>
            <h3>Ο λογαριασμός σας αναμένει έγκριση από τον Διαχειριστή.</h3>
            <div class="nav-buttons">
                <a href='/user_settings' class="btn-link btn-warning">⚙️ Ρυθμίσεις</a>
                <a href='/logout' class="btn-link btn-danger">Αποσύνδεση</a>
            </div>
        </div>
        """)
    
    if user['role'] == 'Admin':
        channels_res = supabase.table('channels').select('name').execute()
    else:
        # Αναζήτηση καναλιών για απλούς χρήστες
        cm_res = supabase.table('channel_members').select('channel_id').eq('user_id', user['id']).execute()
        ch_ids = [m['channel_id'] for m in cm_res.data]
        if ch_ids:
            channels_res = supabase.table('channels').select('name').in_('id', ch_ids).execute()
        else:
            channels_res = {'data': []}
            
    channels = sorted(list(set([r['name'] for r in channels_res.data])))
    vessel_channels = [f'Σκάφος {i}' for i in range(1, 11)]
    standard_channels = [ch for ch in channels if ch not in vessel_channels and ch not in ['Ψηφιακή Βιβλιοθήκη', 'Roster Βάρδιας']]

    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Καλώς ορίσατε, {{ user.name }}</h2>
        <div class="nav-buttons">
            <a href="/admin" class="btn-link">Κεντρικό Πάνελ</a>
            <a href="/library" class="btn-link">Ψηφιακή Βιβλιοθήκη</a>
            {% if roster_active or user.role == 'Admin' %}<a href="/roster" class="btn-link">Roster Βάρδιας</a>{% endif %}
            <a href="/user_settings" class="btn-link btn-warning">⚙️ Ρυθμίσεις</a>
            <a href="/logout" class="btn-link btn-danger">Αποσύνδεση</a>
        </div>
        <hr>
        <h3>Επιχειρησιακά Κανάλια</h3>
        <ul>
            {% for ch in standard_channels %}
                <li><a href="/logbook/{{ ch }}"><b>{{ '📢 ' if ch == 'Γενικό Κανάλι' else '' }}{{ ch }}</b></a></li>
            {% endfor %}
        </ul>
        <hr>
        <h3>Logbooks 10 Σκαφών</h3>
        <ul>
            {% for v in vessel_channels %}
                {% if user.role == 'Admin' or v in channels %}
                    <li><a href="/vessel_log/{{ v }}"><b>🛥️ {{ v }}</b></a></li>
                {% endif %}
            {% endfor %}
        </ul>
    </div>
    """, user=user, standard_channels=standard_channels, vessel_channels=vessel_channels, channels=channels, roster_active=is_roster_active())

@app.route('/login', methods=['GET', 'POST'])
def login():
    error_msg = ""
    if request.method == 'POST':
        phone = request.form.get('phone')
        password = request.form.get('password', '')
        res = supabase.table('users').select('*').eq('phone', phone).execute()
        
        if res.data:
            user = res.data[0]
            session['user_phone'] = phone
            if not user.get('password') or not user.get('email'):
                return redirect(url_for('set_password'))
            else:
                if check_password_hash(user['password'], password):
                    return redirect(url_for('index'))
                else:
                    error_msg = "Λάθος κωδικός πρόσβασης."
        else:
            error_msg = "Ο αριθμός δεν βρέθηκε. Παρακαλώ εγγραφείτε."
            
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width: 400px; margin-top: 50px;">
        <h2>Σύνδεση</h2>
        <p style="color:red; font-weight:bold;">{{ error_msg }}</p>
        <form method="POST">
            Τηλέφωνο: <input type="text" name="phone" required><br>
            Κωδικός: <input type="password" name="password" placeholder="Απαραίτητος μετά την 1η είσοδο"><br><br>
            <button type="submit" style="width:100%;">Σύνδεση</button>
        </form>
        <div style="margin-top:15px; text-align:center;">
            <a href="/register" class="btn-link" style="width:100%; box-sizing:border-box;">Εγγραφή Νέου Χρήστη</a>
        </div>
    </div>
    """, error_msg=error_msg)

@app.route('/set_password', methods=['GET', 'POST'])
def set_password():
    user = get_current_user()
    if not user: return redirect(url_for('login'))
    msg = ""
    if request.method == 'POST':
        new_pass = request.form.get('new_password')
        confirm_pass = request.form.get('confirm_password')
        email = request.form.get('email', '').strip()
        
        if not email: msg = "Το email είναι υποχρεωτικό."
        elif new_pass and new_pass == confirm_pass:
            supabase.table('users').update({'password': generate_password_hash(new_pass), 'email': email}).eq('id', user['id']).execute()
            return redirect(url_for('index'))
        else: msg = "Οι κωδικοί δεν ταιριάζουν."

    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width: 400px;">
        <h2>Ορισμός Κωδικού & Email</h2>
        <p style="color:red;">{{ msg }}</p>
        <form method="POST">
            Email: <input type="email" name="email" value="{{ user.email or '' }}" required><br>
            Νέος Κωδικός: <input type="password" name="new_password" required><br>
            Επιβεβαίωση: <input type="password" name="confirm_password" required><br><br>
            <button type="submit" style="width:100%;">Αποθήκευση</button>
        </form>
    </div>
    """, msg=msg, user=user)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u_data = {
            'phone': request.form.get('phone'), 'name': request.form.get('name'),
            'station': request.form.get('station'), 'email': request.form.get('email')
        }
        res = supabase.table('users').select('id').execute()
        if len(res.data) == 0:
            u_data.update({'status': 'Approved', 'role': 'Admin', 'admin_level': 1})
        else:
            u_data.update({'status': 'Pending', 'role': 'User', 'admin_level': 0})
            
        try:
            supabase.table('users').insert(u_data).execute()
            session['user_phone'] = u_data['phone']
            return redirect(url_for('set_password'))
        except Exception as e:
            flash('Το τηλέφωνο υπάρχει ήδη.')
            return redirect(url_for('login'))
            
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width: 400px;">
        <h2>Εγγραφή</h2>
        <form method="POST">
            Τηλέφωνο: <input type="text" name="phone" required><br>Όνομα: <input type="text" name="name" required><br>
            Σταθμός: <input type="text" name="station" required><br>Email: <input type="email" name="email" required><br><br>
            <button type="submit" style="width:100%;">Εγγραφή</button>
        </form>
        <div style="margin-top:15px; text-align:center;"><a href="/login" class="btn-link" style="width:100%;">Επιστροφή</a></div>
    </div>
    """)

@app.route('/user_settings', methods=['GET', 'POST'])
def user_settings():
    user = get_current_user()
    if not user: return redirect(url_for('login'))
    msg = ""

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_profile':
            new_email = request.form.get('email', '').strip()
            current_pass = request.form.get('current_password', '')
            new_pass = request.form.get('new_password', '')
            confirm_pass = request.form.get('confirm_password', '')

            updates = {'email': new_email}
            if new_pass:
                if not check_password_hash(user['password'], current_pass): msg = "Λάθος τρέχων κωδικός."
                elif new_pass != confirm_pass: msg = "Οι νέοι κωδικοί δεν ταιριάζουν."
                else: updates['password'] = generate_password_hash(new_pass)
            
            if not msg:
                supabase.table('users').update(updates).eq('id', user['id']).execute()
                msg = "Το προφίλ ενημερώθηκε."
                user['email'] = new_email
                
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>⚙️ Ρυθμίσεις Χρήστη</h2>
        <div class="nav-buttons"><a href="/" class="btn-link" style="background:#475569;">Επιστροφή</a></div>
        <p style="color:red; font-weight:bold;">{{ msg }}</p>

        <form method="POST" style="background:#f1f5f9; padding:15px; border-radius:5px;">
            <input type="hidden" name="action" value="update_profile">
            <h3>👤 Στοιχεία Λογαριασμού & Κωδικός</h3>
            Email Ειδοποιήσεων: <input type="email" name="email" value="{{ user.email }}" required><br><br>
            <p style="font-size:12px; color:#475569;">Συμπληρώστε τα παρακάτω μόνο αν επιθυμείτε αλλαγή κωδικού:</p>
            Τρέχων Κωδικός: <input type="password" name="current_password"><br>
            Νέος Κωδικός: <input type="password" name="new_password"><br>
            Επιβεβαίωση: <input type="password" name="confirm_password"><br><br>
            <button type="submit">Αποθήκευση Αλλαγών</button>
        </form>
    </div>
    """, user=user, msg=msg)

@app.route('/logbook/<channel_name>', methods=['GET', 'POST'])
def logbook(channel_name):
    user = get_current_user()
    if not user: return redirect(url_for('login'))
    
    if channel_name == 'E-Καθήκοντα' and user['role'] != 'Admin':
        return render_template_string(BASE_STYLE + "<div class='container'><h3 style='color:red;'>⚠️ Απαιτούνται δικαιώματα Admin.</h3><a href='/' class='btn-link'>Επιστροφή</a></div>")

    if request.method == 'POST':
        content = request.form.get('content', '')
        file = request.files.get('file')
        file_url, filename = None, None
        
        if file and file.filename != '':
            raw_filename = secure_filename(file.filename)
            filename = f"{uuid.uuid4().hex}_{raw_filename}"
            file_url = upload_to_supabase(file, filename, file.content_type)
            
        supabase.table('logbook_entries').insert({
            'channel_name': channel_name, 'author_name': user['name'], 'content': content, 
            'filename': filename, 'file_url': file_url, 'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }).execute()
        
    entries = supabase.table('logbook_entries').select('*').eq('channel_name', channel_name).order('id', desc=True).execute().data
    
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Κανάλι: {{ channel_name }}</h2>
        <div class="nav-buttons"><a href="/" class="btn-link" style="background:#475569;">Αρχική</a></div>
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; border-radius:5px;">
            <textarea name="content" placeholder="Καταχώρηση..." rows="3" required></textarea><br>
            Επισύναψη Αρχείου (Supabase Storage): <input type="file" name="file"><br><br>
            <button type="submit">Καταχώρηση</button>
        </form>
        <hr>
        <div style="background:#e7ebf0; padding:15px; border-radius:8px; max-height:500px; overflow-y:auto;">
            {% for e in entries %}
                <div class="chat-bubble">
                    <div class="chat-author">{{ e.author_name }} <span class="chat-time">{{ e.timestamp }}</span></div>
                    <div class="chat-text">{{ e.content }}</div>
                    {% if e.file_url %}
                        <div style="margin-top:6px;"><a href="{{ e.file_url }}" target="_blank" class="btn-link" style="padding:2px 6px; font-size:11px;">📥 Λήψη (Cloud)</a></div>
                    {% endif %}
                    {% if user.role == 'Admin' %}
                        <form method="POST" action="/delete_logbook" style="margin-top:8px;">
                            <input type="hidden" name="id" value="{{ e.id }}">
                            <input type="hidden" name="channel_name" value="{{ channel_name }}">
                            <button type="submit" class="btn-danger" style="padding:2px 6px; font-size:10px;">🗑️ Διαγραφή</button>
                        </form>
                    {% endif %}
                </div>
            {% endfor %}
        </div>
    </div>
    """, channel_name=channel_name, entries=entries, user=user)

@app.route('/delete_logbook', methods=['POST'])
def delete_logbook():
    user = get_current_user()
    if user and user['role'] == 'Admin':
        supabase.table('logbook_entries').delete().eq('id', request.form.get('id')).execute()
    return redirect(url_for('logbook', channel_name=request.form.get('channel_name')))

@app.route('/vessel_log/<vessel_name>', methods=['GET', 'POST'])
def vessel_log(vessel_name):
    user = get_current_user()
    if not user: return redirect(url_for('login'))
        
    if request.method == 'POST':
        supabase.table('vessel_logs').insert({
            'vessel_name': vessel_name, 'entry_date': request.form.get('entry_date'),
            'incident': request.form.get('incident'), 'damage': request.form.get('damage'),
            'working_hours': request.form.get('working_hours'), 'repair_report': request.form.get('repair_report'),
            'author': user['name'], 'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }).execute()
        
    entries = supabase.table('vessel_logs').select('*').eq('vessel_name', vessel_name).order('id', desc=True).execute().data
    
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Logbook Σκάφους: {{ vessel_name }}</h2>
        <div class="nav-buttons"><a href="/" class="btn-link" style="background:#475569;">Αρχική</a></div>
        <form method="POST" style="background:#f1f5f9; padding:15px; border-radius:5px;">
            Ημερομηνία: <input type="date" name="entry_date" value="{{ today }}" required><br>
            Περιστατικό: <textarea name="incident" rows="2" required></textarea><br>
            Βλάβη: <textarea name="damage" rows="2" required></textarea><br>
            Ώρες: <input type="text" name="working_hours" required><br>
            Επιδιόρθωση: <textarea name="repair_report" rows="2" required></textarea><br><br>
            <button type="submit">Καταχώρηση</button>
        </form>
        <hr>
        <ul>
            {% for e in entries %}
                <li style="margin-bottom: 12px; background:#fff; padding:12px; border-radius:4px; border:1px solid #e2e8f0;">
                    <b>[{{ e.entry_date }}] Συντάκτης: {{ e.author }}</b><br>
                    <b>Περιστατικό:</b> {{ e.incident }}<br><b>Βλάβη:</b> {{ e.damage }}<br>
                    <b>Ώρες:</b> {{ e.working_hours }}<br><b>Επιδιόρθωση:</b> {{ e.repair_report }}
                    {% if user.role == 'Admin' %}
                        <form method="POST" action="/delete_vessel" style="margin-top:6px;">
                            <input type="hidden" name="id" value="{{ e.id }}">
                            <input type="hidden" name="vessel_name" value="{{ vessel_name }}">
                            <button type="submit" class="btn-danger" style="padding:2px 6px; font-size:10px;">🗑️ Διαγραφή</button>
                        </form>
                    {% endif %}
                </li>
            {% endfor %}
        </ul>
    </div>
    """, vessel_name=vessel_name, entries=entries, today=datetime.now().strftime('%Y-%m-%d'), user=user)

@app.route('/delete_vessel', methods=['POST'])
def delete_vessel():
    user = get_current_user()
    if user and user['role'] == 'Admin':
        supabase.table('vessel_logs').delete().eq('id', request.form.get('id')).execute()
    return redirect(url_for('vessel_log', vessel_name=request.form.get('vessel_name')))

@app.route('/library', methods=['GET', 'POST'])
def library():
    user = get_current_user()
    if not user: return redirect(url_for('login'))
    
    if request.method == 'POST' and user['role'] == 'Admin':
        file = request.files.get('file')
        if file and file.filename != '':
            filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
            file_url = upload_to_supabase(file, filename, file.content_type)
            supabase.table('library').insert({
                'title': request.form.get('title'), 'category': request.form.get('category'),
                'filename': filename, 'file_url': file_url, 'uploader': user['name'],
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }).execute()
        
    items = supabase.table('library').select('*').order('id', desc=True).execute().data
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Ψηφιακή Βιβλιοθήκη (Supabase)</h2>
        <div class="nav-buttons"><a href="/" class="btn-link" style="background:#475569;">Αρχική</a></div>
        {% if user.role == 'Admin' %}
        <form method="POST" enctype="multipart/form-data" style="background:#f1f5f9; padding:15px; border-radius:5px;">
            Τίτλος: <input type="text" name="title" required><br>
            Κατηγορία: <select name="category"><option>Βιβλίο</option><option>Έγγραφο</option></select><br>
            Αρχείο: <input type="file" name="file" required><br><br><button type="submit">Μεταφόρτωση</button>
        </form><hr>
        {% endif %}
        <table>
            <tr><th>Τίτλος</th><th>Κατηγορία</th><th>Ημερομηνία</th><th>Λήψη</th>{% if user.role=='Admin' %}<th>Διαγραφή</th>{% endif %}</tr>
            {% for i in items %}
            <tr>
                <td>{{ i.title }}</td><td>{{ i.category }}</td><td>{{ i.timestamp }}</td>
                <td><a href="{{ i.file_url }}" target="_blank" class="btn-link" style="padding:4px; font-size:11px;">📥 Λήψη</a></td>
                {% if user.role == 'Admin' %}
                <td><form method="POST" action="/delete_library"><input type="hidden" name="id" value="{{ i.id }}"><button type="submit" class="btn-danger" style="padding:2px 6px; font-size:11px;">🗑️</button></form></td>
                {% endif %}
            </tr>
            {% endfor %}
        </table>
    </div>
    """, items=items, user=user)

@app.route('/delete_library', methods=['POST'])
def delete_library():
    user = get_current_user()
    if user and user['role'] == 'Admin':
        supabase.table('library').delete().eq('id', request.form.get('id')).execute()
    return redirect(url_for('library'))

@app.route('/roster', methods=['GET', 'POST'])
def roster():
    user = get_current_user()
    if not user: return redirect(url_for('login'))
    
    current_ym = request.args.get('ym', datetime.now().strftime('%Y-%m'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'save_roster':
            ym = request.form.get('year_month', current_ym)
            for day in range(1, 32):
                sym = request.form.get(f'day_{day}', '').strip().upper()
                if sym in ROSTER_SYMBOLS:
                    # Supabase Upsert based on composite uniqueness (handled via logic or RPC usually)
                    # For simplicity via Python Client without custom RPC:
                    existing = supabase.table('roster_entries').select('id').eq('user_id', user['id']).eq('year_month', ym).eq('day', day).execute()
                    if existing.data:
                        supabase.table('roster_entries').update({'symbol': sym}).eq('id', existing.data[0]['id']).execute()
                    else:
                        supabase.table('roster_entries').insert({'user_id': user['id'], 'year_month': ym, 'day': day, 'symbol': sym}).execute()
            return redirect(url_for('roster', ym=ym))
            
        elif action == 'send_excel_email' and user['role'] == 'Admin':
            ym = request.form.get('year_month', current_ym)
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"Roster {ym}"
            headers = ['Ονοματεπώνυμο'] + [str(d) for d in range(1, 32)]
            ws.append(headers)
            
            u_list = supabase.table('users').select('id, name').eq('status', 'Approved').order('name').execute().data
            r_rows = supabase.table('roster_entries').select('user_id, day, symbol').eq('year_month', ym).execute().data
            
            r_data = {u['id']: {d: '' for d in range(1, 32)} for u in u_list}
            for row in r_rows:
                if row['user_id'] in r_data:
                    r_data[row['user_id']][row['day']] = row['symbol']
            
            for u in u_list:
                row_data = [u['name']] + [r_data[u['id']][d] for d in range(1, 32)]
                ws.append(row_data)
            
            excel_io = BytesIO()
            wb.save(excel_io)
            excel_io.seek(0)
            
            admins = supabase.table('users').select('email').eq('role', 'Admin').execute().data
            for adm in admins:
                if adm.get('email'):
                    send_real_email(adm['email'], f"Μηνιαίο Roster - {ym}", "Επισυνάπτεται το Roster.", excel_io, f"Roster_{ym}.xlsx")
                    excel_io.seek(0)
            flash("Το Rosterστάλθηκε μέσω email!")
            
    users_list = supabase.table('users').select('id, name').eq('status', 'Approved').order('name').execute().data
    rows = supabase.table('roster_entries').select('user_id, day, symbol').eq('year_month', current_ym).execute().data
    roster_data = {u['id']: {d: '' for d in range(1, 32)} for u in users_list}
    for row in rows:
        if row['user_id'] in roster_data: roster_data[row['user_id']][row['day']] = row['symbol']
            
    my_row = roster_data.get(user['id'], {})
    
    return render_template_string(BASE_STYLE + """
    <div class="container" style="max-width:100%;">
        <h2>Ψηφιακό Roster Βάρδιας</h2>
        <div class="nav-buttons"><a href="/" class="btn-link" style="background:#475569;">Αρχική</a></div>
        {% with messages = get_flashed_messages() %}{% if messages %}<p style="color:green; font-weight:bold;">{{ messages[0] }}</p>{% endif %}{% endwith %}
        
        <form method="GET" style="margin-bottom:15px; display:flex; gap:10px; align-items:flex-end;">
            <div>Μήνας (ΕΕΕΕ-ΜΜ): <input type="text" name="ym" value="{{ current_ym }}" style="width:150px;"></div>
            <div><button type="submit">Προβολή</button></div>
        </form>

        {% if user.role == 'Admin' %}
        <form method="POST"><input type="hidden" name="action" value="send_excel_email"><input type="hidden" name="year_month" value="{{ current_ym }}"><button type="submit" class="btn-success">📧 Αποστολή Roster σε Excel</button></form>
        {% endif %}
        
        <form method="POST">
            <input type="hidden" name="action" value="save_roster"><input type="hidden" name="year_month" value="{{ current_ym }}">
            <h3>Η προσωπική μου συμπλήρωση:</h3>
            <div style="overflow-x:auto;">
            <table><tr>{% for d in range(1, 32) %}<th>{{ d }}</th>{% endfor %}</tr>
            <tr>{% for d in range(1, 32) %}<td><select name="day_{{ d }}" style="width:45px; padding:2px; font-size:11px;">{% set cv = my_row.get(d, '') %}{% for sym in roster_symbols %}<option value="{{ sym }}" {% if cv == sym %}selected{% endif %}>{{ sym or '-' }}</option>{% endfor %}</select></td>{% endfor %}</tr></table>
            </div><button type="submit">Αποθήκευση</button>
        </form><hr>
        <h3>Συγκεντρωτικός Πίνακας Προσωπικού</h3>
        <div style="overflow-x:auto;">
        <table>
            <tr><th style="position:sticky; left:0; background:#eee;">Όνομα</th>{% for d in range(1, 32) %}<th>{{ d }}</th>{% endfor %}</tr>
            {% for u in users_list %}<tr><td style="position:sticky; left:0; background:#f9f9f9; text-align:left; font-weight:bold;">{{ u.name }}</td>{% for d in range(1, 32) %}<td>{{ roster_data[u.id][d] }}</td>{% endfor %}</tr>{% endfor %}
        </table>
        </div>
    </div>
    """, users_list=users_list, roster_data=roster_data, current_ym=current_ym, my_row=my_row, roster_symbols=ROSTER_SYMBOLS, user=user)

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    user = get_current_user()
    if not user or user['role'] != 'Admin': return redirect(url_for('index'))
    is_super = (user['id'] == get_super_admin_id() or user.get('admin_level', 0) == 1)
    msg = ""

    if request.method == 'POST':
        admin_pass_db = get_setting('admin_password')
        if check_password_hash(admin_pass_db, request.form.get('admin_password', '')):
            act = request.form.get('action')
            if act == 'approve' and check_admin_permission(user, 'manage_users'):
                supabase.table('users').update({'status': 'Approved'}).eq('id', request.form.get('user_id')).execute()
                msg = "Χρήστης εγκρίθηκε."
            elif act == 'revoke' and check_admin_permission(user, 'manage_users'):
                supabase.table('users').update({'status': 'Pending', 'role': 'User', 'admin_level': 0}).eq('id', request.form.get('user_id')).execute()
                msg = "Δικαιώματα αφαιρέθηκαν."
            elif act == 'set_admin_role' and is_super:
                uid, lvl = request.form.get('target_user_id'), int(request.form.get('admin_level_val', 2))
                perms = ",".join(request.form.getlist('custom_perms')) if lvl == 4 else ""
                supabase.table('users').update({'role': 'Admin', 'admin_level': lvl, 'custom_permissions': perms}).eq('id', uid).execute()
                msg = "Ρόλος διαχειριστή ενημερώθηκε."
            elif act == 'toggle_roster':
                set_setting('roster_enabled', '1' if not is_roster_active() else '0')
                msg = "Η κατάσταση Roster ενημερώθηκε."
            elif act == 'change_password' and is_super:
                set_setting('admin_password', generate_password_hash(request.form.get('new_password')))
                msg = "Ο κεντρικός κωδικός άλλαξε."
        else: msg = "Λάθος κωδικός διαχειριστή."

    users = supabase.table('users').select('*').order('id').execute().data
    return render_template_string(BASE_STYLE + """
    <div class="container">
        <h2>Κεντρικό Πάνελ Διαχείρισης</h2>
        <div class="nav-buttons"><a href="/" class="btn-link" style="background:#475569;">Επιστροφή</a></div>
        <p style="color:red; font-weight:bold;">{{ msg }}</p>
        
        <form method="POST" style="background:#f1f5f9; padding:15px; border-radius:5px;">
            Κωδικός Διαχειριστή: <input type="password" name="admin_password" required style="width:250px;"><br><br>
            <p>Κατάσταση Roster: <b>{% if roster_active %}ΕΝΕΡΓΟ{% else %}ΑΝΕΝΕΡΓΟ{% endif %}</b></p>
            <button type="submit" name="action" value="toggle_roster" class="{% if roster_active %}btn-danger{% else %}btn-success{% endif %}">{% if roster_active %}Απενεργοποίηση Roster{% else %}Ενεργοποίηση Roster{% endif %}</button>
        </form>

        {% if is_super %}
        <hr>
        <h3>👑 Ορισμός Ιεραρχίας Διαχειριστών</h3>
        <form method="POST" style="background:#eff6ff; padding:15px; border-radius:5px;">
            <input type="hidden" name="action" value="set_admin_role">
            Επιλογή Χρήστη: <select name="target_user_id" required>{% for u in users %}{% if u.status == 'Approved' %}<option value="{{ u.id }}">{{ u.name }} (Lvl: {{ u.admin_level }})</option>{% endif %}{% endfor %}</select><br>
            Βαθμίδα: <select name="admin_level_val"><option value="1">Super Admin</option><option value="2">Κανονικός Διαχειριστής</option><option value="4">Περιορισμένος</option></select><br>
            <div style="font-size:12px;"><label><input type="checkbox" name="custom_perms" value="manage_users"> Έγκριση Χρηστών</label></div>
            Κωδικός Διαχειριστή: <input type="password" name="admin_password" required><button type="submit" class="btn-success">Ανάθεση</button>
        </form>
        {% endif %}

        <hr>
        <h3>Έλεγχος Μελών & Εγκρίσεων</h3>
        <table>
            <tr><th>Όνομα</th><th>Σταθμός</th><th>Κατάσταση</th><th>Ρόλος</th><th>Ενέργειες</th></tr>
            {% for u in users %}
            <tr>
                <td>{{ u.name }}</td><td>{{ u.station }}</td><td>{{ u.status }}</td><td>{{ u.role }}</td>
                <td>
                    <form method="POST" style="display:inline;">
                        <input type="hidden" name="user_id" value="{{ u.id }}">
                        {% if u.status == 'Pending' %}
                            <button type="submit" name="action" value="approve" class="btn-success" style="padding:4px;" onclick="this.form.admin_password.value=prompt('Κωδικός Διαχειριστή:');">Έγκριση</button>
                        {% else %}
                            <button type="submit" name="action" value="revoke" class="btn-danger" style="padding:4px;" onclick="this.form.admin_password.value=prompt('Κωδικός Διαχειριστή:');">Αφαίρεση</button>
                        {% endif %}
                        <input type="hidden" name="admin_password">
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    """, users=users, msg=msg, roster_active=is_roster_active(), is_super=is_super)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)