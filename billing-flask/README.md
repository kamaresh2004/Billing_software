# Billing Pro

Billing Pro is a Flask-based retail billing and point-of-sale workspace. It keeps the original app's useful product, stock, cashier, sales, and PDF invoice workflow while adding a transactional checkout, role checks, customers, payments, stock movements, audit records, configurable database URL, responsive UI, and production-friendly configuration points.

## Included

- Responsive Billing Pro dashboard and POS screen
- Product catalog with SKU/barcode-aware lookup, prices, tax, reorder levels, and soft archive
- Stock levels and stock movement records
- Customers and payment methods
- Atomic checkout: sale lines, payment, inventory deduction, and audit event are committed together
- Professional PDF invoices
- Admin, manager, and cashier role enforcement with 403 responses
- Hashed passwords and secure session cookie defaults
- SQLite by default; PostgreSQL/MySQL can be selected with `DATABASE_URL`

## Run locally

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000.

Development accounts are seeded on first run:

- Admin: `admin@billingpro.local` / `admin12345`
- Cashier: `cashier@billingpro.local` / `cashier123`

Set `ADMIN_PASSWORD` and `CASHIER_PASSWORD` before the first run to use different seeded passwords.

## Configuration and migrations

The parent `.env` file is loaded automatically. Important variables include `SECRET_KEY`, `DATABASE_URL`, `APP_NAME`, `ADMIN_PASSWORD`, `CASHIER_PASSWORD`, `FLASK_ENV`, and `CART_ABANDONMENT_HOURS`. External payment, SMS, email, and maps API keys are optional and are intentionally unused until their integrations are enabled.

Apply schema migrations with:

```powershell
$env:FLASK_APP = "app:create_app"
flask db upgrade
```

Create a new migration after changing models with `flask db migrate -m "describe the change"`, then review it before applying.

Run tests with:

```powershell
pytest
```

For production, use a WSGI server and PostgreSQL/MySQL for multi-terminal concurrency:

```powershell
gunicorn -w 4 "app:create_app()"
```

SQLite backup: periodically copy `instance/billing_pro.db` while the app is stopped, or use SQLite's backup API. For PostgreSQL, use `pg_dump` and restore with `pg_restore`.

## Configuration

Useful environment variables:

```text
APP_NAME=Billing Pro
SECRET_KEY=replace-with-a-long-random-value
DATABASE_URL=sqlite:///instance/billing_pro.db
FLASK_DEBUG=0
ADMIN_PASSWORD=replace-before-first-run
CASHIER_PASSWORD=replace-before-first-run
```

The default SQLite database is created at `instance/billing_pro.db`. The application creates its tables and demo catalog automatically for local development. Back up that file before upgrades or use a managed PostgreSQL database for production.

## Project layout

```text
app.py                 Flask routes, authorization, POS transaction workflow
models.py              SQLAlchemy models
pdf_invoice.py         ReportLab invoice document builder
templates/             Billing Pro UI and table partials
static/css/style.css   Responsive design system
instance/              Local database files
```

For production, run behind a WSGI server, set a unique `SECRET_KEY`, disable debug, use PostgreSQL or MySQL, and terminate TLS at the reverse proxy.
