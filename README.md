# RaktaSetu — Blood Donor Network

A Flask application connecting patients and blood banks with nearby, blood-compatible donors and NSS volunteers. SPO (Smart Proximity Organizer) is a transparent geographic matching engine: it checks blood-group compatibility, filters to a 20 km radius, then ranks donors nearest-first. It is not a generative-AI medical decision system.

## What is implemented

- Email + password registration and sign-in for donors, NSS volunteers, patients/attendants and blood banks. Passwords are hashed; authenticated endpoints use signed, HttpOnly session cookies.
- Donor/NSS profiles with blood group, availability, contact details and coordinates.
- Patient and blood-bank requests; donor details remain hidden from the requester until a donor accepts.
- SPO matching uses great-circle distance (Haversine) and a fixed 20 km radius. Every compatible, available donor within 20 km is notified immediately; matches are ranked nearest-first for display.
- Every eligible donor within 20 km receives the initial alert. If an accepting donor does not confirm they are on the way within 25 minutes, that acceptance expires, remaining pending donors receive an escalation notification, and newly eligible donors are alerted. A decline triggers an immediate search for newly eligible donors.
- In-app notifications persist in the account notification bar. Real email delivery is available with SendGrid environment variables.
- SQLite for local development; PostgreSQL via DATABASE_URL for deployment. APScheduler checks escalation deadlines every 30 seconds. The included Procfile runs a single web worker so the scheduler is not duplicated.

## Run in VS Code

Install Python 3.10+ and open this folder in VS Code. In its integrated terminal:

    python -m venv .venv
    # Windows PowerShell: .venv\Scripts\Activate.ps1
    # macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt

Copy .env.example to .env and set a long random SECRET_KEY. Local mode uses SQLite automatically. Then run:

    python app.py

Open http://127.0.0.1:5000. For a second terminal/browser profile, open an incognito window to test donor and requester accounts at the same time.

## Email alerts

Set SENDGRID_API_KEY and a verified FROM_EMAIL in the hosting provider's secret/environment-variable panel. Never commit credentials or paste them into chat. Without both variables, in-app notifications still work and each notification records email_status: not_configured.

## Deploy publicly

1. Push this source to your GitHub repository (the deployment must have access to it).
2. Create a PostgreSQL database with your hosting provider and copy its private/internal connection URL into DATABASE_URL.
3. Create a web service from the repository. The included render.yaml supplies Python build/start commands, health check and environment-variable prompts; choose a PostgreSQL plan with persistence suitable for your event.
4. Set APP_ENV=production, set a private SECRET_KEY, and configure SENDGRID_API_KEY plus verified FROM_EMAIL for real email. Do not use SQLite on an ephemeral host filesystem for public deployment.
5. Use an always-on web instance or a separate scheduled worker for the 25-minute timer. Free hosts that sleep while idle can delay escalation until the service wakes up.
6. After deployment, check /healthz, create test donor and patient/bank accounts, and verify the email provider's delivery logs. A public URL is not created by this repository alone; deploy it in your hosting account.

## Test

    pytest -q

The background escalation service must run in exactly one process/instance in this deployment model. The supplied Procfile uses one Gunicorn worker. For a multi-instance production system, move timed escalations to a dedicated worker/queue or a scheduled job with database locking.

## Important real-world limits

This is a deployable hackathon pilot, not a validated clinical or blood-bank inventory system. Accounts are self-registered (including the blood-bank role), email identity is not verified in-app, and donor eligibility/inventory is not clinically validated. Before real emergency reliance, add organization verification, email verification and account recovery, abuse/rate limiting, consent/privacy retention controls, monitoring/backups, and review the blood compatibility workflow with a qualified blood-bank professional. A hospital/clinician must verify compatibility and eligibility before transfusion. The interface recommends contacting the hospital/emergency services.

## Project layout

- app.py — API routes, auth, request flow and 25-minute escalation scheduler
- database.py — SQLAlchemy models and SQLite/PostgreSQL setup
- matching.py — SPO compatibility and geographic ranking
- notifications.py — persistent app notices and optional SendGrid email
- static/index.html — responsive browser UI
- tests/ — matching and authenticated end-to-end tests
