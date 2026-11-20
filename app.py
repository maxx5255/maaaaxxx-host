"""
╔═══════════════════════════════════════════════════════════════╗
║  MAXX HOSTING - Professional Flask Backend v2.0               ║
╠═══════════════════════════════════════════════════════════════╣
║  • Multi-admin (admin/admin123 + MAXX/MAXX@2010)              ║
║  • Landing page auth (register + login)                       ║
║  • Plans CRUD + Orders management                             ║
║  • Referral system (5 refs → free panel)                      ║
║  • Backups API + Task Scheduler API                           ║
║  • File download + path-traversal protection                  ║
║  • GitHub deploy (no git required)                            ║
║  • Health checks, structured logging, atomic JSON             ║
║  • Thread-safe persistence with locks                         ║
╚═══════════════════════════════════════════════════════════════╝
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import json, os, subprocess, random, string, uuid, sys, shutil, threading, time, zipfile
from datetime import datetime, timedelta
from functools import wraps
from copy import deepcopy

try:
    import psutil
except ImportError:
    print("Install psutil: pip install psutil")
    sys.exit(1)

try:
    import requests
except ImportError:
    requests = None
    print("Warning: requests not installed — GitHub deploy will be disabled")

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

class Config:
    SECRET_KEY = os.environ.get('MAXX_SECRET', 'maxx-hosting-secret-2026-change-in-prod')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024
    DEBUG = os.environ.get('MAXX_DEBUG', 'false').lower() == 'true'
    HOST = os.environ.get('MAXX_HOST', '0.0.0.0')
    PORT = int(os.environ.get('MAXX_PORT', '5000'))

    BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
    USERS_FILE = os.path.join(BASE_DIR, 'users.json')
    PLANS_FILE = os.path.join(BASE_DIR, 'plans.json')
    ORDERS_FILE= os.path.join(BASE_DIR, 'orders.json')
    BOTS_DIR   = os.path.join(BASE_DIR, 'bots')
    LOG_DIR    = os.path.join(BASE_DIR, 'logs')
    BACKUP_DIR = '_backups'        # inside each bots/<server_id>/

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH

os.makedirs(Config.BOTS_DIR, exist_ok=True)
os.makedirs(Config.LOG_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

import logging
from logging.handlers import RotatingFileHandler

log_file = os.path.join(Config.LOG_DIR, 'app.log')
handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger = logging.getLogger('maxx')
logger.setLevel(logging.INFO)
logger.addHandler(handler)
if Config.DEBUG:
    logger.addHandler(logging.StreamHandler())

# ═══════════════════════════════════════════════════════════════
# DEFAULT DATA
# ═══════════════════════════════════════════════════════════════

DEFAULT_USERS = {
    'admin': {
        'username': 'admin', 'password': 'admin123',
        'role': 'admin', 'email': 'admin@maxx.local', 'servers': []
    },
    'MAXX': {
        'username': 'MAXX', 'password': 'MAXX@2010',
        'role': 'admin', 'email': 'maxx@maxx.local', 'servers': []
    }
}

DEFAULT_PLANS = [
    {
        'id': 'free', 'name': 'Free', 'icon': '🆓', 'price': 0,
        'period': 'Via Referrals', 'ram': '1GB', 'disk': '1GB', 'cpu': '30%',
        'validDays': 3, 'referralsRequired': 5,
        'features': ['5 Referrals Required', '3 Days Validity', '1GB RAM / 1GB Disk', '30% CPU Limit'],
        'popular': False, 'active': True
    },
    {
        'id': 'standard', 'name': 'Standard', 'icon': '⭐', 'price': 2,
        'period': '7 Days Panel', 'ram': '1GB', 'disk': '1GB', 'cpu': '50%',
        'validDays': 7, 'referralsRequired': 0,
        'features': ['No Referrals Needed', '7 Days Validity', '1GB RAM / 1GB Disk', '50% CPU Limit'],
        'popular': True, 'active': True
    },
    {
        'id': 'premium', 'name': 'Premium', 'icon': '💎', 'price': 5,
        'period': '30 Days Panel', 'ram': '2GB', 'disk': '2GB', 'cpu': '80%',
        'validDays': 30, 'referralsRequired': 0,
        'features': ['No Referrals Needed', '30 Days Validity', '2GB RAM / 2GB Disk', '80% CPU Limit'],
        'popular': False, 'active': True
    }
]

# ═══════════════════════════════════════════════════════════════
# THREAD-SAFE JSON STORE (atomic writes + corruption recovery)
# ═══════════════════════════════════════════════════════════════

class JSONStore:
    def __init__(self, path, default):
        self.path = path
        self.default = default
        self._lock = threading.RLock()

    def load(self):
        with self._lock:
            if not os.path.exists(self.path):
                self._save(self.default)
                return deepcopy(self.default)
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Corrupted {self.path}: {e} — resetting")
                backup = self.path + '.corrupt.' + str(int(time.time()))
                try: shutil.copy(self.path, backup)
                except: pass
                self._save(self.default)
                return deepcopy(self.default)

    def save(self, data):
        with self._lock:
            self._save(data)

    def _save(self, data):
        tmp = self.path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            logger.error(f"Save failed for {self.path}: {e}")

users_store  = JSONStore(Config.USERS_FILE,  DEFAULT_USERS)
plans_store  = JSONStore(Config.PLANS_FILE,  DEFAULT_PLANS)
orders_store = JSONStore(Config.ORDERS_FILE, [])

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def load_users():
    data = users_store.load()
    # Ensure both admins exist
    changed = False
    for uname, default in DEFAULT_USERS.items():
        if uname not in data:
            data[uname] = deepcopy(default)
            changed = True
    if changed:
        users_store.save(data)
    return data

def save_users(data):
    users_store.save(data)

def load_plans():
    return plans_store.load()

def save_plans(plans):
    plans_store.save(plans)

def load_orders():
    return orders_store.load()

def save_orders(orders):
    orders_store.save(orders)

def generate_password(length=12):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

def generate_order_id():
    return 'MAX-' + uuid.uuid4().hex[:8].upper()

def generate_referral_code():
    return uuid.uuid4().hex[:8].upper()

def get_server_dir(server_id):
    d = os.path.join(Config.BOTS_DIR, server_id)
    os.makedirs(d, exist_ok=True)
    return d

def safe_path(server_id, user_path):
    """Return absolute path if inside server_dir, else None (path traversal protection)"""
    server_dir = os.path.abspath(get_server_dir(server_id))
    try:
        abs_path = os.path.abspath(os.path.join(server_dir, user_path))
    except:
        return None
    if not (abs_path == server_dir or abs_path.startswith(server_dir + os.sep)):
        return None
    return abs_path

def find_server(server_id):
    """Returns (owner_username, server_dict) or (None, None)"""
    users = load_users()
    for uname, data in users.items():
        if data.get('role') == 'admin':
            continue
        for s in data.get('servers', []):
            if isinstance(s, dict) and s.get('server_id') == server_id:
                return uname, s
    return None, None

def update_server(server_id, updater_fn):
    """Apply updater_fn(server_dict). Returns True if found & updated."""
    users = load_users()
    for uname, data in users.items():
        if data.get('role') == 'admin':
            continue
        for i, s in enumerate(data.get('servers', [])):
            if isinstance(s, dict) and s.get('server_id') == server_id:
                updater_fn(s)
                data['servers'][i] = s
                save_users(users)
                return True
    return False

def check_server_status(server_id):
    """Returns (status, server_dict_or_None). status ∈ {'ok','expired','deleted'}"""
    owner, server = find_server(server_id)
    if not server:
        return 'deleted', None
    expiry = server.get('expiry', '')
    if expiry:
        try:
            exp_date = datetime.strptime(expiry, '%Y-%m-%d %H:%M:%S.%f')
            if datetime.now() > exp_date:
                return 'expired', server
        except:
            pass
    return 'ok', server

def is_admin_user(username):
    users = load_users()
    return users.get(username, {}).get('role') == 'admin'

def create_default_files(server_dir):
    main_py = os.path.join(server_dir, 'main.py')
    if not os.path.exists(main_py):
        with open(main_py, 'w', encoding='utf-8') as f:
            f.write('''# MAXX HOSTING - Default Bot
import time

print("=" * 40)
print("Bot is running on MAXX HOSTING")
print("Server is ready!")
print("=" * 40)

counter = 0
while True:
    counter += 1
    print(f"[{time.strftime('%H:%M:%S')}] Heartbeat #{counter} | Server active")
    time.sleep(10)
''')
    req_file = os.path.join(server_dir, 'requirements.txt')
    if not os.path.exists(req_file):
        with open(req_file, 'w', encoding='utf-8') as f:
            f.write('# Add your pip packages here\n')

# ═══════════════════════════════════════════════════════════════
# AUTH DECORATORS
# ═══════════════════════════════════════════════════════════════

def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get('role') != 'admin':
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper

def require_login(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Login required'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper

# ═══════════════════════════════════════════════════════════════
# RATE LIMITER (improved)
# ═══════════════════════════════════════════════════════════════

CPU_HISTORY = {}
_hist_lock = threading.Lock()

def check_rate(server_id, limit_percent, pid):
    if not pid:
        return False, 0
    try:
        proc = psutil.Process(pid)
        cpu = proc.cpu_percent(interval=1)
        now = time.time()
        with _hist_lock:
            CPU_HISTORY.setdefault(server_id, [])
            CPU_HISTORY[server_id].append({'t': now, 'c': cpu})
            CPU_HISTORY[server_id] = [h for h in CPU_HISTORY[server_id] if now - h['t'] < 30]
            recent = [h['c'] for h in CPU_HISTORY[server_id] if now - h['t'] < 10]
        if recent:
            avg = sum(recent) / len(recent)
            if avg > limit_percent:
                return True, avg
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False, 0
    return False, 0

CRASH_COUNT = {}
_crash_lock = threading.Lock()

def should_auto_restart(server_id):
    with _crash_lock:
        now = time.time()
        info = CRASH_COUNT.setdefault(server_id, {'count': 0, 'last': now})
        if now - info['last'] >= 60:
            info['count'] = 0
        if info['count'] >= 3:
            return False
        info['count'] += 1
        info['last'] = now
        return True

# ═══════════════════════════════════════════════════════════════
# PROCESS MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def _log_line(log_file, line):
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except:
        pass

def run_bot(server_id, main_file='main.py', requirements_file='requirements.txt'):
    server_dir = get_server_dir(server_id)
    main_path  = os.path.join(server_dir, main_file)
    log_file   = os.path.join(server_dir, 'output.log')
    python_exe = sys.executable

    if not os.path.exists(main_path):
        return None, f"ERROR: {main_file} not found!"

    try:
        open(log_file, 'w').close()
    except:
        pass

    ts = lambda: datetime.now().strftime('%I:%M:%S %p')

    _, server = find_server(server_id)
    cpu_limit = (server or {}).get('cpu_limit', 80)

    _log_line(log_file, f"[{ts()}] Rate limit: {cpu_limit}%")

    # Install requirements
    if requirements_file and requirements_file.strip():
        req_path = os.path.join(server_dir, requirements_file.strip())
        if os.path.exists(req_path):
            try:
                with open(req_path, 'r', encoding='utf-8') as f:
                    pkgs = [l.strip() for l in f if l.strip() and not l.strip().startswith('#')]
                if pkgs:
                    _log_line(log_file, f"[{ts()}] Installing {len(pkgs)} package(s)...")
                    proc = subprocess.Popen(
                        [python_exe, '-m', 'pip', 'install', '-r', os.path.abspath(req_path),
                         '--disable-pip-version-check', '--no-warn-script-location'],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                    )
                    for line in iter(proc.stdout.readline, ''):
                        if line.strip():
                            _log_line(log_file, f"[{ts()}] {line.rstrip()}")
                    proc.wait()
                    _log_line(log_file, f"[{ts()}] Requirements done")
            except Exception as e:
                _log_line(log_file, f"[{ts()}] pip error: {e}")

    _log_line(log_file, "")
    _log_line(log_file, f"[{ts()}] Run: python {main_file}")

    try:
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED']  = '1'

        proc = subprocess.Popen(
            [python_exe, os.path.abspath(main_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=server_dir,
            text=True, encoding='utf-8', errors='replace', bufsize=1, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )

        _log_line(log_file, f"[{ts()}] PID: {proc.pid}")
        _log_line(log_file, "")

        # Rate monitor thread
        def rate_monitor():
            while proc.poll() is None:
                time.sleep(5)
                exceeded, avg = check_rate(server_id, cpu_limit, proc.pid)
                if exceeded:
                    _log_line(log_file, f"[{datetime.now().strftime('%I:%M:%S %p')}] CPU EXCEEDED {avg:.1f}% > {cpu_limit}%")
                    try:
                        proc.terminate()
                        time.sleep(2)
                        if proc.poll() is None:
                            proc.kill()
                    except: pass
                    update_server(server_id, lambda s: s.update(
                        status='stopped', pid=None,
                        rate_limit_exceeded=True, stopped_by_user=False
                    ))
                    break
        threading.Thread(target=rate_monitor, daemon=True).start()

        # Output streaming thread
        def stream_output():
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    for line in iter(proc.stdout.readline, ''):
                        if line:
                            line = line.rstrip('\n\r')
                            if line:
                                f.write(f"[{datetime.now().strftime('%I:%M:%S %p')}] {line}\n")
                                f.flush()
            except: pass
        threading.Thread(target=stream_output, daemon=True).start()

        return proc.pid, None

    except Exception as e:
        _log_line(log_file, f"[{ts()}] Error: {e}")
        return None, str(e)

def stop_bot_process(pid):
    try:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True)
        else:
            os.kill(pid, 15)
        return True
    except:
        return False

def monitor_bot(server_id, pid):
    """Watchdog — auto-restart on crash (unless user-stopped or rate-limited)"""
    while True:
        try:
            if sys.platform == 'win32':
                r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True)
                if str(pid) not in r.stdout:
                    break
            else:
                try: os.kill(pid, 0)
                except: break
        except:
            break
        time.sleep(5)

    _, server = find_server(server_id)
    if not server:
        return
    if server.get('stopped_by_user') or server.get('rate_limit_exceeded'):
        return

    if should_auto_restart(server_id):
        time.sleep(3)
        new_pid, _ = run_bot(
            server_id,
            server.get('main_file', 'main.py'),
            server.get('requirements_file', 'requirements.txt')
        )
        if new_pid:
            update_server(server_id, lambda s: s.update(
                status='running', pid=new_pid,
                started_at=str(datetime.now()),
                rate_limit_exceeded=False, stopped_by_user=False
            ))
            threading.Thread(target=monitor_bot, args=(server_id, new_pid), daemon=True).start()
    else:
        update_server(server_id, lambda s: s.update(status='stopped', pid=None))

def get_process_stats(pid):
    try:
        proc = psutil.Process(pid)
        cpu = proc.cpu_percent(interval=0.3)
        mem = proc.memory_info().rss / (1024 * 1024)
        return {
            'cpu': round(cpu, 1),
            'ram_mb': round(mem, 1),
            'ram_display': f"{mem:.1f} MB" if mem < 1024 else f"{mem/1024:.1f} GB"
        }
    except:
        return {'cpu': 0, 'ram_mb': 0, 'ram_display': '0 MB'}

def get_network_stats(pid):
    try:
        io = psutil.Process(pid).io_counters()
        return _fmt_kb(io.read_bytes / 1024), _fmt_kb(io.write_bytes / 1024)
    except:
        return "0 KB", "0 KB"

def _fmt_kb(kb):
    if kb < 1024: return f"{kb:.1f} KB"
    mb = kb / 1024
    if mb < 1024: return f"{mb:.1f} MB"
    return f"{mb/1024:.2f} GB"

# ═══════════════════════════════════════════════════════════════
# PUBLIC / LANDING
# ═══════════════════════════════════════════════════════════════

@app.route('/')
@app.route('/landing')
def index():
    return render_template('landing.html')

@app.route('/health')
@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'version': '2.0',
        'users': len([u for u in load_users().values() if u.get('role') != 'admin']),
        'plans': len(load_plans()),
        'orders': len(load_orders()),
        'time': datetime.now().isoformat()
    })

# ═══════════════════════════════════════════════════════════════
# LANDING AUTH (register / login)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not username or len(username) < 3:
        return jsonify({'error': 'Username must be at least 3 characters'}), 400
    if not email or '@' not in email:
        return jsonify({'error': 'Valid email required'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    users = load_users()
    for u in users.values():
        if u.get('username', '').lower() == username.lower():
            return jsonify({'error': 'Username already taken'}), 400
        if u.get('email', '').lower() == email:
            return jsonify({'error': 'Email already registered'}), 400

    users[username] = {
        'username': username,
        'email': email,
        'password': password,
        'role': 'user',
        'servers': [],
        'referral_code': generate_referral_code(),
        'referrals': 0,
        'referred_by': session.get('referrer'),
        'created': str(datetime.now())
    }
    save_users(users)

    # Credit referrer
    referrer = session.get('referrer')
    if referrer and referrer in users and users[referrer].get('role') != 'admin':
        users[referrer]['referrals'] = users[referrer].get('referrals', 0) + 1
        save_users(users)

    session.permanent = True
    app.permanent_session_lifetime = timedelta(days=30)
    session['user']    = username
    session['role']    = 'user'
    session['email']   = email
    session.pop('referrer', None)

    logger.info(f"Registered: {username}")
    return jsonify({
        'success': True,
        'username': username,
        'email': email,
        'referral_code': users[username]['referral_code']
    })

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.get_json() or {}
    identifier = (data.get('identifier') or '').strip()
    password = data.get('password') or ''

    users = load_users()
    for uname, udata in users.items():
        if udata.get('role') == 'admin':
            continue
        if (uname.lower() == identifier.lower() or
            udata.get('email', '').lower() == identifier.lower()):
            if udata.get('password') == password:
                session.permanent = True
                app.permanent_session_lifetime = timedelta(days=30)
                session['user']  = uname
                session['role']  = 'user'
                session['email'] = udata.get('email', '')
                logger.info(f"Login: {uname}")
                return jsonify({'success': True, 'username': uname, 'email': udata.get('email', '')})
            break
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/auth/me')
def api_me():
    if 'user' not in session:
        return jsonify({'logged_in': False}), 200
    users = load_users()
    u = users.get(session['user'], {})
    return jsonify({
        'logged_in': True,
        'username': session['user'],
        'email': u.get('email', ''),
        'role': u.get('role', 'user'),
        'referrals': u.get('referrals', 0),
        'referral_code': u.get('referral_code', '')
    })

# ═══════════════════════════════════════════════════════════════
# REFERRAL
# ═══════════════════════════════════════════════════════════════

@app.route('/ref/<code>')
def referral_link(code):
    users = load_users()
    for uname, data in users.items():
        if data.get('role') == 'admin':
            continue
        if data.get('referral_code') == code:
            session['referrer'] = uname
            session['referrer_code'] = code
            break
    return redirect(url_for('index'))

# ═══════════════════════════════════════════════════════════════
# PLANS API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/plans')
def api_plans_list():
    """Public — only active plans"""
    plans = [p for p in load_plans() if p.get('active', True)]
    return jsonify(plans)

@app.route('/api/plans/all')
@require_admin
def api_plans_all():
    return jsonify(load_plans())

@app.route('/api/plans', methods=['POST'])
@require_admin
def api_plans_create():
    data = request.get_json() or {}
    if not data.get('name'):
        return jsonify({'error': 'Plan name required'}), 400
    data['id'] = data.get('id') or ('plan-' + uuid.uuid4().hex[:6])
    plans = load_plans()
    plans.append(data)
    save_plans(plans)
    logger.info(f"Plan created: {data['id']}")
    return jsonify({'success': True, 'plan': data})

@app.route('/api/plans/<plan_id>', methods=['PUT', 'PATCH'])
@require_admin
def api_plans_update(plan_id):
    data = request.get_json() or {}
    plans = load_plans()
    for i, p in enumerate(plans):
        if p.get('id') == plan_id:
            data['id'] = plan_id
            plans[i] = {**p, **data}
            save_plans(plans)
            return jsonify({'success': True, 'plan': plans[i]})
    return jsonify({'error': 'Plan not found'}), 404

@app.route('/api/plans/<plan_id>', methods=['DELETE'])
@require_admin
def api_plans_delete(plan_id):
    plans = [p for p in load_plans() if p.get('id') != plan_id]
    save_plans(plans)
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════
# ORDERS API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/orders', methods=['POST'])
@require_login
def api_orders_create():
    data = request.get_json() or {}
    plan_id = data.get('plan_id')
    plans = load_plans()
    plan = next((p for p in plans if p.get('id') == plan_id), None)
    if not plan:
        return jsonify({'error': 'Plan not found'}), 404

    order = {
        'id': generate_order_id(),
        'username': session['user'],
        'email': session.get('email', ''),
        'plan_id': plan['id'],
        'plan_name': plan['name'],
        'price': plan.get('price', 0),
        'valid_days': plan.get('validDays', 30),
        'ram': plan.get('ram', '1GB'),
        'disk': plan.get('disk', '1GB'),
        'cpu': plan.get('cpu', '50%'),
        'purchased_at': str(datetime.now()),
        'status': 'pending',
        'activated_at': None,
        'server_id': None
    }
    orders = load_orders()
    orders.append(order)
    save_orders(orders)
    logger.info(f"Order created: {order['id']} for {session['user']}")
    return jsonify({'success': True, 'order': order})

@app.route('/api/orders')
@require_login
def api_orders_mine():
    orders = [o for o in load_orders() if o.get('username') == session['user']]
    return jsonify(orders)

@app.route('/api/orders/all')
@require_admin
def api_orders_all():
    return jsonify(load_orders())

@app.route('/api/orders/<order_id>/activate', methods=['POST'])
@require_admin
def api_orders_activate(order_id):
    """Admin activates order — creates a server for the user"""
    orders = load_orders()
    order = next((o for o in orders if o['id'] == order_id), None)
    if not order:
        return jsonify({'error': 'Order not found'}), 404
    if order['status'] == 'active':
        return jsonify({'error': 'Already activated'}), 400

    users = load_users()
    if order['username'] not in users:
        return jsonify({'error': 'User not found'}), 404

    # Create server
    server_id = uuid.uuid4().hex[:8]
    expiry_date = datetime.now() + timedelta(days=int(order.get('valid_days', 30)))
    create_default_files(get_server_dir(server_id))

    cpu_str = str(order.get('cpu', '50%')).replace('%', '').strip()
    cpu_limit = int(cpu_str) if cpu_str.isdigit() else 50

    new_server = {
        'server_id': server_id,
        'login_url': f"/{server_id}/login",
        'dashboard_url': f"/{server_id}/home",
        'type': 'python',
        'ram': order.get('ram', '1GB'),
        'disk': order.get('disk', '1GB'),
        'status': 'stopped',
        'pid': None,
        'created': str(datetime.now()),
        'expiry': str(expiry_date),
        'main_file': 'main.py',
        'requirements_file': 'requirements.txt',
        'cpu_limit': cpu_limit,
        'rate_limit_exceeded': False,
        'stopped_by_user': False,
        'backups': [],
        'scheduled_tasks': []
    }

    users[order['username']].setdefault('servers', []).append(new_server)
    save_users(users)

    order['status'] = 'active'
    order['activated_at'] = str(datetime.now())
    order['server_id'] = server_id
    save_orders(orders)

    logger.info(f"Order activated: {order_id} → server {server_id}")
    return jsonify({'success': True, 'server_id': server_id, 'order': order})

@app.route('/api/orders/<order_id>/cancel', methods=['POST'])
@require_admin
def api_orders_cancel(order_id):
    orders = load_orders()
    for o in orders:
        if o['id'] == order_id:
            o['status'] = 'cancelled'
            save_orders(orders)
            return jsonify({'success': True})
    return jsonify({'error': 'Order not found'}), 404

# ═══════════════════════════════════════════════════════════════
# SERVER LOGIN / HOME
# ═══════════════════════════════════════════════════════════════

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Generic admin login"""
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        users = load_users()
        u = users.get(username)
        if u and u.get('role') == 'admin' and u.get('password') == password:
            session['user'] = username
            session['role'] = 'admin'
            logger.info(f"Admin login: {username}")
            return redirect(url_for('admin_dashboard'))
        return render_template('login.html', error="Invalid credentials!")
    return render_template('login.html', error=None)

@app.route('/<server_id>/login', methods=['GET', 'POST'])
def server_login(server_id):
    status, server = check_server_status(server_id)
    if status != 'ok':
        return render_template('error.html', error_type=status, current_server=server)

    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        users = load_users()
        u = users.get(username)
        if u and u.get('role') != 'admin' and u.get('password') == password:
            # Verify they own this server
            owns = any(s.get('server_id') == server_id for s in u.get('servers', []) if isinstance(s, dict))
            if owns:
                session['user'] = username
                session['role'] = 'user'
                session['current_server_id'] = server_id
                return redirect(url_for('server_home', server_id=server_id))
        return render_template('login.html', error="Invalid credentials!")
    return render_template('login.html', error=None)

@app.route('/<server_id>/home')
def server_home(server_id):
    if 'user' not in session or session.get('role') != 'user':
        return redirect(url_for('server_login', server_id=server_id))
    if session.get('current_server_id') != server_id:
        session.clear()
        return redirect(url_for('server_login', server_id=server_id))

    status, server = check_server_status(server_id)
    if status != 'ok':
        session.clear()
        return render_template('error.html', error_type=status, current_server=server)

    return render_template('home.html', username=session['user'], current_server=server)

@app.route('/logout')
def logout():
    sid = session.get('current_server_id')
    session.clear()
    if sid:
        return redirect(url_for('server_login', server_id=sid))
    return redirect(url_for('login'))

# ═══════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════

@app.route('/admin')
@require_admin
def admin_dashboard():
    users = load_users()
    user_list = []
    total_servers = 0
    total_running = 0
    for uname, data in users.items():
        if data.get('role') == 'admin':
            continue
        servers = data.get('servers', [])
        if not isinstance(servers, list):
            servers = []
        running = sum(1 for s in servers if isinstance(s, dict) and s.get('status') == 'running')
        total_servers += len(servers)
        total_running += running
        user_list.append({
            'username': uname,
            'password': data.get('password', ''),
            'servers': servers,
            'server_count': len(servers),
            'running_count': running
        })
    return render_template(
        'admin.html',
        users=user_list,
        total_servers=total_servers,
        total_running=total_running
    )

@app.route('/admin/create_server', methods=['POST'])
@require_admin
def create_server():
    data = request.get_json() or {}
    username = data.get('username', '')
    password = data.get('password', '')
    server_type = data.get('server_type', 'python')
    ram   = data.get('ram', '512MB')
    disk  = data.get('disk', '1GB')
    days  = int(data.get('expiry_days', 30))
    cpu   = int(data.get('cpu_limit', 80))

    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400

    users = load_users()
    server_id = uuid.uuid4().hex[:8]
    expiry_date = datetime.now() + timedelta(days=days)
    create_default_files(get_server_dir(server_id))

    new_server = {
        'server_id': server_id,
        'login_url': f"/{server_id}/login",
        'dashboard_url': f"/{server_id}/home",
        'full_link': request.host_url.rstrip('/') + f"/{server_id}/home",
        'type': server_type,
        'ram': ram, 'disk': disk,
        'status': 'stopped', 'pid': None,
        'created': str(datetime.now()),
        'expiry': str(expiry_date),
        'main_file': 'main.py',
        'requirements_file': 'requirements.txt',
        'cpu_limit': cpu,
        'rate_limit_exceeded': False,
        'stopped_by_user': False,
        'backups': [],
        'scheduled_tasks': []
    }

    if username not in users:
        users[username] = {
            'username': username, 'password': password,
            'role': 'user', 'email': '',
            'servers': [],
            'referral_code': generate_referral_code(),
            'referrals': 0,
            'created': str(datetime.now())
        }
    users[username].setdefault('servers', []).append(new_server)
    save_users(users)
    logger.info(f"Server {server_id} created for {username}")

    return jsonify({
        'success': True,
        'username': username,
        'password': password,
        'login_url': new_server['login_url'],
        'hostname': new_server['full_link'],
        'server_id': server_id
    })

@app.route('/admin/set_rate_limit/<server_id>', methods=['POST'])
@require_admin
def set_rate_limit(server_id):
    cpu = int((request.get_json() or {}).get('cpu_limit', 80))
    ok = update_server(server_id, lambda s: s.update(cpu_limit=cpu))
    return jsonify({'success': ok, 'cpu_limit': cpu}) if ok else (jsonify({'error': 'Not found'}), 404)

@app.route('/admin/delete_server/<username>/<server_id>', methods=['POST'])
@require_admin
def delete_server(username, server_id):
    users = load_users()
    if username in users:
        servers = users[username].get('servers', [])
        for s in servers:
            if isinstance(s, dict) and s.get('server_id') == server_id:
                if s.get('pid'):
                    stop_bot_process(s['pid'])
                try: shutil.rmtree(get_server_dir(server_id))
                except: pass
                break
        users[username]['servers'] = [s for s in servers if not (isinstance(s, dict) and s.get('server_id') == server_id)]
        if not users[username]['servers']:
            del users[username]
        save_users(users)
    logger.info(f"Server {server_id} deleted")
    return jsonify({'success': True})

@app.route('/admin/server_action', methods=['POST'])
@app.route('/api/server_action', methods=['POST'])
def server_action():
    """Bulk actions — admin OR server owner"""
    if 'user' not in session:
        return jsonify({'error': 'Login required'}), 401

    data = request.get_json() or {}
    server_id = data.get('server_id', '')
    action    = data.get('action', '')

    _, server = find_server(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404

    # Permission: admin or owner
    if session.get('role') != 'admin':
        if session.get('user') != data.get('username'):
            return jsonify({'error': 'Forbidden'}), 403

    if action == 'stop':
        if server.get('pid'):
            stop_bot_process(server['pid'])
        update_server(server_id, lambda s: s.update(status='stopped', pid=None, stopped_by_user=True))
    elif action == 'restart':
        if server.get('pid'):
            stop_bot_process(server['pid'])
        time.sleep(1)
        pid, err = run_bot(server_id, server.get('main_file', 'main.py'), server.get('requirements_file', 'requirements.txt'))
        if pid:
            update_server(server_id, lambda s: s.update(
                status='running', pid=pid, started_at=str(datetime.now()),
                rate_limit_exceeded=False, stopped_by_user=False
            ))
            threading.Thread(target=monitor_bot, args=(server_id, pid), daemon=True).start()
    elif action == 'backup':
        pass  # handled by backup endpoint
    else:
        return jsonify({'error': f'Unknown action: {action}'}), 400

    return jsonify({'success': True, 'action': action})

# ═══════════════════════════════════════════════════════════════
# BOT CONTROL API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/run/<server_id>', methods=['POST'])
@require_login
def api_run(server_id):
    _, server = find_server(server_id)
    if not server:
        return jsonify({'status': 'error', 'msg': 'Not found'})

    # Permission
    if session.get('role') != 'admin' and session.get('user') != find_server(server_id)[0]:
        return jsonify({'status': 'error', 'msg': 'Forbidden'}), 403

    if server.get('status') == 'running':
        return jsonify({'status': 'error', 'msg': 'Already running!'})

    update_server(server_id, lambda s: s.update(rate_limit_exceeded=False, stopped_by_user=False))
    pid, err = run_bot(server_id, server.get('main_file', 'main.py'), server.get('requirements_file', 'requirements.txt'))
    if pid:
        update_server(server_id, lambda s: s.update(
            status='running', pid=pid, started_at=str(datetime.now())
        ))
        threading.Thread(target=monitor_bot, args=(server_id, pid), daemon=True).start()
        return jsonify({'status': 'success', 'msg': 'Started'})
    return jsonify({'status': 'error', 'msg': err or 'Failed'})

@app.route('/api/stop/<server_id>', methods=['POST'])
@require_login
def api_stop(server_id):
    owner, server = find_server(server_id)
    if not server:
        return jsonify({'status': 'error', 'msg': 'Not found'})
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'status': 'error', 'msg': 'Forbidden'}), 403

    if server.get('pid'):
        stop_bot_process(server['pid'])
    update_server(server_id, lambda s: s.update(status='stopped', pid=None, stopped_by_user=True))

    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    _log_line(log_file, f"\n[{datetime.now().strftime('%I:%M:%S %p')}] Stopped by user")
    return jsonify({'status': 'success', 'msg': 'Stopped'})

@app.route('/api/logs/<server_id>')
@require_login
def api_logs(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'logs': '', 'error': 'Forbidden'}), 403
    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    logs = ''
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                logs = f.read()
        except:
            pass
    return jsonify({'logs': logs})

@app.route('/api/clear_logs/<server_id>', methods=['POST'])
@require_login
def api_clear_logs(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'status': 'error'}), 403
    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    try:
        open(log_file, 'w').close()
        return jsonify({'status': 'success'})
    except:
        return jsonify({'status': 'error'}), 500

@app.route('/api/command', methods=['POST'])
@require_login
def api_command():
    data = request.get_json() or {}
    cmd = data.get('cmd', '')
    server_id = data.get('server_id', '')
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'status': 'error', 'msg': 'Forbidden'}), 403

    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            cwd=get_server_dir(server_id), timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        output = (r.stdout + r.stderr)[:2000]
        log_file = os.path.join(get_server_dir(server_id), 'output.log')
        _log_line(log_file, f"[{datetime.now().strftime('%I:%M:%S %p')}] $ {cmd}\n{output}")
        return jsonify({'status': 'success', 'output': output})
    except subprocess.TimeoutExpired:
        return jsonify({'status': 'error', 'msg': 'Timeout'})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)})

@app.route('/api/stats/<server_id>')
@require_login
def api_stats(server_id):
    owner, server = find_server(server_id)
    if not server:
        return jsonify({
            'cpu': '0%', 'ram': '0 MB', 'uptime': '0h', 'status': 'unknown',
            'cpu_limit': 80, 'net_in': '0 KB', 'net_out': '0 KB'
        })
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403

    uptime, cpu, ram, nin, nout = "0h 0m", "0%", "0 MB", "0 KB", "0 KB"

    if server.get('status') == 'running' and server.get('pid'):
        try:
            s = get_process_stats(server['pid'])
            cpu = f"{s['cpu']}%"
            ram = s['ram_display']
            nin, nout = get_network_stats(server['pid'])
        except:
            pass

    if server.get('status') == 'running' and server.get('started_at'):
        try:
            start = datetime.strptime(server['started_at'], '%Y-%m-%d %H:%M:%S.%f')
            diff = datetime.now() - start
            secs = int(diff.total_seconds())
            if diff.days > 0:
                uptime = f"{diff.days}d {secs//3600 % 24}h"
            else:
                uptime = f"{secs//3600}h {(secs%3600)//60}m {secs%60}s"
        except:
            pass

    return jsonify({
        'cpu': cpu, 'ram': ram, 'uptime': uptime,
        'net_in': nin, 'net_out': nout,
        'cpu_limit': server.get('cpu_limit', 80),
        'status': server.get('status', 'stopped'),
        'uptime_seconds': (
            int((datetime.now() - datetime.strptime(server['started_at'], '%Y-%m-%d %H:%M:%S.%f')).total_seconds())
            if server.get('started_at') and server.get('status') == 'running' else 0
        )
    })

@app.route('/api/change_password/<server_id>', methods=['POST'])
@require_login
def api_change_password(server_id):
    data = request.get_json() or {}
    new_pass = data.get('new_password', '')
    if not new_pass or len(new_pass) < 4:
        return jsonify({'error': 'Password must be at least 4 characters'}), 400

    users = load_users()
    uname = session['user']
    if uname not in users:
        return jsonify({'error': 'User not found'}), 404
    users[uname]['password'] = new_pass
    save_users(users)
    return jsonify({'success': True, 'msg': 'Password updated'})

# ═══════════════════════════════════════════════════════════════
# FILES API (with path traversal protection)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/files/<server_id>')
@require_login
def api_files(server_id):
    folder = request.args.get('folder', '').strip('/')
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'files': [], 'error': 'Forbidden'}), 403

    base = get_server_dir(server_id)
    target = safe_path(server_id, folder) if folder else base
    if not target or not os.path.isdir(target):
        return jsonify({'files': []})

    files = []
    try:
        for item in sorted(os.listdir(target)):
            p = os.path.join(target, item)
            files.append({
                'name': item,
                'is_dir': os.path.isdir(p),
                'size': os.path.getsize(p) if os.path.isfile(p) else 0,
                'modified': datetime.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m-%d %H:%M')
            })
    except Exception as e:
        logger.error(f"List files error: {e}")
    return jsonify({'files': files})

@app.route('/api/file/<server_id>', methods=['GET'])
@require_login
def api_get_file(server_id):
    filename = request.args.get('filename', '')
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403

    path = safe_path(server_id, filename)
    if path and os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return jsonify({'content': f.read()})
        except:
            pass
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/file/<server_id>', methods=['POST'])
@require_login
def api_save_file(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json() or {}
    path = safe_path(server_id, data.get('filename', ''))
    if not path:
        return jsonify({'error': 'Invalid path'}), 400
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(data.get('content', ''))
    return jsonify({'success': True})

@app.route('/api/file/<server_id>', methods=['DELETE'])
@require_login
def api_delete_file(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403

    path = safe_path(server_id, (request.get_json() or {}).get('filename', ''))
    if path and os.path.exists(path):
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    return jsonify({'success': True})

@app.route('/api/download/<server_id>')
@require_login
def api_download(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403

    filename = request.args.get('filename', '')
    path = safe_path(server_id, filename)
    if not path or not os.path.isfile(path):
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, as_attachment=True, download_name=os.path.basename(filename))

@app.route('/api/upload/<server_id>', methods=['POST'])
@require_login
def api_upload(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400

    folder = request.form.get('folder', '').strip('/')
    base = get_server_dir(server_id)
    target_dir = safe_path(server_id, folder) if folder else base
    if not target_dir:
        return jsonify({'error': 'Invalid path'}), 400
    os.makedirs(target_dir, exist_ok=True)

    file = request.files['file']
    dest = safe_path(server_id, os.path.join(folder, file.filename) if folder else file.filename)
    if not dest:
        return jsonify({'error': 'Invalid path'}), 400
    file.save(dest)
    return jsonify({'success': True})

@app.route('/api/create_folder/<server_id>', methods=['POST'])
@require_login
def api_create_folder(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403
    path = safe_path(server_id, (request.get_json() or {}).get('foldername', ''))
    if not path:
        return jsonify({'error': 'Invalid path'}), 400
    os.makedirs(path, exist_ok=True)
    return jsonify({'success': True})

@app.route('/api/rename/<server_id>', methods=['POST'])
@require_login
def api_rename(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403
    d = request.get_json() or {}
    old_path = safe_path(server_id, d.get('old_name', ''))
    new_path = safe_path(server_id, d.get('new_name', ''))
    if not old_path or not new_path:
        return jsonify({'error': 'Invalid path'}), 400
    if os.path.exists(old_path):
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        os.rename(old_path, new_path)
        return jsonify({'success': True})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/unzip/<server_id>', methods=['POST'])
@require_login
def api_unzip(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403
    path = safe_path(server_id, (request.get_json() or {}).get('filename', ''))
    if not path or not path.endswith('.zip') or not os.path.isfile(path):
        return jsonify({'status': 'error', 'msg': 'Invalid zip'}), 400
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            zf.extractall(os.path.dirname(path))
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)})

# ═══════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════

@app.route('/api/get_startup/<server_id>')
@require_login
def api_get_startup(server_id):
    _, server = find_server(server_id)
    if server:
        return jsonify({
            'main_file': server.get('main_file', 'main.py'),
            'requirements_file': server.get('requirements_file', 'requirements.txt')
        })
    return jsonify({'main_file': 'main.py', 'requirements_file': 'requirements.txt'})

@app.route('/api/set_startup/<server_id>', methods=['POST'])
@require_login
def api_set_startup(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403
    d = request.get_json() or {}
    ok = update_server(server_id, lambda s: s.update(
        main_file=d.get('main_file', 'main.py'),
        requirements_file=d.get('requirements_file')
    ))
    return jsonify({'success': ok})

# ═══════════════════════════════════════════════════════════════
# BACKUP API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/backup/list/<server_id>')
@require_login
def api_backup_list(server_id):
    owner, server = find_server(server_id)
    if not server:
        return jsonify({'backups': []})
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403
    return jsonify({'backups': server.get('backups', [])})

@app.route('/api/backup/create/<server_id>', methods=['POST'])
@require_login
def api_backup_create(server_id):
    owner, server = find_server(server_id)
    if not server:
        return jsonify({'error': 'Not found'}), 404
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403

    name = (request.get_json() or {}).get('name', '').strip()
    if not name:
        name = 'backup-' + datetime.now().strftime('%Y%m%d-%H%M%S')

    server_dir = get_server_dir(server_id)
    backup_dir = os.path.join(server_dir, Config.BACKUP_DIR)
    os.makedirs(backup_dir, exist_ok=True)
    zip_path = os.path.join(backup_dir, name + '.zip')

    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(server_dir):
                if Config.BACKUP_DIR in root.split(os.sep):
                    continue
                for file in files:
                    fp = os.path.join(root, file)
                    arc = os.path.relpath(fp, server_dir)
                    zf.write(fp, arc)
        size_mb = round(os.path.getsize(zip_path) / (1024 * 1024), 2)
        meta = {'name': name, 'size': size_mb, 'date': datetime.now().strftime('%Y-%m-%d %H:%M')}

        def _add(s):
            s.setdefault('backups', []).insert(0, meta)
        update_server(server_id, _add)
        logger.info(f"Backup {name} created for {server_id}")
        return jsonify({'status': 'success', 'backup': meta})
    except Exception as e:
        logger.error(f"Backup error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/backup/restore/<server_id>', methods=['POST'])
@require_login
def api_backup_restore(server_id):
    owner, server = find_server(server_id)
    if not server:
        return jsonify({'error': 'Not found'}), 404
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403

    name = (request.get_json() or {}).get('name', '').strip()
    server_dir = get_server_dir(server_id)
    zip_path = os.path.join(server_dir, Config.BACKUP_DIR, name + '.zip')
    if not os.path.isfile(zip_path):
        return jsonify({'error': 'Backup not found'}), 404

    try:
        # Stop server if running
        if server.get('pid'):
            stop_bot_process(server['pid'])
            update_server(server_id, lambda s: s.update(status='stopped', pid=None))

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(server_dir)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/backup/delete/<server_id>', methods=['POST'])
@require_login
def api_backup_delete(server_id):
    owner, server = find_server(server_id)
    if not server:
        return jsonify({'error': 'Not found'}), 404
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403

    name = (request.get_json() or {}).get('name', '').strip()
    zip_path = os.path.join(get_server_dir(server_id), Config.BACKUP_DIR, name + '.zip')
    try:
        if os.path.isfile(zip_path):
            os.remove(zip_path)
        def _rm(s):
            s['backups'] = [b for b in s.get('backups', []) if b.get('name') != name]
        update_server(server_id, _rm)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ═══════════════════════════════════════════════════════════════
# SCHEDULER API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/scheduler/list/<server_id>')
@require_login
def api_scheduler_list(server_id):
    owner, server = find_server(server_id)
    if not server:
        return jsonify({'tasks': []})
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403
    return jsonify({'tasks': server.get('scheduled_tasks', [])})

@app.route('/api/scheduler/add/<server_id>', methods=['POST'])
@require_login
def api_scheduler_add(server_id):
    owner, server = find_server(server_id)
    if not server:
        return jsonify({'error': 'Not found'}), 404
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403

    d = request.get_json() or {}
    task = {
        'id': str(uuid.uuid4().hex[:8]),
        'name': d.get('name', 'Task'),
        'cron': d.get('cron', '0 * * * *'),
        'command': d.get('command', ''),
        'last_run': 0,
        'created': str(datetime.now())
    }
    def _add(s):
        s.setdefault('scheduled_tasks', []).append(task)
    update_server(server_id, _add)
    return jsonify({'status': 'success', 'task': task})

@app.route('/api/scheduler/delete/<server_id>', methods=['POST'])
@require_login
def api_scheduler_delete(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403
    tid = (request.get_json() or {}).get('id', '')
    def _rm(s):
        s['scheduled_tasks'] = [t for t in s.get('scheduled_tasks', []) if t.get('id') != tid]
    update_server(server_id, _rm)
    return jsonify({'status': 'success'})

def cron_matches(cron, now):
    """Simple cron matcher — supports *, */N, N, N-M, N,M"""
    try:
        parts = cron.split()
        if len(parts) != 5: return False
        minute, hour, dom, month, dow = parts

        def match(field, value):
            if field == '*': return True
            if field.startswith('*/'):
                return value % int(field[2:]) == 0
            if ',' in field:
                return value in [int(x) for x in field.split(',')]
            if '-' in field:
                a, b = map(int, field.split('-'))
                return a <= value <= b
            return int(field) == value

        return (match(minute, now.minute) and match(hour, now.hour) and
                match(dom, now.day) and match(month, now.month) and
                match(dow, now.isoweekday() % 7))
    except:
        return False

def scheduler_thread():
    """Runs every 60s, checks all tasks"""
    while True:
        time.sleep(60)
        try:
            now = datetime.now()
            for uname, data in load_users().items():
                if data.get('role') == 'admin':
                    continue
                for server in data.get('servers', []):
                    for task in server.get('scheduled_tasks', []):
                        if now.timestamp() - task.get('last_run', 0) < 60:
                            continue
                        if cron_matches(task.get('cron', ''), now):
                            server_id = server.get('server_id')
                            log_file = os.path.join(get_server_dir(server_id), 'output.log')
                            _log_line(log_file, f"\n[{now.strftime('%I:%M:%S %p')}] [SCHEDULER] {task.get('name')} → {task.get('command')}")
                            try:
                                subprocess.run(
                                    task.get('command', 'echo'), shell=True, cwd=get_server_dir(server_id),
                                    timeout=30, capture_output=True, text=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                                )
                            except: pass
                            # Update last_run
                            def _mark(s, tid=task.get('id')):
                                for t in s.get('scheduled_tasks', []):
                                    if t.get('id') == tid:
                                        t['last_run'] = now.timestamp()
                            update_server(server_id, _mark)
        except Exception as e:
            logger.error(f"Scheduler error: {e}")

# ═══════════════════════════════════════════════════════════════
# GITHUB DEPLOY (no git required)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/github/deploy/<server_id>', methods=['POST'])
@require_login
def api_github_deploy(server_id):
    owner, _ = find_server(server_id)
    if session.get('role') != 'admin' and session.get('user') != owner:
        return jsonify({'error': 'Forbidden'}), 403
    if not requests:
        return jsonify({'error': 'requests library not installed'}), 500

    data = request.get_json() or {}
    repo_url     = data.get('repo_url', '').strip()
    access_token = data.get('access_token', '').strip()
    is_private   = bool(data.get('is_private', False))
    branch       = data.get('branch', 'main').strip() or 'main'

    if not repo_url:
        return jsonify({'error': 'Repository URL required'}), 400

    server_dir = get_server_dir(server_id)
    log_file = os.path.join(server_dir, 'github_deploy.log')

    try:
        open(log_file, 'w').close()
    except:
        pass

    def _glog(msg):
        _log_line(log_file, f"[{datetime.now().strftime('%I:%M:%S %p')}] {msg}")

    def deploy():
        try:
            _glog("Starting GitHub deployment...")
            _glog(f"Repository: {repo_url}")

            clean = repo_url.replace('.git', '').rstrip('/')
            if 'github.com' not in clean:
                _glog("ERROR: Only GitHub URLs supported")
                return

            parts = clean.split('github.com/')[-1].split('/')
            if len(parts) < 2:
                _glog("ERROR: Invalid URL")
                return
            owner_r, repo_r = parts[0], parts[1]
            br = branch
            if len(parts) > 3 and parts[2] == 'tree':
                br = parts[3]

            _glog(f"Owner: {owner_r} | Repo: {repo_r} | Branch: {br}")

            api_url = f"https://api.github.com/repos/{owner_r}/{repo_r}/zipball/{br}"
            headers = {'Accept': 'application/vnd.github.v3+json'}
            if is_private and access_token:
                headers['Authorization'] = f'token {access_token}'
                _glog("Using access token")

            r = requests.get(api_url, headers=headers, stream=True, timeout=60)
            if r.status_code == 200:
                _glog("Download successful")
                tmp_zip = os.path.join(server_dir, '_gh_tmp.zip')
                with open(tmp_zip, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)

                with zipfile.ZipFile(tmp_zip, 'r') as zf:
                    for member in zf.namelist():
                        rel = '/'.join(member.split('/')[1:])
                        if not rel:
                            continue
                        target = os.path.join(server_dir, rel)
                        if member.endswith('/'):
                            os.makedirs(target, exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with zf.open(member) as src, open(target, 'wb') as dst:
                                shutil.copyfileobj(src, dst)
                            _glog(f"  ✓ {rel}")

                os.remove(tmp_zip)
                _glog("Deployment completed")
            elif r.status_code == 404:
                _glog("ERROR: Repo not found")
            elif r.status_code == 401:
                _glog("ERROR: Auth failed")
            elif r.status_code == 403:
                _glog("ERROR: Rate limit / access denied")
            else:
                _glog(f"ERROR: HTTP {r.status_code}")
        except requests.exceptions.Timeout:
            _glog("ERROR: Timeout")
        except Exception as e:
            _glog(f"ERROR: {e}")

    threading.Thread(target=deploy, daemon=True).start()
    return jsonify({'status': 'success', 'msg': 'Deployment started'})

@app.route('/api/github/logs/<server_id>')
@require_login
def api_github_logs(server_id):
    log_file = os.path.join(get_server_dir(server_id), 'github_deploy.log')
    logs = "> Ready for deployment..."
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                logs = f.read()
        except:
            pass
    return jsonify({'logs': logs})

@app.route('/api/github/clear_logs/<server_id>', methods=['POST'])
@require_login
def api_github_clear_logs(server_id):
    log_file = os.path.join(get_server_dir(server_id), 'github_deploy.log')
    try:
        open(log_file, 'w').close()
        return jsonify({'status': 'success'})
    except:
        return jsonify({'status': 'error'}), 500

# ═══════════════════════════════════════════════════════════════
# PUBLIC API (Telegram bot integration)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/create', methods=['GET'])
def api_create_server():
    username = request.args.get('username', '').strip()
    password = request.args.get('password', '').strip()
    stype    = request.args.get('type', 'python').strip()
    ram      = request.args.get('ram', '1GB').strip()
    disk     = request.args.get('disk', '1GB').strip()
    cpu      = int(request.args.get('cpu', '30'))
    days     = int(request.args.get('days', '3'))

    if not password: password = generate_password(10)
    if not username: username = f"MAXX_CODEX{random.randint(10000, 99999)}"
    if len(username) < 3: return jsonify({'status': 'error', 'message': 'Username too short'}), 400
    if len(password) < 4: return jsonify({'status': 'error', 'message': 'Password too short'}), 400
    if cpu < 10 or cpu > 100: return jsonify({'status': 'error', 'message': 'CPU 10-100'}), 400
    if days < 1 or days > 365: return jsonify({'status': 'error', 'message': 'Days 1-365'}), 400

    users = load_users()
    if username in users:
        return jsonify({'status': 'error', 'message': f"Username '{username}' exists"}), 400

    server_id = uuid.uuid4().hex[:8]
    expiry_date = datetime.now() + timedelta(days=days)
    create_default_files(get_server_dir(server_id))

    host = request.host
    is_local = host.startswith('localhost') or host.startswith('127.0.0.1') or host.startswith('192.168')
    scheme = 'http' if is_local else 'https'
    full_url = f"{scheme}://{host}/{server_id}/login"

    new_server = {
        'server_id': server_id,
        'login_url': f"/{server_id}/login",
        'dashboard_url': f"/{server_id}/home",
        'full_link': full_url,
        'type': stype, 'ram': ram, 'disk': disk,
        'status': 'stopped', 'pid': None,
        'created': str(datetime.now()),
        'expiry': str(expiry_date),
        'main_file': 'main.py',
        'requirements_file': 'requirements.txt',
        'cpu_limit': cpu,
        'rate_limit_exceeded': False,
        'stopped_by_user': False,
        'backups': [],
        'scheduled_tasks': []
    }

    users[username] = {
        'username': username, 'password': password,
        'role': 'user', 'email': '',
        'servers': [new_server],
        'referral_code': generate_referral_code(),
        'referrals': 0,
        'created': str(datetime.now())
    }
    save_users(users)
    logger.info(f"API: Server {server_id} → {username}")

    return jsonify({
        'status': 'success',
        'message': 'Panel created!',
        'username': username,
        'password': password,
        'server_type': stype,
        'ram': ram, 'disk': disk,
        'cpu_limit': cpu,
        'validity': f'{days} days',
        'expiry_date': expiry_date.strftime('%Y-%m-%d'),
        'full_url': full_url,
        'server_id': server_id
    }), 200

# ═══════════════════════════════════════════════════════════════
# ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════

@app.errorhandler(404)
def err_404(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return render_template('error.html', error_type='not_found', current_server=None), 404

@app.errorhandler(403)
def err_403(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Forbidden'}), 403
    return render_template('error.html', error_type='unauthorized', current_server=None), 403

@app.errorhandler(500)
def err_500(e):
    logger.exception("Internal error")
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return render_template('error.html', error_type='error', current_server=None), 500

# ═══════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════

def _init_defaults():
    """Ensure all stores exist with defaults"""
    load_users()   # seeds admins
    load_plans()   # seeds default plans
    load_orders()  # seeds empty list

def _start_background_threads():
    threading.Thread(target=scheduler_thread, daemon=True, name='scheduler').start()
    logger.info("Background threads started")

if __name__ == '__main__':
    _init_defaults()
    _start_background_threads()

    print("\n" + "=" * 58)
    print("🚀 MAXX HOSTING - Professional Backend v2.0")
    print("=" * 58)
    print(f"📍 Landing   : http://localhost:{Config.PORT}")
    print(f"📍 Admin     : http://localhost:{Config.PORT}/login")
    print(f"📍 Public API: http://localhost:{Config.PORT}/api/create")
    print(f"📍 Health    : http://localhost:{Config.PORT}/health")
    print()
    print("👤 Admins:")
    print("   admin / admin123")
    print("   MAXX  / MAXX@2010")
    print("=" * 58 + "\n")

    app.run(debug=Config.DEBUG, host=Config.HOST, port=Config.PORT, threaded=True)
