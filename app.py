import os
import sqlite3
import json
import re
import hashlib
import hmac
import secrets
import requests
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from flask import Flask, g, render_template, request, redirect, url_for, session, flash, abort, jsonify

APP_NAME = "ACHULO"
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "achulo.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static/uploads")
ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB — photos & documents
MAX_VIDEO_SIZE = 60 * 1024 * 1024  # 60MB — property video tours
VIDEO_EXTENSIONS = {'mp4', 'mov', 'webm'}

# Create uploads directory
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.jinja_env.filters['fromjson'] = lambda s: json.loads(s) if s else []
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_VIDEO_SIZE

# Admin credentials
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@achulo.test")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me-admin-password")

# Escrow: percentage of each released payment ACHULO keeps as a platform fee.
ESCROW_FEE_PERCENT = 2

# Paystack — buyer payments into escrow are collected via Paystack Standard Checkout.
# Set these in your environment before going live; without them, payment is disabled
# and pay_listing() tells the buyer so instead of pretending a payment happened.
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_BASE_URL = "https://api.paystack.co"

# Prembly (Identitypass) — instant NIN/BVN identity verification.
# NOTE: endpoint paths below follow Prembly's standard REST pattern from their public
# docs, but Prembly does update their API surface — confirm the exact paths and
# response field names in your Prembly dashboard before going live. Without these
# two env vars set, verify_nin_bvn() fails gracefully rather than faking a match.
PREMBLY_API_KEY = os.environ.get("PREMBLY_API_KEY", "")
PREMBLY_APP_ID = os.environ.get("PREMBLY_APP_ID", "")
PREMBLY_BASE_URL = "https://api.prembly.com/identitypass/verification"

PRICE_PRESETS = [
    {"label": "Any", "min": None, "max": None},
    {"label": "Under ₦500k", "min": None, "max": 500_000},
    {"label": "₦500k – ₦1M", "min": 500_000, "max": 1_000_000},
    {"label": "₦1M – ₦3M", "min": 1_000_000, "max": 3_000_000},
    {"label": "₦3M – ₦10M", "min": 3_000_000, "max": 10_000_000},
    {"label": "Over ₦10M", "min": 10_000_000, "max": None},
]

USER_ROLES = {
    'buyer': 'Buyer',
    'seller': 'Listing Agent',
}

# ---------------------------------------------------------------- CONTEXT PROCESSOR
@app.context_processor
def inject_user():
    """Inject current user into all templates"""
    if 'user_id' in session:
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        return {'current_user': user, 'app_name': APP_NAME, 'price_presets': PRICE_PRESETS}
    return {'current_user': None, 'app_name': APP_NAME, 'price_presets': PRICE_PRESETS}

# ---------------------------------------------------------------- SECURITY UTILITIES
def hash_password(password):
    """Hash password with salt"""
    return generate_password_hash(password, method='pbkdf2:sha256')

def verify_password(password, hash_val):
    """Verify password against hash"""
    return check_password_hash(hash_val, password)

def validate_email(email):
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_password(password):
    """Validate password strength (min 8 chars, uppercase, number, special char)"""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain an uppercase letter"
    if not re.search(r'[0-9]', password):
        return False, "Password must contain a number"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Password must contain a special character"
    return True, "Password is strong"

def validate_phone(phone):
    """Validate Nigerian phone number"""
    phone = re.sub(r'\D', '', phone)
    if phone.startswith('234'):
        phone = '0' + phone[3:]
    pattern = r'^0[789]\d{9}$'
    return re.match(pattern, phone) is not None

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def sanitize_input(text):
    """Sanitize user input to prevent XSS"""
    if not isinstance(text, str):
        return text
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<iframe[^>]*>.*?</iframe>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = text.replace('<', '&lt;').replace('>', '&gt;')
    return text.strip()

# ---------------------------------------------------------------- DATABASE
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            phone TEXT,
            role TEXT NOT NULL DEFAULT 'buyer',
            kyc_status TEXT NOT NULL DEFAULT 'unverified',
            kyc_verified_at TEXT,
            kyc_document_type TEXT,
            kyc_document_url TEXT,
            kyc_reference_id TEXT,
            company_status TEXT NOT NULL DEFAULT 'unverified',
            kyc_method TEXT,
            kyc_verified_number_type TEXT,
            kyc_verified_number_masked TEXT,
            kyc_verified_name TEXT,
            nin TEXT UNIQUE,
            bvn TEXT UNIQUE,
            cac TEXT UNIQUE,
            country TEXT DEFAULT 'Nigeria',
            state TEXT,
            city TEXT,
            account_locked BOOLEAN DEFAULT 0,
            account_status TEXT NOT NULL DEFAULT 'active',
            ban_reason TEXT,
            status_updated_at TEXT,
            failed_login_attempts INTEGER DEFAULT 0,
            last_login TEXT,
            last_activity TEXT,
            two_factor_enabled BOOLEAN DEFAULT 0,
            two_factor_secret TEXT,
            accepted_terms BOOLEAN DEFAULT 0,
            accepted_privacy BOOLEAN DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL,
            description TEXT,
            state TEXT,
            lga TEXT,
            location_tag TEXT,
            address TEXT,
            price INTEGER NOT NULL,
            rental_period TEXT NOT NULL DEFAULT 'per_annum',
            bedrooms INTEGER DEFAULT 1,
            bathrooms INTEGER DEFAULT 1,
            image_url TEXT,
            document_url TEXT,
            property_deed TEXT,
            tax_clearance TEXT,
            verification_status TEXT DEFAULT 'pending_documents',
            verified_at TEXT,
            verified_by_admin_id INTEGER REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'draft',
            flagged_for_review BOOLEAN DEFAULT 0,
            fraud_score INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER NOT NULL REFERENCES users(id),
            listing_id INTEGER NOT NULL REFERENCES listings(id),
            amount INTEGER NOT NULL,
            platform_fee INTEGER NOT NULL DEFAULT 0,
            seller_payout INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'awaiting_payment',
            dispute_reason TEXT,
            transaction_hash TEXT UNIQUE,
            paystack_reference TEXT UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            paid_at TEXT,
            released_at TEXT
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL REFERENCES listings(id),
            reporter_id INTEGER REFERENCES users(id),
            reporter_name TEXT,
            report_reason TEXT,
            report_details TEXT,
            severity TEXT DEFAULT 'low',
            status TEXT DEFAULT 'open',
            admin_notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            ip_address TEXT,
            success BOOLEAN,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            action TEXT NOT NULL,
            resource_type TEXT,
            resource_id INTEGER,
            details TEXT,
            ip_address TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            listing_id INTEGER REFERENCES listings(id),
            document_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_name TEXT,
            verified BOOLEAN DEFAULT 0,
            verification_date TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS listing_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL REFERENCES listings(id),
            image_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS listing_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL REFERENCES listings(id),
            video_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        -- Archive: when a listing is deleted, a full snapshot (including its
        -- photo/video paths) is written here first instead of being lost, so
        -- there's a permanent record of what existed and why it was removed.
        CREATE TABLE IF NOT EXISTS deleted_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_listing_id INTEGER NOT NULL,
            owner_id INTEGER,
            owner_name TEXT,
            title TEXT,
            description TEXT,
            address TEXT,
            state TEXT,
            lga TEXT,
            location_tag TEXT,
            price INTEGER,
            rental_period TEXT,
            bedrooms INTEGER,
            bathrooms INTEGER,
            verification_status TEXT,
            status TEXT,
            images_json TEXT,
            videos_json TEXT,
            deleted_by INTEGER REFERENCES users(id),
            delete_reason TEXT,
            deleted_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL REFERENCES users(id),
            recipient_id INTEGER NOT NULL REFERENCES users(id),
            listing_id INTEGER REFERENCES listings(id),
            body TEXT NOT NULL,
            read_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            email TEXT,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            admin_response TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            listing_id INTEGER NOT NULL REFERENCES listings(id),
            created_at TEXT NOT NULL,
            UNIQUE(user_id, listing_id)
        );

        CREATE TABLE IF NOT EXISTS property_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL,
            location_tag TEXT,
            state TEXT,
            min_price INTEGER,
            max_price INTEGER,
            bedrooms INTEGER,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        );

        -- Read-only views, not extra copies of the data: they always reflect
        -- the live `listings` table, split by review state, so admin/home
        -- queries can pull "the approved ones" or "the pending ones" by name
        -- instead of repeating the same WHERE clause everywhere.
        CREATE VIEW IF NOT EXISTS approved_listings AS
            SELECT * FROM listings
            WHERE status = 'active' AND verification_status = 'verified';

        CREATE VIEW IF NOT EXISTS pending_listings_db AS
            SELECT * FROM listings
            WHERE verification_status IN ('pending_documents', 'pending_review');
        """
    )
    db.commit()
    db.close()

# Initialize DB on startup
if not os.path.exists(DB_PATH):
    init_db()

def seed_admin():
    """Ensure an admin-eligible account exists so ADMIN_EMAIL / ADMIN_PASSWORD actually work.
    Admin rights are granted at login when the logged-in email matches ADMIN_EMAIL —
    this just makes sure that account exists instead of leaving it to be registered manually."""
    db = get_db()
    existing = db.execute('SELECT id FROM users WHERE email = ?', (ADMIN_EMAIL,)).fetchone()
    if not existing:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO users
            (name, email, phone, password_hash, role, kyc_status, kyc_verified_at,
             account_status, country, created_at, updated_at, accepted_terms, accepted_privacy)
            VALUES (?, ?, ?, ?, 'buyer', 'verified', ?, 'active', 'Nigeria', ?, ?, 1, 1)""",
            ('Admin', ADMIN_EMAIL, '0000000000', hash_password(ADMIN_PASSWORD), now, now, now)
        )
        db.commit()
    db.close()

with app.app_context():
    seed_admin()

# ---------------------------------------------------------------- AUTHENTICATION DECORATORS
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def seller_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.', 'error')
            return redirect(url_for('login'))

        if session.get('is_admin'):
            return f(*args, **kwargs)

        db = get_db()
        user = db.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        
        if not user or user['role'] != 'seller':
            flash('You must be a listing agent to access this page.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def kyc_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.', 'error')
            return redirect(url_for('login'))
        
        db = get_db()
        user = db.execute('SELECT kyc_status FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        
        if not user or user['kyc_status'] != 'verified':
            flash('Please complete KYC verification to proceed.', 'warning')
            return redirect(url_for('dashboard_kyc'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'is_admin' not in session or not session['is_admin']:
            if 'user_id' not in session:
                flash('Please log in first.', 'error')
                return redirect(url_for('login'))
            flash('That page is for admins only.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function
# ---------------------------------------------------------------- PAYSTACK
def paystack_initialize(email, amount_naira, reference, callback_url):
    """Start a Paystack Standard Checkout session. Returns (authorization_url, error)."""
    if not PAYSTACK_SECRET_KEY:
        return None, 'Payments are not configured yet — PAYSTACK_SECRET_KEY is missing.'
    try:
        resp = requests.post(
            f'{PAYSTACK_BASE_URL}/transaction/initialize',
            headers={'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}', 'Content-Type': 'application/json'},
            json={
                'email': email,
                'amount': int(amount_naira) * 100,  # Paystack expects kobo
                'reference': reference,
                'callback_url': callback_url,
                'currency': 'NGN',
            },
            timeout=15
        )
        data = resp.json()
        if resp.status_code == 200 and data.get('status'):
            return data['data']['authorization_url'], None
        return None, data.get('message', 'Paystack could not start this payment.')
    except requests.RequestException as e:
        return None, f'Could not reach Paystack: {e}'

def paystack_verify(reference):
    """Verify a transaction by reference. Returns (verified_bool, data_dict, error)."""
    if not PAYSTACK_SECRET_KEY:
        return False, None, 'Payments are not configured.'
    try:
        resp = requests.get(
            f'{PAYSTACK_BASE_URL}/transaction/verify/{reference}',
            headers={'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}'},
            timeout=15
        )
        data = resp.json()
        if resp.status_code == 200 and data.get('status'):
            tx_data = data['data']
            return tx_data.get('status') == 'success', tx_data, None
        return False, None, data.get('message', 'Verification failed.')
    except requests.RequestException as e:
        return False, None, f'Could not reach Paystack: {e}'

# ---------------------------------------------------------------- PREMBLY (NIN/BVN)
def prembly_verify(number_type, number):
    """Look up a NIN or BVN via Prembly. Returns (verified_bool, verified_name_or_None, error_or_None).
    number_type is 'nin' or 'bvn'. The raw number is sent to Prembly for this one call
    and never persisted — callers should only store prembly_mask(number) afterward."""
    if not PREMBLY_API_KEY or not PREMBLY_APP_ID:
        return False, None, 'NIN/BVN verification is not configured yet — PREMBLY_API_KEY / PREMBLY_APP_ID missing.'

    endpoint = 'nin' if number_type == 'nin' else 'bvn'
    try:
        resp = requests.post(
            f'{PREMBLY_BASE_URL}/{endpoint}',
            headers={
                'x-api-key': PREMBLY_API_KEY,
                'app-id': PREMBLY_APP_ID,
                'Content-Type': 'application/json',
            },
            json={'number': number},
            timeout=15
        )
        data = resp.json()
        # Prembly's success shape is generally {"status": true, "detail": "...", "response_code": "00",
        # "data": {"first_name": ..., "last_name": ..., ...}} — confirm exact field names against your
        # dashboard docs, as these vary slightly by product tier.
        if resp.status_code == 200 and data.get('status'):
            person = data.get('data', {}) or {}
            first = person.get('first_name', '') or person.get('firstName', '')
            last = person.get('last_name', '') or person.get('lastName', '')
            verified_name = f'{first} {last}'.strip() or None
            return True, verified_name, None
        return False, None, data.get('detail') or data.get('message') or 'No match found for that number.'
    except requests.RequestException as e:
        return False, None, f'Could not reach Prembly: {e}'

def prembly_mask(number):
    """Never store the raw NIN/BVN — keep only a display-safe masked tail."""
    digits = re.sub(r'\D', '', number or '')
    if len(digits) < 4:
        return '****'
    return '*' * (len(digits) - 4) + digits[-4:]

# ---------------------------------------------------------------- AUDIT LOGGING
def log_action(user_id, action, resource_type=None, resource_id=None, details=None):
    """Log user actions for security audit"""
    db = get_db()
    ip_address = request.remote_addr
    created_at = datetime.now().isoformat()
    
    db.execute(
        """INSERT INTO audit_log 
        (user_id, action, resource_type, resource_id, details, ip_address, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, action, resource_type, resource_id, details, ip_address, created_at)
    )
    db.commit()

def log_login_attempt(user_id, success):
    """Log login attempts"""
    db = get_db()
    ip_address = request.remote_addr
    created_at = datetime.now().isoformat()
    
    db.execute(
        """INSERT INTO login_attempts 
        (user_id, ip_address, success, created_at)
        VALUES (?, ?, ?, ?)""",
        (user_id, ip_address, success, created_at)
    )
    db.commit()

# ---------------------------------------------------------------- ROUTES

@app.route('/')
def home():
    """Marketing landing page — no search, no listings grid, its own page."""
    return render_template('landing.html')

@app.route('/browse')
def browse():
    db = get_db()
    location = sanitize_input(request.args.get('location', '').strip())
    rental_period = request.args.get('rentalPeriod', '')
    try:
        price_index = int(request.args.get('price', ''))
    except ValueError:
        price_index = None
    price_preset = PRICE_PRESETS[price_index] if price_index is not None and 0 <= price_index < len(PRICE_PRESETS) else None

    query = """SELECT l.*, u.name AS lister_name, u.kyc_status AS lister_kyc_status,
                      u.company_status AS lister_company_status, u.id AS lister_id
               FROM approved_listings l JOIN users u ON u.id = l.owner_id
               WHERE 1=1"""
    params = []

    if location:
        query += " AND (l.location_tag LIKE ? OR l.state LIKE ? OR l.lga LIKE ? OR l.address LIKE ?)"
        like = f"%{location}%"
        params += [like, like, like, like]

    if rental_period in ('per_month', 'per_annum'):
        query += " AND l.rental_period = ?"
        params.append(rental_period)

    if price_preset:
        if price_preset['min'] is not None:
            query += " AND l.price >= ?"
            params.append(price_preset['min'])
        if price_preset['max'] is not None:
            query += " AND l.price <= ?"
            params.append(price_preset['max'])

    query += " ORDER BY l.created_at DESC LIMIT 60"
    listings = db.execute(query, params).fetchall()

    favorited_ids = set()
    if 'user_id' in session and listings:
        rows = db.execute(
            f"SELECT listing_id FROM favorites WHERE user_id = ? AND listing_id IN ({','.join('?' * len(listings))})",
            [session['user_id']] + [l['id'] for l in listings]
        ).fetchall()
        favorited_ids = {r['listing_id'] for r in rows}

    return render_template(
        'browse.html', listings=listings, location=location, rental_period=rental_period,
        price_index=price_index, favorited_ids=favorited_ids
    )

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/security')
def security_info():
    return render_template('security.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = sanitize_input(request.form.get('name', ''))
        email = sanitize_input(request.form.get('email', ''))
        phone = request.form.get('phone', '')
        role = request.form.get('role', 'buyer')
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        accept_terms = request.form.get('accept_terms')
        accept_privacy = request.form.get('accept_privacy')

        # Validation
        if not all([name, email, phone, password, confirm_password]):
            flash('All fields are required.', 'error')
            return render_template('register.html')

        if not validate_email(email):
            flash('Invalid email format.', 'error')
            return render_template('register.html')

        if not validate_phone(phone):
            flash('Invalid Nigerian phone number.', 'error')
            return render_template('register.html')

        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('register.html')

        valid_password, message = validate_password(password)
        if not valid_password:
            flash(message, 'error')
            return render_template('register.html')

        if not accept_terms or not accept_privacy:
            flash('You must accept Terms of Service and Privacy Policy.', 'error')
            return render_template('register.html')

        if role not in USER_ROLES:
            flash('Invalid user role.', 'error')
            return render_template('register.html')

        db = get_db()
        
        # Check if email exists
        if db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone():
            flash('Email already registered.', 'error')
            return render_template('register.html')

        # Create user. Listing agents can start listing right away — identity and
        # company verification happen afterward from their dashboard, not at signup.
        password_hash = hash_password(password)
        now = datetime.now().isoformat()

        cur = db.execute(
            """INSERT INTO users 
            (name, email, phone, password_hash, role, kyc_status, company_status, country, created_at, updated_at, accepted_terms, accepted_privacy)
            VALUES (?, ?, ?, ?, ?, 'unverified', 'unverified', 'Nigeria', ?, ?, 1, 1)""",
            (name, email, phone, password_hash, role, now, now)
        )
        db.commit()
        
        user_id = cur.lastrowid

        log_action(user_id, 'user_registration', 'user', user_id, f'New {role} account created')

        if role == 'seller':
            flash('Account created! Head to Verification in your dashboard to get your identity — and optionally your company — verified.', 'success')
        else:
            flash('Account created successfully! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html', user_roles=USER_ROLES)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = sanitize_input(request.form.get('email', ''))
        password = request.form.get('password', '')

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        if user and user['account_locked']:
            flash('Account is locked. Contact support.', 'error')
            return render_template('login.html')

        if user and verify_password(password, user['password_hash']):
            if user['account_status'] == 'banned':
                log_login_attempt(user['id'], False)
                return render_template('banned.html', reason=user['ban_reason'], user_email=user['email'])

            # Reset failed attempts
            db.execute('UPDATE users SET failed_login_attempts = 0 WHERE id = ?', (user['id'],))
            db.execute('UPDATE users SET last_login = ? WHERE id = ?', (datetime.now().isoformat(), user['id'],))
            db.commit()
            
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_role'] = user['role']
            session['kyc_status'] = user['kyc_status']
            
            log_login_attempt(user['id'], True)
            log_action(user['id'], 'login', 'user', user['id'])
            
            if email == ADMIN_EMAIL:
                session['is_admin'] = True

            if user['account_status'] == 'suspended':
                flash('Your account is currently suspended by an administrator. Some actions are restricted. You can request a review below.', 'error')
                return redirect(url_for('dashboard'))

            flash(f'Welcome back, {user["name"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            if user:
                attempts = user['failed_login_attempts'] + 1
                if attempts >= 5:
                    db.execute('UPDATE users SET account_locked = 1 WHERE id = ?', (user['id'],))
                    flash('Too many failed attempts. Account locked.', 'error')
                    log_action(user['id'], 'account_locked_failed_login')
                else:
                    db.execute('UPDATE users SET failed_login_attempts = ? WHERE id = ?', (attempts, user['id']))
                    flash(f'Invalid credentials. {5 - attempts} attempts remaining.', 'error')
                db.commit()
                log_login_attempt(user['id'], False)
            else:
                flash('Invalid email or password.', 'error')

        return render_template('login.html')

    return render_template('login.html')

@app.route('/logout')
def logout():
    user_id = session.get('user_id')
    log_action(user_id, 'logout', 'user', user_id)
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    if session.get('user_role') == 'seller' or session.get('is_admin'):
        listings = db.execute(
            'SELECT * FROM listings WHERE owner_id = ? ORDER BY created_at DESC LIMIT 5',
            (session['user_id'],)
        ).fetchall()
        return render_template('dashboard_seller.html', user=user, listings=listings)
    else:
        listings = db.execute(
            """SELECT l.*, u.name AS lister_name, u.kyc_status AS lister_kyc_status, u.company_status AS lister_company_status, u.id AS lister_id
               FROM listings l JOIN users u ON u.id = l.owner_id
               WHERE l.status = 'active' ORDER BY l.created_at DESC LIMIT 9"""
        ).fetchall()
        conversations = db.execute(
            """SELECT DISTINCT CASE WHEN sender_id = ? THEN recipient_id ELSE sender_id END AS other_id
               FROM messages WHERE sender_id = ? OR recipient_id = ? LIMIT 5""",
            (session['user_id'], session['user_id'], session['user_id'])
        ).fetchall()
        return render_template('dashboard_buyer.html', user=user, listings=listings, conversations=conversations)

@app.route('/dashboard/verification', methods=['GET'])
@login_required
def dashboard_kyc():
    """Two-step verification: identity (required) and company (optional, earns the Verified Agent badge)."""
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    identity_doc = db.execute(
        "SELECT * FROM documents WHERE user_id = ? AND document_type = 'identity_document' ORDER BY id DESC LIMIT 1",
        (session['user_id'],)
    ).fetchone()
    company_docs = db.execute(
        "SELECT * FROM documents WHERE user_id = ? AND document_type IN ('company_incorporation','company_proof') ORDER BY id DESC",
        (session['user_id'],)
    ).fetchall()
    return render_template('dashboard_kyc.html', user=user, identity_doc=identity_doc, company_docs=company_docs)

@app.route('/dashboard/verification/identity', methods=['POST'])
@login_required
def verify_identity_submit():
    db = get_db()
    file = request.files.get('document')

    if not file or file.filename == '':
        flash('Please choose a file to upload.', 'error')
        return redirect(url_for('dashboard_kyc'))

    if not allowed_file(file.filename):
        flash('File type not allowed. Use PDF, JPG, PNG, DOC, or DOCX.', 'error')
        return redirect(url_for('dashboard_kyc'))

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_FILE_SIZE:
        flash('File too large. Maximum 10MB.', 'error')
        return redirect(url_for('dashboard_kyc'))

    filename = secure_filename(f"identity_{session['user_id']}_{int(datetime.now().timestamp())}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    db.execute(
        """INSERT INTO documents (user_id, document_type, file_path, file_name, created_at)
        VALUES (?, 'identity_document', ?, ?, ?)""",
        (session['user_id'], filepath, filename, datetime.now().isoformat())
    )
    db.execute("UPDATE users SET kyc_status = 'pending', kyc_method = 'document' WHERE id = ?", (session['user_id'],))
    db.commit()

    log_action(session['user_id'], 'identity_document_uploaded', 'document', None)
    flash('Identity document submitted. An admin will review it shortly.', 'success')
    return redirect(url_for('dashboard_kyc'))

@app.route('/dashboard/verification/identity/instant', methods=['POST'])
@login_required
def verify_identity_instant():
    """Instant identity verification via Prembly NIN/BVN lookup — no admin review needed
    if the number matches. Falls back with a clear error if Prembly isn't configured."""
    number_type = request.form.get('number_type')
    number = re.sub(r'\D', '', request.form.get('number', ''))

    if number_type not in ('nin', 'bvn'):
        flash('Please choose NIN or BVN.', 'error')
        return redirect(url_for('dashboard_kyc'))

    expected_len = 11
    if len(number) != expected_len:
        flash(f'{number_type.upper()} must be {expected_len} digits.', 'error')
        return redirect(url_for('dashboard_kyc'))

    verified, verified_name, error = prembly_verify(number_type, number)
    db = get_db()

    if not verified:
        flash(f'Verification failed: {error}', 'error')
        return redirect(url_for('dashboard_kyc'))

    db.execute(
        """UPDATE users SET kyc_status = 'verified', kyc_method = 'nin_bvn',
           kyc_verified_number_type = ?, kyc_verified_number_masked = ?, kyc_verified_name = ?
           WHERE id = ?""",
        (number_type, prembly_mask(number), verified_name, session['user_id'])
    )
    db.commit()
    log_action(session['user_id'], 'identity_verified_instant', 'user', session['user_id'], f'{number_type.upper()} verified via Prembly')
    flash(f"Identity verified instantly via your {number_type.upper()}{' (' + verified_name + ')' if verified_name else ''}.", 'success')
    return redirect(url_for('dashboard_kyc'))

@app.route('/dashboard/verification/company', methods=['POST'])
@login_required
def verify_company_submit():
    db = get_db()
    incorp = request.files.get('incorporation')
    proof = request.files.get('proof')

    if not incorp or incorp.filename == '' or not proof or proof.filename == '':
        flash('Please attach both your incorporation certificate and one proof-of-address document.', 'error')
        return redirect(url_for('dashboard_kyc'))

    for f in (incorp, proof):
        if not allowed_file(f.filename):
            flash('File type not allowed. Use PDF, JPG, PNG, DOC, or DOCX.', 'error')
            return redirect(url_for('dashboard_kyc'))
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(0)
        if size > MAX_FILE_SIZE:
            flash('One of your files is too large. Maximum 10MB each.', 'error')
            return redirect(url_for('dashboard_kyc'))

    now_ts = int(datetime.now().timestamp())
    incorp_filename = secure_filename(f"company_incorp_{session['user_id']}_{now_ts}_{incorp.filename}")
    incorp_path = os.path.join(app.config['UPLOAD_FOLDER'], incorp_filename)
    incorp.save(incorp_path)

    proof_filename = secure_filename(f"company_proof_{session['user_id']}_{now_ts}_{proof.filename}")
    proof_path = os.path.join(app.config['UPLOAD_FOLDER'], proof_filename)
    proof.save(proof_path)

    now = datetime.now().isoformat()
    db.execute(
        """INSERT INTO documents (user_id, document_type, file_path, file_name, created_at)
        VALUES (?, 'company_incorporation', ?, ?, ?)""",
        (session['user_id'], incorp_path, incorp_filename, now)
    )
    db.execute(
        """INSERT INTO documents (user_id, document_type, file_path, file_name, created_at)
        VALUES (?, 'company_proof', ?, ?, ?)""",
        (session['user_id'], proof_path, proof_filename, now)
    )
    db.execute("UPDATE users SET company_status = 'pending' WHERE id = ?", (session['user_id'],))
    db.commit()

    log_action(session['user_id'], 'company_documents_uploaded', 'document', None)
    flash('Company documents submitted for review.', 'success')
    return redirect(url_for('dashboard_kyc'))

@app.route('/dashboard/listings', methods=['GET', 'POST'])
@login_required
@seller_required
def dashboard_listings():
    db = get_db()
    
    if request.method == 'POST':
        title = sanitize_input(request.form.get('title', ''))
        description = sanitize_input(request.form.get('description', ''))
        address = sanitize_input(request.form.get('address', ''))
        state = request.form.get('state', '')
        lga = request.form.get('lga', '')
        location_tag = request.form.get('location_tag', '')
        price = request.form.get('price', '')
        bedrooms = request.form.get('bedrooms', '1')
        bathrooms = request.form.get('bathrooms', '1')
        rental_period = request.form.get('rental_period', 'per_annum')
        
        # Validate input
        if not all([title, description, address, state, price]):
            flash('All fields are required.', 'error')
            return redirect(url_for('dashboard_listings'))
        
        try:
            price = int(price)
            bedrooms = int(bedrooms)
            bathrooms = int(bathrooms)
        except ValueError:
            flash('Invalid price or room numbers.', 'error')
            return redirect(url_for('dashboard_listings'))
        
        # Create listing
        now = datetime.now().isoformat()
        cur = db.execute(
            """INSERT INTO listings 
            (owner_id, title, description, address, state, lga, location_tag, price, bedrooms, bathrooms, 
             rental_period, verification_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_documents', ?, ?)""",
            (session['user_id'], title, description, address, state, lga, location_tag, price, bedrooms, 
             bathrooms, rental_period, now, now)
        )
        db.commit()
        listing_id = cur.lastrowid
        
        log_action(session['user_id'], 'listing_created', 'listing', listing_id)
        flash('Listing created! Now upload property documents for verification.', 'success')
        return redirect(url_for('dashboard_listings'))
    
    listings = db.execute(
        'SELECT * FROM listings WHERE owner_id = ? ORDER BY created_at DESC',
        (session['user_id'],)
    ).fetchall()
    
    return render_template('dashboard_listings.html', listings=listings)

@app.route('/dashboard/listings/new', methods=['GET', 'POST'])
@login_required
@seller_required
def dashboard_listing_new():
    """Create a new listing"""
    db = get_db()
    
    if request.method == 'POST':
        title = sanitize_input(request.form.get('title', ''))
        description = sanitize_input(request.form.get('description', ''))
        address = sanitize_input(request.form.get('address', ''))
        state = request.form.get('state', '')
        lga = request.form.get('lga', '')
        location_tag = request.form.get('location_tag', '')
        price = request.form.get('price', '')
        bedrooms = request.form.get('bedrooms', '1')
        bathrooms = request.form.get('bathrooms', '1')
        rental_period = request.form.get('rental_period', 'per_annum')
        
        # Validate input
        if not all([title, description, address, state, price]):
            flash('All fields are required.', 'error')
            return render_template('dashboard_listing_new.html')
        
        try:
            price = int(price)
            bedrooms = int(bedrooms)
            bathrooms = int(bathrooms)
        except ValueError:
            flash('Invalid price or room numbers.', 'error')
            return render_template('dashboard_listing_new.html')
        
        # Create listing
        now = datetime.now().isoformat()
        cur = db.execute(
            """INSERT INTO listings 
            (owner_id, title, description, address, state, lga, location_tag, price, bedrooms, bathrooms, 
             rental_period, verification_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_documents', ?, ?)""",
            (session['user_id'], title, description, address, state, lga, location_tag, price, bedrooms, 
             bathrooms, rental_period, now, now)
        )
        db.commit()
        listing_id = cur.lastrowid
        
        log_action(session['user_id'], 'listing_created', 'listing', listing_id)
        flash('Listing created successfully!', 'success')
        return redirect(url_for('dashboard_listings'))
    
    return render_template('dashboard_listing_new.html')

@app.route('/dashboard/listings/<int:listing_id>/upload', methods=['POST'])
@login_required
@seller_required
def upload_listing_documents(listing_id):
    db = get_db()
    listing = db.execute(
        'SELECT * FROM listings WHERE id = ? AND owner_id = ?',
        (listing_id, session['user_id'])
    ).fetchone()
    
    if not listing:
        flash('That listing could not be found — it may have already been removed.', 'error')
        return redirect(url_for('dashboard_listings'))
    
    if 'documents' not in request.files:
        flash('No files selected.', 'error')
        return redirect(url_for('dashboard_listings'))
    
    files = request.files.getlist('documents')
    uploaded_count = 0
    
    for file in files:
        if file and file.filename and allowed_file(file.filename):
            if len(file.read()) <= MAX_FILE_SIZE:
                file.seek(0)
                filename = secure_filename(f"{listing_id}_{int(datetime.now().timestamp())}_{file.filename}")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                
                db.execute(
                    """INSERT INTO documents 
                    (user_id, listing_id, document_type, file_path, file_name, created_at)
                    VALUES (?, ?, 'property_document', ?, ?, ?)""",
                    (session['user_id'], listing_id, filepath, filename, datetime.now().isoformat())
                )
                uploaded_count += 1
    
    if uploaded_count > 0:
        db.execute('UPDATE listings SET document_url = ?, verification_status = ? WHERE id = ?',
                  (uploaded_count, 'pending_review', listing_id))
        db.commit()
        log_action(session['user_id'], 'property_documents_uploaded', 'listing', listing_id)
        flash(f'{uploaded_count} document(s) uploaded successfully.', 'success')
    else:
        flash('No valid files uploaded.', 'error')
    
    return redirect(url_for('dashboard_listings'))

@app.route('/dashboard/listings/<int:listing_id>/images', methods=['POST'])
@login_required
@seller_required
def upload_listing_images(listing_id):
    """Upload one or more photos for a listing"""
    db = get_db()
    listing = db.execute(
        'SELECT * FROM listings WHERE id = ? AND owner_id = ?',
        (listing_id, session['user_id'])
    ).fetchone()

    if not listing:
        flash('That listing could not be found — it may have already been removed.', 'error')
        return redirect(url_for('dashboard_listings'))

    files = request.files.getlist('images')
    image_exts = {'jpg', 'jpeg', 'png'}
    uploaded = []

    for file in files:
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext not in image_exts:
                continue
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(0)
            if size > MAX_FILE_SIZE:
                continue
            filename = secure_filename(f"img_{listing_id}_{int(datetime.now().timestamp())}_{file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            rel_path = url_for('static', filename=f'uploads/{filename}')
            db.execute(
                'INSERT INTO listing_images (listing_id, image_path, created_at) VALUES (?, ?, ?)',
                (listing_id, rel_path, datetime.now().isoformat())
            )
            uploaded.append(rel_path)

    if uploaded:
        if not listing['image_url']:
            db.execute('UPDATE listings SET image_url = ? WHERE id = ?', (uploaded[0], listing_id))
        db.commit()
        log_action(session['user_id'], 'listing_images_uploaded', 'listing', listing_id)
        flash(f'{len(uploaded)} photo(s) uploaded.', 'success')
    else:
        flash('No valid images uploaded (JPG/PNG only, max 10MB each).', 'error')

    if request.form.get('return_to') == 'edit':
        return redirect(url_for('dashboard_listing_edit', listing_id=listing_id))
    return redirect(url_for('dashboard_listings'))

@app.route('/dashboard/listings/<int:listing_id>/images/<int:image_id>/delete', methods=['POST'])
@login_required
@seller_required
def delete_listing_image(listing_id, image_id):
    """Delete a single photo from a listing"""
    db = get_db()
    listing = db.execute(
        'SELECT * FROM listings WHERE id = ? AND owner_id = ?',
        (listing_id, session['user_id'])
    ).fetchone()

    if not listing:
        flash('That listing could not be found — it may have already been removed.', 'error')
        return redirect(url_for('dashboard_listings'))

    image = db.execute(
        'SELECT * FROM listing_images WHERE id = ? AND listing_id = ?', (image_id, listing_id)
    ).fetchone()

    if image:
        db.execute('DELETE FROM listing_images WHERE id = ?', (image_id,))
        if listing['image_url'] == image['image_path']:
            next_image = db.execute(
                'SELECT * FROM listing_images WHERE listing_id = ? AND id != ? ORDER BY id LIMIT 1',
                (listing_id, image_id)
            ).fetchone()
            db.execute(
                'UPDATE listings SET image_url = ? WHERE id = ?',
                (next_image['image_path'] if next_image else None, listing_id)
            )
        db.commit()
        log_action(session['user_id'], 'listing_image_deleted', 'listing', listing_id)
        flash('Photo removed.', 'success')

    return redirect(url_for('dashboard_listing_edit', listing_id=listing_id))

@app.route('/dashboard/listings/<int:listing_id>/videos', methods=['POST'])
@login_required
@seller_required
def upload_listing_videos(listing_id):
    """Upload one or more video tours for a listing"""
    db = get_db()
    listing = db.execute(
        'SELECT * FROM listings WHERE id = ? AND owner_id = ?',
        (listing_id, session['user_id'])
    ).fetchone()

    if not listing:
        flash('That listing could not be found — it may have already been removed.', 'error')
        return redirect(url_for('dashboard_listings'))

    files = request.files.getlist('videos')
    uploaded = []

    for file in files:
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext not in VIDEO_EXTENSIONS:
                continue
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(0)
            if size > MAX_VIDEO_SIZE:
                continue
            filename = secure_filename(f"vid_{listing_id}_{int(datetime.now().timestamp())}_{file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            rel_path = url_for('static', filename=f'uploads/{filename}')
            db.execute(
                'INSERT INTO listing_videos (listing_id, video_path, created_at) VALUES (?, ?, ?)',
                (listing_id, rel_path, datetime.now().isoformat())
            )
            uploaded.append(rel_path)

    if uploaded:
        db.commit()
        log_action(session['user_id'], 'listing_videos_uploaded', 'listing', listing_id)
        flash(f'{len(uploaded)} video(s) uploaded.', 'success')
    else:
        flash('No valid videos uploaded (MP4/MOV/WEBM only, max 60MB each).', 'error')

    return redirect(url_for('dashboard_listing_edit', listing_id=listing_id))

@app.route('/dashboard/listings/<int:listing_id>/videos/<int:video_id>/delete', methods=['POST'])
@login_required
@seller_required
def delete_listing_video(listing_id, video_id):
    """Delete a single video from a listing"""
    db = get_db()
    listing = db.execute(
        'SELECT * FROM listings WHERE id = ? AND owner_id = ?',
        (listing_id, session['user_id'])
    ).fetchone()

    if not listing:
        flash('That listing could not be found — it may have already been removed.', 'error')
        return redirect(url_for('dashboard_listings'))

    video = db.execute(
        'SELECT * FROM listing_videos WHERE id = ? AND listing_id = ?', (video_id, listing_id)
    ).fetchone()

    if video:
        db.execute('DELETE FROM listing_videos WHERE id = ?', (video_id,))
        db.commit()
        log_action(session['user_id'], 'listing_video_deleted', 'listing', listing_id)
        flash('Video removed.', 'success')

    return redirect(url_for('dashboard_listing_edit', listing_id=listing_id))

@app.route('/dashboard/listings/<int:listing_id>/edit', methods=['GET', 'POST'])
@login_required
@seller_required
def dashboard_listing_edit(listing_id):
    """Edit an existing listing. Owners can edit their own; admin can edit any."""
    db = get_db()
    if session.get('is_admin'):
        listing = db.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    else:
        listing = db.execute(
            'SELECT * FROM listings WHERE id = ? AND owner_id = ?',
            (listing_id, session['user_id'])
        ).fetchone()

    if not listing:
        flash('That listing could not be found — it may have already been removed.', 'error')
        return redirect(url_for('admin_dashboard') if session.get('is_admin') else url_for('dashboard_listings'))

    listing_images = db.execute(
        'SELECT * FROM listing_images WHERE listing_id = ? ORDER BY id', (listing_id,)
    ).fetchall()
    listing_videos = db.execute(
        'SELECT * FROM listing_videos WHERE listing_id = ? ORDER BY id', (listing_id,)
    ).fetchall()

    if request.method == 'POST':
        title = sanitize_input(request.form.get('title', ''))
        description = sanitize_input(request.form.get('description', ''))
        address = sanitize_input(request.form.get('address', ''))
        state = request.form.get('state', '')
        lga = request.form.get('lga', '')
        location_tag = request.form.get('location_tag', '')
        price = request.form.get('price', '')
        bedrooms = request.form.get('bedrooms', '1')
        bathrooms = request.form.get('bathrooms', '1')
        rental_period = request.form.get('rental_period', 'per_annum')

        if not all([title, description, address, state, price]):
            flash('All fields are required.', 'error')
            return render_template('dashboard_listing_new.html', listing=listing, listing_images=listing_images, listing_videos=listing_videos)

        try:
            price = int(price)
            bedrooms = int(bedrooms)
            bathrooms = int(bathrooms)
        except ValueError:
            flash('Invalid price or room numbers.', 'error')
            return render_template('dashboard_listing_new.html', listing=listing, listing_images=listing_images, listing_videos=listing_videos)

        db.execute(
            """UPDATE listings SET title=?, description=?, address=?, state=?, lga=?, location_tag=?,
               price=?, bedrooms=?, bathrooms=?, rental_period=?, updated_at=? WHERE id=?""",
            (title, description, address, state, lga, location_tag, price, bedrooms, bathrooms,
             rental_period, datetime.now().isoformat(), listing_id)
        )
        db.commit()
        log_action(session['user_id'], 'listing_updated', 'listing', listing_id)
        flash('Listing updated successfully.', 'success')
        return redirect(url_for('admin_dashboard') if session.get('is_admin') and listing['owner_id'] != session['user_id'] else url_for('dashboard_listings'))

    return render_template('dashboard_listing_new.html', listing=listing, listing_images=listing_images, listing_videos=listing_videos)

@app.route('/dashboard/listings/<int:listing_id>/delete', methods=['POST'])
@login_required
@seller_required
def dashboard_listing_delete(listing_id):
    db = get_db()
    if session.get('is_admin'):
        listing = db.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    else:
        listing = db.execute(
            'SELECT * FROM listings WHERE id = ? AND owner_id = ?',
            (listing_id, session['user_id'])
        ).fetchone()

    if not listing:
        flash('That listing could not be found — it may have already been removed.', 'error')
        return redirect(url_for('admin_dashboard') if session.get('is_admin') else url_for('dashboard_listings'))

    owner_row = db.execute('SELECT name FROM users WHERE id = ?', (listing['owner_id'],)).fetchone()
    images = db.execute('SELECT image_path FROM listing_images WHERE listing_id = ?', (listing_id,)).fetchall()
    videos = db.execute('SELECT video_path FROM listing_videos WHERE listing_id = ?', (listing_id,)).fetchall()
    delete_reason = sanitize_input(request.form.get('reason', ''))

    # Archive a full snapshot before anything is removed, so deleted listing
    # info isn't just gone — it lives on in its own table for admin reference.
    db.execute(
        """INSERT INTO deleted_listings
           (original_listing_id, owner_id, owner_name, title, description, address, state, lga,
            location_tag, price, rental_period, bedrooms, bathrooms, verification_status, status,
            images_json, videos_json, deleted_by, delete_reason, deleted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (listing_id, listing['owner_id'], owner_row['name'] if owner_row else None,
         listing['title'], listing['description'], listing['address'], listing['state'], listing['lga'],
         listing['location_tag'], listing['price'], listing['rental_period'], listing['bedrooms'],
         listing['bathrooms'], listing['verification_status'], listing['status'],
         json.dumps([i['image_path'] for i in images]), json.dumps([v['video_path'] for v in videos]),
         session['user_id'], delete_reason or None, datetime.now().isoformat())
    )

    db.execute('DELETE FROM listing_images WHERE listing_id = ?', (listing_id,))
    db.execute('DELETE FROM listing_videos WHERE listing_id = ?', (listing_id,))
    db.execute('DELETE FROM documents WHERE listing_id = ?', (listing_id,))
    db.execute('DELETE FROM messages WHERE listing_id = ?', (listing_id,))
    db.execute('DELETE FROM listings WHERE id = ?', (listing_id,))
    db.commit()

    is_foreign = session.get('is_admin') and listing['owner_id'] != session['user_id']
    log_action(session['user_id'], 'listing_deleted', 'listing', listing_id,
               'Deleted by admin' if is_foreign else None)
    flash('Listing deleted.', 'success')
    return redirect(url_for('admin_dashboard') if is_foreign else url_for('dashboard_listings'))

@app.route('/listing/<int:listing_id>')
def listing_detail(listing_id):
    """View a single listing - fixed route name"""
    db = get_db()
    listing = db.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    
    if not listing:
        flash('That listing is no longer available — it may have been removed.', 'error')
        return redirect(url_for('browse'))
    
    owner = db.execute('SELECT id, name, phone, kyc_status, company_status, account_status FROM users WHERE id = ?', (listing['owner_id'],)).fetchone()
    images = db.execute('SELECT * FROM listing_images WHERE listing_id = ? ORDER BY id', (listing_id,)).fetchall()
    videos = db.execute('SELECT * FROM listing_videos WHERE listing_id = ? ORDER BY id', (listing_id,)).fetchall()

    is_favorited = False
    if 'user_id' in session:
        is_favorited = db.execute(
            'SELECT 1 FROM favorites WHERE user_id = ? AND listing_id = ?',
            (session['user_id'], listing_id)
        ).fetchone() is not None

    return render_template('listing.html', listing=listing, owner=owner, images=images, videos=videos, is_favorited=is_favorited)

@app.route('/listing/<int:listing_id>/report', methods=['POST'])
def report_listing(listing_id):
    db = get_db()
    listing = db.execute('SELECT id FROM listings WHERE id = ?', (listing_id,)).fetchone()
    
    if not listing:
        flash('That listing is no longer available.', 'error')
        return redirect(url_for('browse'))
    
    reporter_id = session.get('user_id')
    reason = sanitize_input(request.form.get('reason', ''))
    details = sanitize_input(request.form.get('details', ''))
    
    if not reason or not details:
        flash('Please provide reason and details.', 'error')
        return redirect(url_for('listing_detail', listing_id=listing_id))
    
    db.execute(
        """INSERT INTO reports 
        (listing_id, reporter_id, report_reason, report_details, created_at)
        VALUES (?, ?, ?, ?, ?)""",
        (listing_id, reporter_id, reason, details, datetime.now().isoformat())
    )
    
    # Flag listing if multiple reports
    report_count = db.execute(
        'SELECT COUNT(*) as count FROM reports WHERE listing_id = ?',
        (listing_id,)
    ).fetchone()
    
    if report_count['count'] >= 3:
        db.execute('UPDATE listings SET flagged_for_review = 1, fraud_score = fraud_score + 30 WHERE id = ?', (listing_id,))
    
    db.commit()
    log_action(reporter_id, 'listing_reported', 'listing', listing_id)
    flash('Report submitted. Thank you for helping keep our community safe.', 'success')
    
    return redirect(url_for('listing_detail', listing_id=listing_id))

# ---------------------------------------------------------------- MESSAGING (BUYER <-> LISTER CHAT)
@app.route('/messages')
@login_required
def messages_inbox():
    db = get_db()
    uid = session['user_id']
    partners = db.execute(
        """SELECT u.id, u.name, u.role,
                  (SELECT body FROM messages m2
                   WHERE (m2.sender_id = u.id AND m2.recipient_id = ?) OR (m2.sender_id = ? AND m2.recipient_id = u.id)
                   ORDER BY m2.created_at DESC LIMIT 1) AS last_message,
                  (SELECT created_at FROM messages m3
                   WHERE (m3.sender_id = u.id AND m3.recipient_id = ?) OR (m3.sender_id = ? AND m3.recipient_id = u.id)
                   ORDER BY m3.created_at DESC LIMIT 1) AS last_at
           FROM users u
           WHERE u.id IN (
               SELECT sender_id FROM messages WHERE recipient_id = ?
               UNION
               SELECT recipient_id FROM messages WHERE sender_id = ?
           )
           ORDER BY last_at DESC""",
        (uid, uid, uid, uid, uid, uid)
    ).fetchall()
    return render_template('messages_inbox.html', partners=partners)

@app.route('/messages/<int:other_id>', methods=['GET', 'POST'])
@login_required
def messages_thread(other_id):
    db = get_db()
    uid = session['user_id']
    other = db.execute('SELECT id, name, role, kyc_status FROM users WHERE id = ?', (other_id,)).fetchone()
    if not other:
        flash('That user could not be found.', 'error')
        return redirect(url_for('messages_inbox'))

    listing_id = request.args.get('listing_id', type=int)

    if request.method == 'POST':
        body = sanitize_input(request.form.get('body', ''))
        listing_id = request.form.get('listing_id', type=int)
        if body:
            db.execute(
                """INSERT INTO messages (sender_id, recipient_id, listing_id, body, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (uid, other_id, listing_id, body, datetime.now().isoformat())
            )
            db.commit()
        return redirect(url_for('messages_thread', other_id=other_id, listing_id=listing_id))

    thread = db.execute(
        """SELECT * FROM messages WHERE (sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?)
           ORDER BY created_at ASC""",
        (uid, other_id, other_id, uid)
    ).fetchall()

    db.execute(
        "UPDATE messages SET read_at = ? WHERE recipient_id = ? AND sender_id = ? AND read_at IS NULL",
        (datetime.now().isoformat(), uid, other_id)
    )
    db.commit()

    listing = None
    if listing_id:
        listing = db.execute('SELECT id, title FROM listings WHERE id = ?', (listing_id,)).fetchone()

    return render_template('messages_thread.html', other=other, thread=thread, listing=listing)

# ---------------------------------------------------------------- TRANSACTIONS / ESCROW
@app.route('/listing/<int:listing_id>/pay', methods=['POST'])
@login_required
def pay_listing(listing_id):
    db = get_db()
    listing = db.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    if not listing:
        flash('That listing is no longer available.', 'error')
        return redirect(url_for('browse'))

    buyer = db.execute('SELECT email FROM users WHERE id = ?', (session['user_id'],)).fetchone()

    amount = listing['price']
    platform_fee = round(amount * ESCROW_FEE_PERCENT / 100)
    seller_payout = amount - platform_fee

    reference = f"achulo_{secrets.token_hex(12)}"
    now = datetime.now().isoformat()
    db.execute(
        """INSERT INTO transactions
        (buyer_id, listing_id, amount, platform_fee, seller_payout, status, transaction_hash, paystack_reference, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'awaiting_payment', ?, ?, ?, ?)""",
        (session['user_id'], listing_id, amount, platform_fee, seller_payout, reference, reference, now, now)
    )
    db.commit()
    log_action(session['user_id'], 'escrow_payment_initiated', 'listing', listing_id)

    callback_url = url_for('paystack_callback', _external=True)
    authorization_url, error = paystack_initialize(buyer['email'], amount, reference, callback_url)

    if error:
        flash(f'Could not start payment: {error}', 'error')
        return redirect(url_for('transactions'))

    return redirect(authorization_url)

@app.route('/payments/callback')
def paystack_callback():
    """Paystack redirects the buyer here after checkout. This is for UX only —
    the webhook below is the source of truth for actually crediting escrow."""
    reference = request.args.get('reference') or request.args.get('trxref')
    if not reference:
        flash('No payment reference was returned by Paystack.', 'error')
        return redirect(url_for('transactions'))

    db = get_db()
    tx = db.execute('SELECT * FROM transactions WHERE paystack_reference = ?', (reference,)).fetchone()
    if not tx:
        flash('That payment could not be matched to a transaction.', 'error')
        return redirect(url_for('transactions'))

    if tx['status'] != 'awaiting_payment':
        # Already settled, likely by the webhook — nothing to do.
        return redirect(url_for('transactions'))

    verified, data, error = paystack_verify(reference)
    now = datetime.now().isoformat()
    if verified:
        db.execute(
            "UPDATE transactions SET status = 'pending', paid_at = ?, updated_at = ? WHERE id = ?",
            (now, now, tx['id'])
        )
        db.commit()
        log_action(tx['buyer_id'], 'escrow_payment_confirmed', 'transaction', tx['id'])
        flash('Payment confirmed — funds are now held in escrow.', 'success')
    else:
        db.execute(
            "UPDATE transactions SET status = 'failed', updated_at = ? WHERE id = ?",
            (now, tx['id'])
        )
        db.commit()
        flash(f'Payment was not successful{": " + error if error else "."}', 'error')

    return redirect(url_for('transactions'))

@app.route('/payments/webhook', methods=['POST'])
def paystack_webhook():
    """Paystack's server-to-server notification — the reliable path, independent of
    whether the buyer's browser makes it back to /payments/callback."""
    if not PAYSTACK_SECRET_KEY:
        return jsonify({'status': 'ignored'}), 200

    signature = request.headers.get('X-Paystack-Signature', '')
    computed = hmac.new(PAYSTACK_SECRET_KEY.encode('utf-8'), request.data, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(signature, computed):
        return jsonify({'status': 'invalid signature'}), 401

    event = request.get_json(silent=True) or {}
    if event.get('event') == 'charge.success':
        reference = event.get('data', {}).get('reference')
        db = get_db()
        tx = db.execute('SELECT * FROM transactions WHERE paystack_reference = ?', (reference,)).fetchone()
        if tx and tx['status'] == 'awaiting_payment':
            verified, _, _ = paystack_verify(reference)
            if verified:
                now = datetime.now().isoformat()
                db.execute(
                    "UPDATE transactions SET status = 'pending', paid_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, tx['id'])
                )
                db.commit()
                log_action(tx['buyer_id'], 'escrow_payment_confirmed_webhook', 'transaction', tx['id'])

    return jsonify({'status': 'ok'}), 200

@app.route('/transactions/<int:transaction_id>/release', methods=['POST'])
@login_required
def release_escrow(transaction_id):
    db = get_db()
    tx = db.execute(
        "SELECT * FROM transactions WHERE id = ? AND buyer_id = ? AND status = 'pending'",
        (transaction_id, session['user_id'])
    ).fetchone()
    if not tx:
        flash('That transaction could not be found or has already been settled.', 'error')
        return redirect(url_for('transactions'))

    now = datetime.now().isoformat()
    db.execute(
        "UPDATE transactions SET status = 'released', released_at = ?, updated_at = ? WHERE id = ?",
        (now, now, transaction_id)
    )
    db.commit()
    log_action(session['user_id'], 'escrow_released', 'transaction', transaction_id)
    flash(f'Funds released — the listing agent receives ₦{tx["seller_payout"]:,} (₦{tx["platform_fee"]:,} platform fee).', 'success')
    return redirect(url_for('transactions'))

@app.route('/transactions/<int:transaction_id>/dispute', methods=['POST'])
@login_required
def dispute_escrow(transaction_id):
    db = get_db()
    tx = db.execute(
        "SELECT * FROM transactions WHERE id = ? AND buyer_id = ? AND status = 'pending'",
        (transaction_id, session['user_id'])
    ).fetchone()
    if not tx:
        flash('That transaction could not be found or has already been settled.', 'error')
        return redirect(url_for('transactions'))

    reason = sanitize_input(request.form.get('reason', ''))
    if not reason:
        flash('Please describe the problem so an admin can review it.', 'error')
        return redirect(url_for('transactions'))

    now = datetime.now().isoformat()
    db.execute(
        "UPDATE transactions SET status = 'disputed', dispute_reason = ?, updated_at = ? WHERE id = ?",
        (reason, now, transaction_id)
    )
    db.commit()
    log_action(session['user_id'], 'escrow_disputed', 'transaction', transaction_id, reason)
    flash('Dispute submitted. Your funds stay in escrow while an admin reviews it.', 'success')
    return redirect(url_for('transactions'))

@app.route('/transactions')
@login_required
def transactions():
    db = get_db()
    rows = db.execute(
        """SELECT t.*, l.title AS title FROM transactions t
           JOIN listings l ON l.id = t.listing_id
           WHERE t.buyer_id = ? ORDER BY t.created_at DESC""",
        (session['user_id'],)
    ).fetchall()
    return render_template('transactions.html', transactions=rows, fee_percent=ESCROW_FEE_PERCENT)

# ---------------------------------------------------------------- SAVED PROPERTIES (favorites)
@app.route('/listing/<int:listing_id>/favorite', methods=['POST'])
@login_required
def toggle_favorite(listing_id):
    db = get_db()
    listing = db.execute('SELECT id FROM listings WHERE id = ?', (listing_id,)).fetchone()
    if not listing:
        flash('That listing is no longer available.', 'error')
        return redirect(url_for('browse'))

    existing = db.execute(
        'SELECT id FROM favorites WHERE user_id = ? AND listing_id = ?',
        (session['user_id'], listing_id)
    ).fetchone()

    if existing:
        db.execute('DELETE FROM favorites WHERE id = ?', (existing['id'],))
        flash('Removed from saved properties.', 'success')
    else:
        db.execute(
            'INSERT INTO favorites (user_id, listing_id, created_at) VALUES (?, ?, ?)',
            (session['user_id'], listing_id, datetime.now().isoformat())
        )
        flash('Saved to your properties.', 'success')
    db.commit()

    next_url = request.form.get('next') or url_for('listing_detail', listing_id=listing_id)
    return redirect(next_url)

@app.route('/dashboard/favorites')
@login_required
def dashboard_favorites():
    db = get_db()
    listings = db.execute(
        """SELECT l.*, f.created_at AS saved_at FROM favorites f
           JOIN listings l ON l.id = f.listing_id
           WHERE f.user_id = ? ORDER BY f.created_at DESC""",
        (session['user_id'],)
    ).fetchall()
    return render_template('dashboard_favorites.html', listings=listings)

# ---------------------------------------------------------------- PROPERTY REQUESTS (buyer "leads")
@app.route('/dashboard/requests', methods=['GET', 'POST'])
@login_required
def dashboard_requests():
    db = get_db()
    if request.method == 'POST':
        title = sanitize_input(request.form.get('title', ''))
        location_tag = sanitize_input(request.form.get('location_tag', ''))
        state = request.form.get('state', '')
        min_price = request.form.get('min_price', type=int)
        max_price = request.form.get('max_price', type=int)
        bedrooms = request.form.get('bedrooms', type=int)
        notes = sanitize_input(request.form.get('notes', ''))

        if not title or not location_tag:
            flash('Please describe what you\'re looking for and where.', 'error')
        else:
            db.execute(
                """INSERT INTO property_requests
                (user_id, title, location_tag, state, min_price, max_price, bedrooms, notes, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
                (session['user_id'], title, location_tag, state, min_price, max_price, bedrooms, notes, datetime.now().isoformat())
            )
            db.commit()
            flash('Request posted — listing agents in that area will see it.', 'success')
        return redirect(url_for('dashboard_requests'))

    requests_rows = db.execute(
        'SELECT * FROM property_requests WHERE user_id = ? ORDER BY created_at DESC',
        (session['user_id'],)
    ).fetchall()
    return render_template('dashboard_requests.html', requests=requests_rows)

@app.route('/dashboard/requests/<int:request_id>/close', methods=['POST'])
@login_required
def close_request(request_id):
    db = get_db()
    row = db.execute(
        'SELECT id FROM property_requests WHERE id = ? AND user_id = ?',
        (request_id, session['user_id'])
    ).fetchone()
    if not row:
        flash('That request could not be found.', 'error')
        return redirect(url_for('dashboard_requests'))

    db.execute("UPDATE property_requests SET status = 'closed' WHERE id = ?", (request_id,))
    db.commit()
    flash('Request closed.', 'success')
    return redirect(url_for('dashboard_requests'))

# ---------------------------------------------------------------- LEADS (seeker request alerts, for listers)
@app.route('/dashboard/leads')
@login_required
@seller_required
def dashboard_leads():
    db = get_db()
    leads = db.execute(
        """SELECT r.*, u.name AS seeker_name FROM property_requests r
           JOIN users u ON u.id = r.user_id
           WHERE r.status = 'open' ORDER BY r.created_at DESC LIMIT 50"""
    ).fetchall()
    return render_template('dashboard_leads.html', leads=leads)

# ---------------------------------------------------------------- ACCOUNT REVIEW / APPEALS
@app.route('/appeal', methods=['GET', 'POST'])
def appeal():
    db = get_db()
    prefill_email = request.args.get('email', '')

    if request.method == 'POST':
        email = sanitize_input(request.form.get('email', ''))
        message = sanitize_input(request.form.get('message', ''))

        if not email or not message:
            flash('Please provide your email and a message.', 'error')
            return render_template('appeal.html', prefill_email=email)

        user = db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
        db.execute(
            """INSERT INTO appeals (user_id, email, message, created_at) VALUES (?, ?, ?, ?)""",
            (user['id'] if user else None, email, message, datetime.now().isoformat())
        )
        db.commit()
        flash('Your review request has been submitted. An admin will get back to you.', 'success')
        return redirect(url_for('login'))

    return render_template('appeal.html', prefill_email=prefill_email)

@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db()
    
    stats = {
        'total_users': db.execute('SELECT COUNT(*) as count FROM users').fetchone()['count'],
        'total_listings': db.execute('SELECT COUNT(*) as count FROM listings').fetchone()['count'],
        'pending_kyc': db.execute("SELECT COUNT(*) as count FROM users WHERE kyc_status = 'pending'").fetchone()['count'],
        'pending_company': db.execute("SELECT COUNT(*) as count FROM users WHERE company_status = 'pending'").fetchone()['count'],
        'pending_reviews': db.execute("SELECT COUNT(*) as count FROM listings WHERE verification_status = 'pending_review'").fetchone()['count'],
        'flagged_listings': db.execute("SELECT COUNT(*) as count FROM listings WHERE flagged_for_review = 1").fetchone()['count'],
        'pending_appeals': db.execute("SELECT COUNT(*) as count FROM appeals WHERE status = 'pending'").fetchone()['count'],
        'disputed_transactions': db.execute("SELECT COUNT(*) as count FROM transactions WHERE status = 'disputed'").fetchone()['count'],
    }

    pending_docs = db.execute(
        """SELECT u.id, u.name, u.email, d.file_path, d.file_name, d.created_at
           FROM users u
           JOIN documents d ON u.id = d.user_id AND d.document_type = 'identity_document'
           WHERE u.kyc_status = 'pending'
           ORDER BY d.created_at"""
    ).fetchall()

    pending_company_docs = db.execute(
        """SELECT u.id, u.name, u.email,
             GROUP_CONCAT(d.document_type || '::' || d.file_name, '||') AS docs
           FROM users u
           JOIN documents d ON u.id = d.user_id AND d.document_type IN ('company_incorporation','company_proof')
           WHERE u.company_status = 'pending'
           GROUP BY u.id
           ORDER BY MAX(d.created_at)"""
    ).fetchall()

    pending_listings = db.execute(
        """SELECT l.*, u.name AS owner_name FROM pending_listings_db l
           JOIN users u ON u.id = l.owner_id
           ORDER BY l.created_at DESC"""
    ).fetchall()

    all_users = db.execute(
        "SELECT id, name, email, role, kyc_status, account_status, ban_reason, created_at FROM users ORDER BY created_at DESC"
    ).fetchall()

    all_listings = db.execute(
        """SELECT l.*, u.name AS owner_name FROM listings l JOIN users u ON u.id = l.owner_id
           ORDER BY l.created_at DESC"""
    ).fetchall()

    appeals = db.execute(
        """SELECT a.*, u.name AS user_name FROM appeals a LEFT JOIN users u ON u.id = a.user_id
           WHERE a.status = 'pending' ORDER BY a.created_at DESC"""
    ).fetchall()

    disputed_transactions = db.execute(
        """SELECT t.*, l.title AS listing_title, buyer.name AS buyer_name,
             seller.name AS seller_name
           FROM transactions t
           JOIN listings l ON l.id = t.listing_id
           JOIN users buyer ON buyer.id = t.buyer_id
           JOIN users seller ON seller.id = l.owner_id
           WHERE t.status = 'disputed'
           ORDER BY t.updated_at DESC"""
    ).fetchall()

    deleted_listings = db.execute(
        """SELECT dl.*, u.name AS deleted_by_name FROM deleted_listings dl
           LEFT JOIN users u ON u.id = dl.deleted_by
           ORDER BY dl.deleted_at DESC LIMIT 100"""
    ).fetchall()

    return render_template(
        'admin_dashboard.html', stats=stats, pending_docs=pending_docs,
        pending_company_docs=pending_company_docs,
        pending_listings=pending_listings, all_users=all_users,
        all_listings=all_listings, appeals=appeals,
        disputed_transactions=disputed_transactions, deleted_listings=deleted_listings,
        fee_percent=ESCROW_FEE_PERCENT
    )

@app.route('/admin/verify-user/<int:user_id>', methods=['POST'])
@admin_required
def verify_user_kyc(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    
    if not user:
        flash('That user could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))
    
    db.execute(
        """UPDATE users SET kyc_status = 'verified', kyc_verified_at = ? WHERE id = ?""",
        (datetime.now().isoformat(), user_id)
    )
    db.commit()
    
    log_action(session['user_id'], 'user_kyc_verified', 'user', user_id, f'Admin verified KYC for {user["name"]}')
    flash(f'User {user["name"]} KYC verified.', 'success')
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject-user/<int:user_id>', methods=['POST'])
@admin_required
def reject_user_kyc(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        flash('That user could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    db.execute("UPDATE users SET kyc_status = 'rejected' WHERE id = ?", (user_id,))
    db.commit()
    log_action(session['user_id'], 'user_kyc_rejected', 'user', user_id)
    flash(f'Certificate for {user["name"]} was rejected.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/verify-company/<int:user_id>', methods=['POST'])
@admin_required
def verify_company_kyc(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        flash('That user could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    db.execute("UPDATE users SET company_status = 'verified' WHERE id = ?", (user_id,))
    db.commit()
    log_action(session['user_id'], 'company_verified', 'user', user_id, f'Admin verified company for {user["name"]}')
    flash(f'{user["name"]} now has the Verified Agent badge.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject-company/<int:user_id>', methods=['POST'])
@admin_required
def reject_company_kyc(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        flash('That user could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    db.execute("UPDATE users SET company_status = 'rejected' WHERE id = ?", (user_id,))
    db.commit()
    log_action(session['user_id'], 'company_rejected', 'user', user_id)
    flash(f'Company documents for {user["name"]} were rejected.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/<int:user_id>/suspend', methods=['POST'])
@admin_required
def suspend_user(user_id):
    db = get_db()
    reason = sanitize_input(request.form.get('reason', 'Policy violation'))
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        flash('That user could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    db.execute(
        "UPDATE users SET account_status = 'suspended', ban_reason = ?, status_updated_at = ? WHERE id = ?",
        (reason, datetime.now().isoformat(), user_id)
    )
    db.commit()
    log_action(session['user_id'], 'user_suspended', 'user', user_id, reason)
    flash(f'User {user["name"]} suspended.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/<int:user_id>/ban', methods=['POST'])
@admin_required
def ban_user(user_id):
    db = get_db()
    reason = sanitize_input(request.form.get('reason', 'Policy violation'))
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        flash('That user could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    db.execute(
        "UPDATE users SET account_status = 'banned', ban_reason = ?, status_updated_at = ? WHERE id = ?",
        (reason, datetime.now().isoformat(), user_id)
    )
    db.commit()
    log_action(session['user_id'], 'user_banned', 'user', user_id, reason)
    flash(f'User {user["name"]} banned.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/<int:user_id>/restore', methods=['POST'])
@admin_required
def restore_user(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        flash('That user could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    db.execute(
        "UPDATE users SET account_status = 'active', ban_reason = NULL, status_updated_at = ? WHERE id = ?",
        (datetime.now().isoformat(), user_id)
    )
    db.commit()
    log_action(session['user_id'], 'user_restored', 'user', user_id)
    flash(f'User {user["name"]} restored to active.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/approve-listing/<int:listing_id>', methods=['POST'])
@admin_required
def approve_listing(listing_id):
    db = get_db()
    listing = db.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    
    if not listing:
        flash('That listing could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))
    
    db.execute(
        """UPDATE listings SET verification_status = 'verified', verified_at = ?, 
           verified_by_admin_id = ?, status = 'active' WHERE id = ?""",
        (datetime.now().isoformat(), session['user_id'], listing_id)
    )
    db.commit()
    
    log_action(session['user_id'], 'listing_approved', 'listing', listing_id)
    flash('Listing approved and made active.', 'success')
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject-listing/<int:listing_id>', methods=['POST'])
@admin_required
def reject_listing(listing_id):
    db = get_db()
    reason = sanitize_input(request.form.get('reason', ''))
    listing = db.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    if not listing:
        flash('That listing could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    db.execute(
        "UPDATE listings SET verification_status = 'rejected', status = 'rejected', updated_at = ? WHERE id = ?",
        (datetime.now().isoformat(), listing_id)
    )
    db.commit()
    log_action(session['user_id'], 'listing_rejected', 'listing', listing_id, reason)
    flash('Listing rejected.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/suspend-listing/<int:listing_id>', methods=['POST'])
@admin_required
def suspend_listing(listing_id):
    db = get_db()
    listing = db.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    if not listing:
        flash('That listing could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    db.execute("UPDATE listings SET status = 'suspended', updated_at = ? WHERE id = ?",
               (datetime.now().isoformat(), listing_id))
    db.commit()
    log_action(session['user_id'], 'listing_suspended', 'listing', listing_id)
    flash('Listing suspended.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/restore-listing/<int:listing_id>', methods=['POST'])
@admin_required
def restore_listing(listing_id):
    db = get_db()
    listing = db.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    if not listing:
        flash('That listing could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    new_status = 'active' if listing['verification_status'] == 'verified' else 'draft'
    db.execute("UPDATE listings SET status = ?, updated_at = ? WHERE id = ?",
               (new_status, datetime.now().isoformat(), listing_id))
    db.commit()
    log_action(session['user_id'], 'listing_restored', 'listing', listing_id)
    flash('Listing restored.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/appeals/<int:appeal_id>/resolve', methods=['POST'])
@admin_required
def resolve_appeal(appeal_id):
    db = get_db()
    decision = request.form.get('decision')  # 'approve' or 'deny'
    response = sanitize_input(request.form.get('response', ''))
    appeal_row = db.execute('SELECT * FROM appeals WHERE id = ?', (appeal_id,)).fetchone()
    if not appeal_row:
        flash('That appeal could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    new_status = 'approved' if decision == 'approve' else 'denied'
    db.execute(
        "UPDATE appeals SET status = ?, admin_response = ?, resolved_at = ? WHERE id = ?",
        (new_status, response, datetime.now().isoformat(), appeal_id)
    )

    if decision == 'approve' and appeal_row['user_id']:
        db.execute(
            "UPDATE users SET account_status = 'active', ban_reason = NULL, status_updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), appeal_row['user_id'])
        )

    db.commit()
    log_action(session['user_id'], 'appeal_resolved', 'appeal', appeal_id, new_status)
    flash(f'Appeal {new_status}.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/transactions/<int:transaction_id>/release', methods=['POST'])
@admin_required
def admin_release_escrow(transaction_id):
    db = get_db()
    tx = db.execute("SELECT * FROM transactions WHERE id = ? AND status = 'disputed'", (transaction_id,)).fetchone()
    if not tx:
        flash('That transaction could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    now = datetime.now().isoformat()
    db.execute(
        "UPDATE transactions SET status = 'released', released_at = ?, updated_at = ? WHERE id = ?",
        (now, now, transaction_id)
    )
    db.commit()
    log_action(session['user_id'], 'admin_escrow_released', 'transaction', transaction_id)
    flash(f'Dispute resolved in favor of the lister — ₦{tx["seller_payout"]:,} released.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/transactions/<int:transaction_id>/refund', methods=['POST'])
@admin_required
def admin_refund_escrow(transaction_id):
    db = get_db()
    tx = db.execute("SELECT * FROM transactions WHERE id = ? AND status = 'disputed'", (transaction_id,)).fetchone()
    if not tx:
        flash('That transaction could not be found.', 'error')
        return redirect(url_for('admin_dashboard'))

    now = datetime.now().isoformat()
    db.execute(
        "UPDATE transactions SET status = 'refunded', updated_at = ? WHERE id = ?",
        (now, transaction_id)
    )
    db.commit()
    log_action(session['user_id'], 'admin_escrow_refunded', 'transaction', transaction_id)
    flash(f'Dispute resolved in favor of the buyer — ₦{tx["amount"]:,} refunded.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, message='Access Denied'), 403

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, message='Page Not Found'), 404

@app.errorhandler(500)
def server_error(e):
    log_action(None, 'server_error', details=str(e))
    return render_template('error.html', code=500, message='Server Error'), 500

if __name__ == '__main__':
    app.run(debug=False)
