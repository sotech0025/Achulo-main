# ACHOULO — Flask Edition

A plain HTML + Python/Flask rebuild of the original React (Vite/Replit) app.
No Node, no build step, no Replit tooling anywhere — deploys straight to Render.

## What changed from the original

- **Frontend**: React + Vite + Radix + wouter → server-rendered Jinja2 templates
  + Tailwind (via CDN, no build step) + a few lines of vanilla JS.
- **Backend**: The original zip only contained the React frontend — it called an
  external `@workspace/api-client-react` package for all data (auth, listings,
  KYC, payments, admin) that wasn't included in the export. This edition adds a
  real, working backend instead: Flask + SQLite, with password-hashed auth,
  listing CRUD, a mock escrow payment flow, NIN "KYC" verification, scam
  reports, and an admin panel — implemented directly here, not stubbed.
- **Replit**: `.replit`, `.replit-artifact/`, `replit.md`, and the
  `@replit/vite-plugin-*` dev-only banner/cartographer plugins are gone. There
  is no Replit branding, badge, or dependency left anywhere in this project.

## Run locally

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Visit http://localhost:5000. A SQLite file (`achoulo.db`) is created
automatically on first run, seeded with:

- Admin login: `admin@achoulo.test` / `admin123`
- Demo agent login: `agent@achoulo.test` / `agent123`
- 4 sample property listings

Delete `achoulo.db` any time to reset the demo data.

## Deploy to Render

**Option A — Blueprint (fastest):** push this folder to a GitHub repo, then in
Render click **New → Blueprint** and point it at the repo. `render.yaml` is
already set up (installs `requirements.txt`, runs `gunicorn app:app`,
generates a `SECRET_KEY`).

**Option B — Manual web service:**
1. New → Web Service → connect your repo
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn app:app`
4. Add an environment variable `SECRET_KEY` with any random string

### Note on the database
This uses SQLite on local disk, which is fine for a demo but **is wiped on
every deploy** on Render's free tier (ephemeral filesystem). For anything
beyond a demo, swap in Render's free Postgres and point `DB_PATH`/queries at
it (the schema in `init_db()` in `app.py` is a good starting point).

## Project structure

```
app.py                  Flask app: routes, SQLite models, auth
templates/               Jinja2 HTML templates (one per page)
static/css/style.css     Small style overrides
static/js/main.js        Light progressive-enhancement JS
static/images/           Logo, favicon, social image (carried over from original)
requirements.txt
Procfile                 gunicorn start command
render.yaml               Render blueprint
```
