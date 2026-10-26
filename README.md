# LinkPOS

LinkPOS is a Django REST Framework and React point-of-sale application backed by PostgreSQL. The working operations in this repository are inventory and product management, customer records, sales checkout, sale cancellation with stock restoration, and sales reporting.

## Local setup

Requirements: Python 3.12, Node.js 20.19+ or 22.12+, npm, and PostgreSQL 17.

Create `backend/.env` from the example and set a private Django secret and PostgreSQL credentials:

```powershell
cd C:\git\pos-system\backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Set `DJANGO_SECRET_KEY` to a long random value, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` to your local PostgreSQL role, and keep `.env` private. `POS_TAX_RATE` defaults to `0.10`; `DJANGO_TIME_ZONE` defaults to `Asia/Colombo`.

Install and initialize the backend:

```powershell
cd C:\git\pos-system\backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py check
python manage.py check_db
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

The frontend uses `http://127.0.0.1:8000/api/v1` by default. To change it, set `VITE_API_BASE_URL` in `frontend/.env`.

```powershell
cd C:\git\pos-system\frontend
npm ci
npm run dev
```

Open the Vite URL in your browser and sign in with a Django user. Create and manage staff accounts using Django admin. API access uses short-lived JWT access tokens with refresh, and all business endpoints require authentication. Health probes are public and return only service status.

## Features

- Dashboard totals and seven-day sales figures are read from PostgreSQL.
- Product records can be created, edited, searched, activated, or archived.
- Customer records can be created, edited, searched, activated, or archived.
- Checkout supports multiple products, stock checks, optional customers, configurable tax, and persisted invoice lines.
- Cancelling a completed sale restores its stock in a transaction; cancelled sales are excluded from revenue totals.
- Sales history shows invoice details. Reports filter by date and export the displayed transactions as CSV.
- The interface adapts to narrow screens and reports API errors rather than substituting sample figures.

## Checks

The isolated test settings use a disposable SQLite test database so the configured PostgreSQL role does not need permission to create databases. PostgreSQL connectivity is checked separately against the configured database.

```powershell
cd C:\git\pos-system\backend
python manage.py test core --settings=pos_system.test_settings
python manage.py check_db

cd C:\git\pos-system\frontend
npm run build
npm audit
```

Before deployment, set `DJANGO_DEBUG=False`, a unique `DJANGO_SECRET_KEY`, production `DJANGO_ALLOWED_HOSTS`, PostgreSQL credentials, HTTPS and CORS settings, then run `python manage.py check --deploy`. Do not expose Django's development server to the public internet.

## API

- `GET /api/v1/health/live/` and `GET /api/v1/health/ready/` — public service and database probes
- `POST /api/v1/auth/token/` and `POST /api/v1/auth/token/refresh/` — JWT authentication
- `GET|POST|PATCH /api/v1/products/` — product and inventory records
- `GET|POST|PATCH /api/v1/customers/` — customer records
- `GET|POST /api/v1/sales/` — sales history and checkout
- `POST /api/v1/sales/{id}/cancel/` — cancel and restock a completed sale
- `GET /api/v1/dashboard/` — sales and inventory metrics
- `GET /api/docs/` — API schema and documentation

This repository does not currently implement purchasing, suppliers, expenses, petty cash, appointments, quotations, payment-gateway settlement, or fiscal receipt integrations. Production deployment also requires environment-specific infrastructure, backups, monitoring, and a reviewed staff role/permission policy; the current API grants the same business permissions to any authenticated user.
