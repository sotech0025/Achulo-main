import os
import sqlite3
import re
import hashlib
import secrets
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
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Create uploads directory
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Admin credentials
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@achulo.test")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me-admin-password")

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
            status TEXT NOT NULL DEFAULT 'pending',
            transaction_hash TEXT UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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
            abort(403)
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
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

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
    return render_template('home.html')

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

        # Listing agents must submit a government certificate/ID for admin review
        gov_cert = request.files.get('gov_certificate')
        if role == 'seller':
            if not gov_cert or not gov_cert.filename:
                flash('Listing agents must upload a government-issued certificate or ID for verification.', 'error')
                return render_template('register.html')
            if not allowed_file(gov_cert.filename):
                flash('Certificate must be a PDF, JPG, PNG, DOC, or DOCX file.', 'error')
                return render_template('register.html')

        db = get_db()
        
        # Check if email exists
        if db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone():
            flash('Email already registered.', 'error')
            return render_template('register.html')

        # Create user
        password_hash = hash_password(password)
        now = datetime.now().isoformat()
        kyc_status = 'pending' if role == 'seller' else 'unverified'

        cur = db.execute(
            """INSERT INTO users 
            (name, email, phone, password_hash, role, kyc_status, country, created_at, updated_at, accepted_terms, accepted_privacy)
            VALUES (?, ?, ?, ?, ?, ?, 'Nigeria', ?, ?, 1, 1)""",
            (name, email, phone, password_hash, role, kyc_status, now, now)
        )
        db.commit()
        
        user_id = cur.lastrowid

        # Save the government certificate for admin review
        if role == 'seller' and gov_cert and gov_cert.filename:
            gov_cert.seek(0, os.SEEK_END)
            size = gov_cert.tell()
            gov_cert.seek(0)
            if size <= MAX_FILE_SIZE:
                filename = secure_filename(f"cert_{user_id}_{int(datetime.now().timestamp())}_{gov_cert.filename}")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                gov_cert.save(filepath)
                db.execute(
                    """INSERT INTO documents (user_id, document_type, file_path, file_name, created_at)
                    VALUES (?, 'government_certificate', ?, ?, ?)""",
                    (user_id, filepath, filename, datetime.now().isoformat())
                )
                db.execute(
                    "UPDATE users SET kyc_document_type = 'government_certificate' WHERE id = ?",
                    (user_id,)
                )
                db.commit()

        log_action(user_id, 'user_registration', 'user', user_id, f'New {role} account created')

        if role == 'seller':
            flash('Account created! Your certificate has been submitted — an admin will review it before you can publish listings.', 'success')
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
            """SELECT l.*, u.name AS lister_name, u.kyc_status AS lister_kyc_status, u.id AS lister_id
               FROM listings l JOIN users u ON u.id = l.owner_id
               WHERE l.status = 'active' ORDER BY l.created_at DESC LIMIT 9"""
        ).fetchall()
        conversations = db.execute(
            """SELECT DISTINCT CASE WHEN sender_id = ? THEN recipient_id ELSE sender_id END AS other_id
               FROM messages WHERE sender_id = ? OR recipient_id = ? LIMIT 5""",
            (session['user_id'], session['user_id'], session['user_id'])
        ).fetchall()
        return render_template('dashboard_buyer.html', user=user, listings=listings, conversations=conversations)

@app.route('/dashboard/kyc', methods=['GET', 'POST'])
@login_required
def dashboard_kyc():
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    if request.method == 'POST':
        doc_type = request.form.get('document_type')
        
        if 'document' not in request.files:
            flash('No file selected.', 'error')
            return render_template('dashboard_kyc.html', user=user)
        
        file = request.files['document']
        
        if file.filename == '':
            flash('No file selected.', 'error')
            return render_template('dashboard_kyc.html', user=user)
        
        if not allowed_file(file.filename):
            flash('File type not allowed. Use PDF, JPG, PNG, DOC, DOCX.', 'error')
            return render_template('dashboard_kyc.html', user=user)
        
        if len(file.read()) > MAX_FILE_SIZE:
            flash('File too large. Maximum 10MB.', 'error')
            return render_template('dashboard_kyc.html', user=user)
        
        file.seek(0)
        filename = secure_filename(f"{session['user_id']}_{doc_type}_{int(datetime.now().timestamp())}_{file.filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Store document info
        db.execute(
            """INSERT INTO documents 
            (user_id, document_type, file_path, file_name, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (session['user_id'], doc_type, filepath, filename, datetime.now().isoformat())
        )
        
        # Update user KYC document
        db.execute(
            'UPDATE users SET kyc_document_type = ?, kyc_document_url = ? WHERE id = ?',
            (doc_type, filename, session['user_id'])
        )
        db.commit()
        
        log_action(session['user_id'], 'document_uploaded', 'document', None, f'Uploaded {doc_type}')
        flash(f'{doc_type.upper()} uploaded successfully. Admin will review shortly.', 'success')
    
    return render_template('dashboard_kyc.html', user=user)

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
        abort(404)
    
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
        abort(404)

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

    return redirect(url_for('dashboard_listings'))

@app.route('/dashboard/listings/<int:listing_id>/edit', methods=['GET', 'POST'])
@login_required
@seller_required
def dashboard_listing_edit(listing_id):
    """Edit an existing listing"""
    db = get_db()
    listing = db.execute(
        'SELECT * FROM listings WHERE id = ? AND owner_id = ?',
        (listing_id, session['user_id'])
    ).fetchone()

    if not listing:
        abort(404)

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
            return render_template('dashboard_listing_new.html', listing=listing)

        try:
            price = int(price)
            bedrooms = int(bedrooms)
            bathrooms = int(bathrooms)
        except ValueError:
            flash('Invalid price or room numbers.', 'error')
            return render_template('dashboard_listing_new.html', listing=listing)

        db.execute(
            """UPDATE listings SET title=?, description=?, address=?, state=?, lga=?, location_tag=?,
               price=?, bedrooms=?, bathrooms=?, rental_period=?, updated_at=? WHERE id=?""",
            (title, description, address, state, lga, location_tag, price, bedrooms, bathrooms,
             rental_period, datetime.now().isoformat(), listing_id)
        )
        db.commit()
        log_action(session['user_id'], 'listing_updated', 'listing', listing_id)
        flash('Listing updated successfully.', 'success')
        return redirect(url_for('dashboard_listings'))

    return render_template('dashboard_listing_new.html', listing=listing)

@app.route('/dashboard/listings/<int:listing_id>/delete', methods=['POST'])
@login_required
@seller_required
def dashboard_listing_delete(listing_id):
    db = get_db()
    listing = db.execute(
        'SELECT * FROM listings WHERE id = ? AND owner_id = ?',
        (listing_id, session['user_id'])
    ).fetchone()

    if not listing:
        abort(404)

    db.execute('DELETE FROM listing_images WHERE listing_id = ?', (listing_id,))
    db.execute('DELETE FROM documents WHERE listing_id = ?', (listing_id,))
    db.execute('DELETE FROM messages WHERE listing_id = ?', (listing_id,))
    db.execute('DELETE FROM listings WHERE id = ?', (listing_id,))
    db.commit()

    log_action(session['user_id'], 'listing_deleted', 'listing', listing_id)
    flash('Listing deleted.', 'success')
    return redirect(url_for('dashboard_listings'))

@app.route('/listing/<int:listing_id>')
def listing_detail(listing_id):
    """View a single listing - fixed route name"""
    db = get_db()
    listing = db.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
    
    if not listing:
        abort(404)
    
    owner = db.execute('SELECT id, name, phone, kyc_status, account_status FROM users WHERE id = ?', (listing['owner_id'],)).fetchone()
    images = db.execute('SELECT * FROM listing_images WHERE listing_id = ? ORDER BY id', (listing_id,)).fetchall()

    return render_template('listing.html', listing=listing, owner=owner, images=images)

@app.route('/listing/<int:listing_id>/report', methods=['POST'])
def report_listing(listing_id):
    db = get_db()
    listing = db.execute('SELECT id FROM listings WHERE id = ?', (listing_id,)).fetchone()
    
    if not listing:
        abort(404)
    
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
        abort(404)

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
        abort(404)

    tx_hash = secrets.token_hex(16)
    now = datetime.now().isoformat()
    db.execute(
        """INSERT INTO transactions (buyer_id, listing_id, amount, status, transaction_hash, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
        (session['user_id'], listing_id, listing['price'], tx_hash, now, now)
    )
    db.commit()
    log_action(session['user_id'], 'escrow_payment_initiated', 'listing', listing_id)
    flash('Payment placed in escrow. It will be released once you confirm the property.', 'success')
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
    return render_template('transactions.html', transactions=rows)

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
        'pending_reviews': db.execute("SELECT COUNT(*) as count FROM listings WHERE verification_status = 'pending_review'").fetchone()['count'],
        'flagged_listings': db.execute("SELECT COUNT(*) as count FROM listings WHERE flagged_for_review = 1").fetchone()['count'],
        'pending_appeals': db.execute("SELECT COUNT(*) as count FROM appeals WHERE status = 'pending'").fetchone()['count'],
    }

    pending_docs = db.execute(
        """SELECT u.id, u.name, u.email, u.kyc_document_type, d.file_path, d.file_name, d.created_at
           FROM users u
           JOIN documents d ON u.id = d.user_id AND d.document_type = 'government_certificate'
           WHERE u.kyc_status = 'pending'
           ORDER BY d.created_at"""
    ).fetchall()

    pending_listings = db.execute(
        """SELECT l.*, u.name AS owner_name FROM listings l
           JOIN users u ON u.id = l.owner_id
           WHERE l.verification_status IN ('pending_review', 'pending_documents')
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

    return render_template(
        'admin_dashboard.html', stats=stats, pending_docs=pending_docs,
        pending_listings=pending_listings, all_users=all_users,
        all_listings=all_listings, appeals=appeals
    )

@app.route('/admin/verify-user/<int:user_id>', methods=['POST'])
@admin_required
def verify_user_kyc(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    
    if not user:
        abort(404)
    
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
        abort(404)

    db.execute("UPDATE users SET kyc_status = 'rejected' WHERE id = ?", (user_id,))
    db.commit()
    log_action(session['user_id'], 'user_kyc_rejected', 'user', user_id)
    flash(f'Certificate for {user["name"]} was rejected.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/<int:user_id>/suspend', methods=['POST'])
@admin_required
def suspend_user(user_id):
    db = get_db()
    reason = sanitize_input(request.form.get('reason', 'Policy violation'))
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        abort(404)

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
        abort(404)

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
        abort(404)

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
        abort(404)
    
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
        abort(404)

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
        abort(404)

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
        abort(404)

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
        abort(404)

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
