# PoshPointHub

PoshPointHub is a Django REST Framework and React point-of-sale application backed by PostgreSQL.

## Local setup

Requirements: Python 3.12, Node.js 20.19+ or 22.12+, npm, and PostgreSQL 17.

Create `backend/.env` from the example and set a private Django secret and PostgreSQL credentials:

```powershell
cd C:\git\pos-system\backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -c "import secrets; print(secrets.token_urlsafe(64))"
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

For an explicitly local development catalog, set `POS_SEED_ADMIN_PASSWORD`, `POS_SEED_MANAGER_PASSWORD`, and/or `POS_SEED_CASHIER_PASSWORD` in the private backend `.env`, then run `python manage.py seed_demo`. The command refuses to run unless `DJANGO_DEBUG=True`, is safe to rerun, and creates catalog records only; it does not create fake sales or payments. Remove the optional passwords from `.env` when finished. There are no built-in default passwords.

The frontend uses `http://127.0.0.1:8000/api/v1` by default. To change it, set `VITE_API_BASE_URL` in `frontend/.env`.

If you open the frontend using your computer's LAN address (for example, `http://192.168.1.98:5173`), add that exact origin to `CORS_ALLOWED_ORIGINS` in the private `backend/.env` and restart Django. Keep the list limited to origins you trust.

```powershell
cd C:\git\pos-system\frontend
npm ci
npm run dev
```

Open the Vite URL in your browser and sign in with a Django user. Create and manage staff accounts using Django admin. API access uses short-lived JWT access tokens with refresh, and all business endpoints require authentication. Health probes are public and return only service status.

## Features

- Dashboard totals and daily or monthly sales trends use backend aggregates. Supported periods are 7, 30, 90, or 365 days.
- Products support category, subcategory, brand, description, barcode, SKU, pricing, stock, server-side search/filtering/pagination, and manager-controlled create/update/archive workflows. The legacy category text remains available for compatibility; migration 0006 links existing text categories to category records.
- Managers can schedule and edit product deals with start/end times and a validated price below the regular price. Checkout applies the lowest active scheduled price, calculates tax after the discount, and records which deal priced each sale line.
- Categories, subcategories, and brands have authenticated CRUD APIs, product counts, and deletion safeguards for referenced records.
- Customers support cashier and manager create/update/archive workflows.
- Checkout validates stock and calculates tax on the server. It records cash payment, a generated invoice, and an append-only financial transaction inside the sale database transaction. Card and bank transfer are rejected until a real payment integration is configured.
- Cancelling a completed sale requires a manager role, restores stock, voids its invoice, and records a negative ledger reversal. Cancelled sales are excluded from revenue totals.
- Cashiers can request partial refunds by sold line and quantity. Pending quantities are reserved against duplicate requests; a different manager/admin must approve or reject. Approval atomically recalculates proportional tax, records the negative refund transaction, optionally restores stock, updates the invoice, and marks a fully refunded sale.
- Cashiers can open one register shift at a time, record cash in/out, and close with expected cash and counted-cash difference. Set `POS_REQUIRE_OPEN_SHIFT=True` to require an open shift before checkout; it defaults to `False`.
- Held bills save a cashier's basket and customer for later resumption or deletion. Cashiers see their own held bills; managers/admins can inspect all.
- Store-wide business name, contact details, currency, tax rate, shift-required checkout, and negative-stock policy are stored in the database. Managers/admins can update them; checkout and dashboard reads use the saved tax and stock rules.
- Persisted notifications are created for new sales, stock crossing its reorder threshold, refund requests, and refund decisions. Each user has a private inbox with unread counts and mark-as-read actions.
- Internal staff messaging supports inbox, sent, and archived folders; search; replies; read/unread states; archive and per-user delete; recipient lookup; and private attachment downloads for validated files up to 5 MB.
- Email supports manual sends and reusable receipt, invoice, order-confirmation, and low-stock templates. Delivery attempts are logged; failed sends can be retried, and a missing SMTP configuration is reported explicitly. Set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` or `EMAIL_USE_SSL`, and `DEFAULT_FROM_EMAIL` in the private backend `.env`.
- Invoice lists show paid and outstanding balances. Staff can record a later payment against an open invoice; each payment creates an append-only ledger entry and is attributed to the active register shift. Reports filter sales by date and export the displayed sales as CSV.
- Authenticated users can save their light/dark theme and low-stock/sales notification preferences to their own preference record.
- Staff can update their own name and email. Catalog write and refund review controls are hidden from cashier users in the frontend and enforced by the API.
- The frontend adapts to narrow screens, supports reduced motion, and reports API errors rather than substituting sample figures.
- POS checkout records cash, card, bank transfer, or other tender, supports a cash/card split, validates that tender allocations equal the backend sale total, and calculates cash change. Refunds and cancellations reverse the original tender mix so register cash totals remain accurate. Card capture represents payment accepted on an external terminal; no payment gateway is integrated.
- The authenticated top-bar global search queries products, orders, customers, and invoices from the database and links matches to their relevant pages.
- Product images accept PNG, JPEG, or WebP uploads up to 5 MB. The API checks the file signature and image dimensions, stores uploads under `MEDIA_ROOT`, and returns an absolute media URL. Configure the deployment web server or object storage to serve `MEDIA_URL` in production; Django's built-in media route is enabled only in development.

### User roles

Migration 0005 creates the Django groups admin, manager, and cashier. Migration 0007 assigns existing non-superuser accounts with no group to the least-privileged cashier role; assign managers and admins in Django admin. Superusers bypass group checks. Cashiers can use checkout and customer records; managers and admins can manage the catalog and cancel sales. New users need a group before accessing business endpoints. There are no built-in default credentials.

Role groups do not yet provide per-branch data isolation or configurable fine-grained permissions.

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
- `/api/v1/deals/` — scheduled product deals with manager-controlled CRUD and active schedule filtering
- `POST /api/v1/auth/token/` and `POST /api/v1/auth/token/refresh/` — JWT authentication
- `GET|POST|PATCH /api/v1/products/` — product and inventory records
- `/api/v1/categories/`, `/api/v1/subcategories/`, `/api/v1/brands/` — catalog reference CRUD
- `GET|POST|PATCH /api/v1/customers/` — customer records
- `GET|POST /api/v1/sales/` — sales history and checkout
- `/api/v1/cash-register/` — list/open shifts, read `current/`, post `{id}/movement/` and `{id}/close/`
- `/api/v1/held-bills/` — list/create/delete held bills and read `{id}/resume/`
- `POST /api/v1/sales/{id}/cancel/` — cancel and restock a completed sale
- `GET /api/v1/invoices/`, `POST /api/v1/invoices/{id}/record_payment/`, `GET /api/v1/transactions/` — generated invoices, later payments, and append-only ledger
- `GET|POST /api/v1/refunds/`, `POST /api/v1/refunds/{id}/approve/`, `POST /api/v1/refunds/{id}/reject/` — partial refund requests and manager review
- `GET|PATCH /api/v1/preferences/me/` — per-user appearance and notification preferences
- `GET|PATCH /api/v1/business-settings/` — store-wide settings; cashier reads and manager/admin edits
- `/api/v1/notifications/` — current user's inbox, unread count, mark one read, and mark all read
- `/api/v1/messages/` — staff inbox/sent/archive, compose, reply, archive, delete, unread count, thread, and attachment download
- `/api/v1/emails/` — manual/template send, delivery log, retry, template list, and SMTP test
- `GET|PATCH /api/v1/profile/me/` — current user's basic profile
- `GET /api/v1/dashboard/?days=7|30|90|365` — sales and inventory metrics
- `GET /api/docs/` — API schema and documentation

The app does not yet implement payment gateway authorization/settlement, manual B2B invoice creation, password-change/session management, PDF receipts, or role/branch administration UI. These require additional backend workflows or external integrations; the frontend does not present them as working controls.

This repository does not currently implement purchasing, suppliers, expenses, petty cash, appointments, quotations, payment-gateway settlement, or fiscal receipt integrations. Production deployment also requires environment-specific infrastructure, backups, monitoring, and review of staff group assignments.
