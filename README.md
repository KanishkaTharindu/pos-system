# POS System

A local development starter for a POS dashboard using Django REST Framework,
PostgreSQL, React, Vite, TypeScript, and Tailwind CSS.

## Requirements

- Python 3.12
- Node.js and npm
- PostgreSQL 17 (the configured local service uses port `5432`)

## Start PostgreSQL and configure Django

The backend reads database settings from `backend/.env`; it does not use
SQLite. Create the environment file from the example if it does not exist:

```powershell
cd C:\git\pos-system\backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Set `DB_NAME`, `DB_USER`, and `DB_PASSWORD` in `backend/.env` to match the
PostgreSQL database role and database you have created. Keep that file private;
it is excluded from Git. Passwords are read as discrete settings, so punctuation
does not need URL encoding.

Install and start the backend:

```powershell
cd C:\git\pos-system\backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py check
python manage.py check_db
python manage.py migrate --plan
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

`check_db` executes a real PostgreSQL query and prints the connected database,
role, server port, and PostgreSQL version without printing credentials. The
expected local configuration is database `pos_db`, role `pos_user`, host
`127.0.0.1`, port `5432`.

## Start the frontend

In another PowerShell terminal:

```powershell
cd C:\git\pos-system\frontend
npm install
npm run dev
```

Open the Vite URL shown in the terminal (normally `http://localhost:5173`).
The UI uses `http://127.0.0.1:8000/api/v1` by default. To change the API URL,
set `VITE_API_BASE_URL` in `frontend/.env`.

## Health and API endpoints

- `GET /api/v1/health/live/` — backend liveness
- `GET /api/v1/health/ready/` — database-backed readiness
- `GET /api/v1/dashboard/` — live dashboard totals
- `GET|POST /api/v1/products/` — products
- `GET|POST /api/v1/customers/` — customers
- `GET|POST /api/v1/sales/` — list and create sales
- `GET /api/docs/` — interactive API documentation

Sales are recorded transactionally: inventory is locked and checked, then
updated with the sale and its line items. An unsuccessful sale does not leave a
partial invoice or consume stock.

## Build the frontend

```powershell
cd C:\git\pos-system\frontend
npm run build
```

This is a development starter, not a production-ready deployment. Authentication
and role-based access control still need to be implemented before exposing the
API to a network or using it for real sales.