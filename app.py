import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, g, render_template, request, redirect, url_for, session, flash, abort

APP_NAME = "ACHOULO"
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "achoulo.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")

# Admin credentials come from environment variables only — never hardcoded.
# Set ADMIN_EMAIL / ADMIN_PASSWORD in your Render environment (or a local .env).
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@achoulo.test")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me-admin-password")

PRICE_PRESETS = [
    {"label": "Any", "min": None, "max": None},
    {"label": "Under \u20a6500k", "min": None, "max": 500_000},
    {"label": "\u20a6500k \u2013 \u20a61M", "min": 500_000, "max": 1_000_000},
    {"label": "\u20a61M \u2013 \u20a63M", "min": 1_000_000, "max": 3_000_000},
    {"label": "\u20a63M \u2013 \u20a610M", "min": 3_000_000, "max": 10_000_000},
    {"label": "Over \u20a610M", "min": 10_000_000, "max": None},
]


# ---------------------------------------------------------------- database
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
            role TEXT NOT NULL DEFAULT 'agent',
            kyc_status TEXT NOT NULL DEFAULT 'unverified',
            nin TEXT,
            country TEXT,
            created_at TEXT NOT NULL
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
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            listing_id INTEGER NOT NULL REFERENCES listings(id),
            amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'escrow',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL REFERENCES listings(id),
            reporter_name TEXT,
            reason TEXT,
            details TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    # No property or listing seed data is created. The only account
    # auto-created is the admin account (from ADMIN_EMAIL / ADMIN_PASSWORD).
    # Set SEED_DEMO_DATA=true to also create a demo agent *account* (no
    # listings) for local testing; leave unset/false in production.
    seed_demo = os.environ.get("SEED_DEMO_DATA", "false").lower() == "true"

    cur = db.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] == 0:
        from werkzeug.security import generate_password_hash

        now = datetime.utcnow().isoformat()
        db.execute(
            "INSERT INTO users (name, email, password_hash, phone, role, kyc_status, created_at) VALUES (?,?,?,?,?,?,?)",
            ("Achoulo Admin", ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), "+2340000000000", "admin", "verified", now),
        )
        if seed_demo:
            db.execute(
                "INSERT INTO users (name, email, password_hash, phone, role, kyc_status, created_at) VALUES (?,?,?,?,?,?,?)",
                ("Demo Agent", "agent@achoulo.test", generate_password_hash("agent123"), "+2348000000001", "agent", "verified", now),
            )
        db.commit()
    else:
        # Keep the admin account's credentials in sync with current env vars,
        # so rotating ADMIN_EMAIL / ADMIN_PASSWORD takes effect on restart.
        from werkzeug.security import generate_password_hash

        admin_row = db.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
        if admin_row:
            db.execute(
                "UPDATE users SET email = ?, password_hash = ? WHERE id = ?",
                (ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), admin_row["id"]),
            )
            db.commit()
    db.close()


# ------------------------------------------------------------------ helpers
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


@app.context_processor
def inject_globals():
    return {"app_name": APP_NAME, "current_user": current_user()}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in to continue.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def compose_location_tag(state, lga):
    parts = [p for p in [lga, state] if p]
    return ", ".join(parts)


# --------------------------------------------------------------------- home
@app.route("/")
def home():
    db = get_db()
    location = request.args.get("location", "").strip()
    rental_period = request.args.get("rentalPeriod", "").strip()
    price_idx = request.args.get("price", "0")
    try:
        price_idx = int(price_idx)
    except ValueError:
        price_idx = 0
    price_idx = max(0, min(price_idx, len(PRICE_PRESETS) - 1))
    preset = PRICE_PRESETS[price_idx]

    query = "SELECT * FROM listings WHERE status = 'active'"
    params = []
    if location:
        query += " AND location_tag LIKE ?"
        params.append(f"%{location}%")
    if rental_period:
        query += " AND rental_period = ?"
        params.append(rental_period)
    if preset["min"] is not None:
        query += " AND price >= ?"
        params.append(preset["min"])
    if preset["max"] is not None:
        query += " AND price <= ?"
        params.append(preset["max"])
    query += " ORDER BY created_at DESC"

    listings = db.execute(query, params).fetchall()

    active_filters = []
    if location:
        active_filters.append(location)
    if rental_period:
        active_filters.append("Annual" if rental_period == "per_annum" else "Monthly")
    if price_idx > 0:
        active_filters.append(preset["label"])

    return render_template(
        "home.html",
        listings=listings,
        price_presets=PRICE_PRESETS,
        selected_price_idx=price_idx,
        location=location,
        rental_period=rental_period,
        active_filters=active_filters,
    )


# -------------------------------------------------------------------- auth
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        from werkzeug.security import generate_password_hash

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        country = request.form.get("country", "").strip()

        if not name or not email or not password:
            flash("Name, email and password are required.", "error")
            return render_template("register.html")

        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            flash("An account with that email already exists.", "error")
            return render_template("register.html")

        db.execute(
            "INSERT INTO users (name, email, password_hash, phone, role, kyc_status, country, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, email, generate_password_hash(password), phone, "agent", "unverified", country, datetime.utcnow().isoformat()),
        )
        db.commit()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        session["user_id"] = user["id"]
        flash("Account created \u2014 welcome to ACHOULO!", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        from werkzeug.security import check_password_hash

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return render_template("login.html")
        session["user_id"] = user["id"]
        flash("Welcome back!", "success")
        dest = request.args.get("next") or url_for("dashboard")
        return redirect(dest)

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


# ---------------------------------------------------------------- listings
@app.route("/listing/<int:listing_id>")
def listing_detail(listing_id):
    db = get_db()
    listing = db.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if not listing:
        return render_template("not_found.html"), 404
    owner = db.execute("SELECT * FROM users WHERE id = ?", (listing["owner_id"],)).fetchone()
    return render_template("listing.html", listing=listing, owner=owner)


@app.route("/listing/<int:listing_id>/report", methods=["POST"])
def report_listing(listing_id):
    db = get_db()
    db.execute(
        "INSERT INTO reports (listing_id, reporter_name, reason, details, created_at) VALUES (?,?,?,?,?)",
        (
            listing_id,
            request.form.get("reporter_name", "Anonymous"),
            request.form.get("reason", "other"),
            request.form.get("details", ""),
            datetime.utcnow().isoformat(),
        ),
    )
    db.commit()
    flash("Thanks \u2014 our trust & safety team will review this listing.", "success")
    return redirect(url_for("listing_detail", listing_id=listing_id))


@app.route("/listing/<int:listing_id>/pay", methods=["POST"])
@login_required
def pay_listing(listing_id):
    db = get_db()
    listing = db.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if not listing:
        abort(404)
    user = current_user()
    db.execute(
        "INSERT INTO transactions (user_id, listing_id, amount, status, created_at) VALUES (?,?,?,?,?)",
        (user["id"], listing_id, listing["price"], "escrow", datetime.utcnow().isoformat()),
    )
    db.commit()
    flash("Payment placed in 48-hour escrow. Funds release once you confirm the property.", "success")
    return redirect(url_for("transactions"))


# --------------------------------------------------------------- dashboard
@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    user = current_user()
    listings = db.execute(
        "SELECT * FROM listings WHERE owner_id = ? ORDER BY created_at DESC", (user["id"],)
    ).fetchall()
    summary = {
        "totalListings": len(listings),
        "activeListings": sum(1 for l in listings if l["status"] == "active"),
        "totalUnits": sum((l["bedrooms"] or 0) for l in listings),
        "availableUnits": sum(1 for l in listings if l["status"] == "active"),
    }
    return render_template("dashboard.html", summary=summary, listings=listings[:5])


@app.route("/dashboard/listings")
@login_required
def dashboard_listings():
    db = get_db()
    user = current_user()
    listings = db.execute(
        "SELECT * FROM listings WHERE owner_id = ? ORDER BY created_at DESC", (user["id"],)
    ).fetchall()
    return render_template("dashboard_listings.html", listings=listings)


@app.route("/dashboard/listings/new", methods=["GET", "POST"])
@login_required
def dashboard_listing_new():
    if request.method == "POST":
        db = get_db()
        user = current_user()
        title = request.form.get("title", "").strip()
        price = request.form.get("price", "0")
        state = request.form.get("state", "").strip()
        lga = request.form.get("lga", "").strip()
        try:
            price = int(price)
        except ValueError:
            price = 0

        if not title or price <= 0:
            flash("Please provide a title and a valid price.", "error")
            return render_template("dashboard_listing_new.html")

        db.execute(
            """INSERT INTO listings (owner_id, title, description, state, lga, location_tag, address,
               price, rental_period, bedrooms, bathrooms, image_url, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user["id"],
                title,
                request.form.get("description", ""),
                state,
                lga,
                compose_location_tag(state, lga),
                request.form.get("address", ""),
                price,
                request.form.get("rental_period", "per_annum"),
                int(request.form.get("bedrooms", 1) or 1),
                int(request.form.get("bathrooms", 1) or 1),
                request.form.get("image_url", "").strip() or None,
                "active",
                datetime.utcnow().isoformat(),
            ),
        )
        db.commit()
        flash("Listing created and published.", "success")
        return redirect(url_for("dashboard_listings"))

    return render_template("dashboard_listing_new.html")


@app.route("/dashboard/kyc", methods=["GET", "POST"])
@login_required
def dashboard_kyc():
    db = get_db()
    user = current_user()
    if request.method == "POST":
        nin = request.form.get("nin", "").strip()
        if len(nin) != 11 or not nin.isdigit():
            flash("Please enter a valid 11-digit NIN.", "error")
        else:
            db.execute("UPDATE users SET kyc_status = ?, nin = ? WHERE id = ?", ("verified", nin, user["id"]))
            db.commit()
            flash("Identity verified successfully.", "success")
        return redirect(url_for("dashboard_kyc"))

    user = current_user()  # refresh
    return render_template("dashboard_kyc.html", user=user)


@app.route("/dashboard/payouts")
@login_required
def dashboard_payouts():
    db = get_db()
    user = current_user()
    listing_ids = [r["id"] for r in db.execute("SELECT id FROM listings WHERE owner_id = ?", (user["id"],)).fetchall()]
    payouts = []
    if listing_ids:
        q_marks = ",".join("?" * len(listing_ids))
        payouts = db.execute(
            f"SELECT t.*, l.title FROM transactions t JOIN listings l ON l.id = t.listing_id "
            f"WHERE t.listing_id IN ({q_marks}) ORDER BY t.created_at DESC",
            listing_ids,
        ).fetchall()
    total_earned = sum(p["amount"] for p in payouts if p["status"] == "released")
    pending = sum(p["amount"] for p in payouts if p["status"] == "escrow")
    return render_template("dashboard_payouts.html", payouts=payouts, total_earned=total_earned, pending=pending)


@app.route("/transactions")
@login_required
def transactions():
    db = get_db()
    user = current_user()
    txns = db.execute(
        "SELECT t.*, l.title FROM transactions t JOIN listings l ON l.id = t.listing_id "
        "WHERE t.user_id = ? ORDER BY t.created_at DESC",
        (user["id"],),
    ).fetchall()
    return render_template("transactions.html", transactions=txns)


# --------------------------------------------------------------------- admin
@app.route("/admin-<secret>")
def admin_secret_login(secret):
    """Direct admin access via yourdomain.com/admin-<ADMIN_PASSWORD>.

    Logs the visitor in as the admin account and redirects to /admin, as an
    alternative to the normal /login form. The secret is compared with
    hmac.compare_digest to avoid timing attacks.
    """
    import hmac

    if not hmac.compare_digest(secret, ADMIN_PASSWORD):
        abort(404)
    db = get_db()
    admin_row = db.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    if not admin_row:
        abort(404)
    session["user_id"] = admin_row["id"]
    flash("Logged in as admin.", "success")
    return redirect(url_for("admin"))


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    listings = db.execute("SELECT * FROM listings ORDER BY created_at DESC").fetchall()
    reports = db.execute(
        "SELECT r.*, l.title FROM reports r JOIN listings l ON l.id = r.listing_id ORDER BY r.created_at DESC"
    ).fetchall()
    return render_template("admin.html", users=users, listings=listings, reports=reports)


@app.route("/admin/listing/<int:listing_id>/status", methods=["POST"])
@admin_required
def admin_update_listing_status(listing_id):
    status = request.form.get("status", "active")
    db = get_db()
    db.execute("UPDATE listings SET status = ? WHERE id = ?", (status, listing_id))
    db.commit()
    flash("Listing status updated.", "success")
    return redirect(url_for("admin"))


@app.errorhandler(404)
def not_found(e):
    return render_template("not_found.html"), 404


init_db()

# Server-side startup warnings only — never exposed in any page or response.
if app.secret_key == "dev-secret-change-me-in-production":
    print("WARNING: SECRET_KEY is not set. Set a random SECRET_KEY env var before deploying.")
if ADMIN_PASSWORD == "change-me-admin-password":
    print("WARNING: ADMIN_PASSWORD is not set. Set a strong, random ADMIN_PASSWORD env var before deploying.")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
