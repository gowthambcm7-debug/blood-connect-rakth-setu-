"""RaktaSetu API — email/password accounts, SPO geographic matching and timed escalation."""
from datetime import datetime, timedelta
from functools import wraps
import atexit
import logging
import math
import os
import re

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory, session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from database import (Account, Alert, DonorProfile, Notification, PatientRequest,
                     SessionLocal, init_db, utcnow)
from matching import (ESCALATION_BATCH_SIZE, INITIAL_ALERT_BATCH, SEARCH_RADIUS_KM,
                     find_matching_donors)
from notifications import create_notification

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
load_dotenv()
app = Flask(__name__, static_folder="static")
secret = os.environ.get("SECRET_KEY")
if not secret and os.environ.get("APP_ENV") == "production":
    raise RuntimeError("SECRET_KEY must be set for production.")
app.secret_key = secret or "local-development-only-change-me"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("APP_ENV") == "production",
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)
init_db()

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ROLES = {"donor", "nss_volunteer", "blood_bank", "patient"}
BLOOD_GROUPS = {"O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"}


def api_error(message, status=400):
    return jsonify({"error": message}), status


def current_account(db):
    account_id = session.get("account_id")
    return db.get(Account, account_id) if account_id else None


def login_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            with SessionLocal() as db:
                account = current_account(db)
                if account is None:
                    return api_error("Please sign in to continue.", 401)
                if roles and account.role not in roles:
                    return api_error("This account does not have access to that action.", 403)
                g.account_id = account.id
                g.account_role = account.role
            return view(*args, **kwargs)
        return wrapped
    return decorator


def account_json(account):
    result = {"id": account.id, "name": account.name, "email": account.email,
              "mobile": account.mobile, "role": account.role}
    if account.donor_profile:
        p = account.donor_profile
        result["donor"] = {"id": p.id, "blood_group": p.blood_group,
                            "platelet_count": p.platelet_count,
                            "is_nss_volunteer": p.is_nss_volunteer,
                            "location_name": p.location_name,
                            "latitude": p.latitude, "longitude": p.longitude,
                            "available": p.available}
    return result


def valid_location(data):
    try:
        lat, lon = float(data["latitude"]), float(data["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    name = str(data.get("location_name", "")).strip()
    if not name or len(name) > 180:
        return None
    return name, lat, lon


def donor_matches(db, patient_request, excluded=None):
    profiles = db.scalars(select(DonorProfile).where(DonorProfile.available.is_(True))).all()
    return find_matching_donors(patient_request, profiles, exclude_ids=excluded)


def notify_donor(db, alert, title, body, kind="blood_request"):
    account = db.get(Account, alert.donor.account_id)
    if account:
        create_notification(db, account, title, body, kind=kind,
                            patient_id=alert.patient_id, alert_id=alert.id)


def notify_requester(db, patient_request, title, body, kind="request_update", alert_id=None):
    account = db.get(Account, patient_request.requester_id)
    if account:
        create_notification(db, account, title, body, kind=kind,
                            patient_id=patient_request.id, alert_id=alert_id)


def dispatch_next_wave(db, patient_request, matches):
    created = []
    for match in matches[:ESCALATION_BATCH_SIZE]:
        alert = Alert(patient_id=patient_request.id, donor_id=match["donor"].id,
                      distance_km=match["distance_km"], status="pending")
        db.add(alert)
        db.flush()
        created.append(alert)
        notify_donor(db, alert,
                     f"Urgent {patient_request.blood_group_needed} blood request nearby",
                     f"{patient_request.patient_name} needs {patient_request.blood_group_needed} blood at {patient_request.hospital_name} ({match['distance_km']} km away). Sign in to respond.")
    return created


def process_escalations(now=None):
    """Send the next donor wave 25 minutes after the latest unconfirmed alert."""
    now = now or utcnow()
    escalated = 0
    with SessionLocal() as db:
        requests = db.scalars(select(PatientRequest).where(PatientRequest.status == "searching")).all()
        for patient_request in requests:
            alerts = db.scalars(select(Alert).where(Alert.patient_id == patient_request.id)).all()
            active = [a for a in alerts if a.status in ("pending", "accepted") and a.confirmed_at is None]
            if not active:
                # Keep searching when no responders remain or no match existed at request time.
                excluded = {a.donor_id for a in alerts}
                new_alerts = dispatch_next_wave(
                    db, patient_request,
                    donor_matches(db, patient_request, excluded)[:INITIAL_ALERT_BATCH],
                )
                if new_alerts:
                    notify_requester(
                        db, patient_request, "A nearby donor is now available",
                        "A newly available compatible donor has been notified about your request.",
                        kind="escalation",
                    )
                    escalated += len(new_alerts)
                continue
            anchor = max((a.accepted_at or a.created_at) for a in active)
            if now - anchor < timedelta(minutes=25):
                continue
            already_alerted = {a.donor_id for a in alerts}
            matches = donor_matches(db, patient_request, already_alerted)
            for alert in active:
                alert.status = "expired"
            next_alerts = dispatch_next_wave(db, patient_request, matches)
            notify_requester(db, patient_request, "Still searching for a donor",
                             f"No donor confirmed within 25 minutes. RaktaSetu has alerted the next {len(next_alerts)} eligible donor(s) within {SEARCH_RADIUS_KM} km.", kind="escalation")
            if next_alerts:
                escalated += len(next_alerts)
            else:
                logger.info("No unalerted matching donors remain for request %s", patient_request.id)
        db.commit()
    return escalated


@app.get("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "service": "RaktaSetu", "search_radius_km": SEARCH_RADIUS_KM})


@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    mobile = str(data.get("mobile", "")).strip()
    password = str(data.get("password", ""))
    role = str(data.get("role", ""))
    if not name or len(name) > 120 or not EMAIL_RE.fullmatch(email) or len(email) > 254:
        return api_error("Enter your name and a valid email address.")
    if len(password) < 8 or len(password) > 256:
        return api_error("Password must be at least 8 characters.")
    if not mobile or len(mobile) > 30 or role not in ROLES:
        return api_error("Enter a mobile number and choose a valid account type.")
    profile_data = None
    if role in ("donor", "nss_volunteer"):
        location = valid_location(data)
        group = data.get("blood_group")
        if not location or group not in BLOOD_GROUPS:
            return api_error("Donor/NSS accounts need a valid blood group and location.")
        profile_data = location, group
    with SessionLocal() as db:
        account = Account(name=name, email=email, mobile=mobile,
                          password_hash=generate_password_hash(password), role=role)
        if profile_data:
            location, group = profile_data
            platelet = data.get("platelet_count")
            try:
                platelet = int(platelet) if platelet not in (None, "") else None
                if platelet is not None and platelet < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return api_error("Platelet count must be a non-negative whole number.")
            account.donor_profile = DonorProfile(
                blood_group=group, platelet_count=platelet,
                is_nss_volunteer=(role == "nss_volunteer"),
                location_name=location[0], latitude=location[1], longitude=location[2], available=True)
        db.add(account)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return api_error("An account with this email already exists.", 409)
        session.clear()
        session["account_id"] = account.id
        return jsonify({"message": "Account created.", "account": account_json(account)}), 201


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.email == email))
        if account is None or not check_password_hash(account.password_hash, password):
            return api_error("Invalid email or password.", 401)
        session.clear()
        session["account_id"] = account.id
        return jsonify({"account": account_json(account)})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"message": "Signed out."})


@app.get("/api/me")
@login_required()
def me():
    with SessionLocal() as db:
        account = db.get(Account, g.account_id)
        return jsonify({"account": account_json(account)})


@app.get("/api/notifications")
@login_required()
def get_notifications():
    with SessionLocal() as db:
        rows = db.scalars(select(Notification).where(Notification.account_id == g.account_id)
                          .order_by(Notification.created_at.desc()).limit(50)).all()
        unread = db.scalar(select(Notification.id).where(Notification.account_id == g.account_id,
                                                        Notification.is_read.is_(False)).limit(1))
        return jsonify({"items": [{"id": n.id, "title": n.title, "body": n.body,
                                    "kind": n.kind, "is_read": n.is_read,
                                    "email_status": n.email_status,
                                    "created_at": n.created_at.isoformat() + "Z"}
                                   for n in rows], "has_unread": unread is not None})


@app.post("/api/notifications/<int:notification_id>/read")
@login_required()
def read_notification(notification_id):
    with SessionLocal() as db:
        item = db.get(Notification, notification_id)
        if item is None or item.account_id != g.account_id:
            return api_error("Notification not found.", 404)
        item.is_read = True
        db.commit()
    return jsonify({"message": "Marked as read."})


@app.put("/api/donor/availability")
@login_required("donor", "nss_volunteer")
def update_availability():
    data = request.get_json(silent=True) or {}
    with SessionLocal() as db:
        account = db.get(Account, g.account_id)
        account.donor_profile.available = bool(data.get("available"))
        db.commit()
        return jsonify({"available": account.donor_profile.available})


def serialize_request_alert(alert, expose_contact=True):
    p = alert.patient_request
    d = alert.donor
    return {"id": alert.id, "status": alert.status, "distance_km": alert.distance_km,
            "created_at": alert.created_at.isoformat() + "Z",
            "patient_name": p.patient_name, "blood_group_needed": p.blood_group_needed,
            "hospital_name": p.hospital_name, "location_name": p.location_name,
            "urgency": p.urgency, "contact_mobile": p.contact_mobile if expose_contact else None,
            "donor_name": d.account.name, "donor_mobile": d.account.mobile,
            "blood_group": d.blood_group, "is_nss_volunteer": d.is_nss_volunteer,
            "confirmed_at": alert.confirmed_at.isoformat() + "Z" if alert.confirmed_at else None}


@app.get("/api/donor/alerts")
@login_required("donor", "nss_volunteer")
def donor_alerts():
    with SessionLocal() as db:
        account = db.get(Account, g.account_id)
        profile = account.donor_profile
        rows = db.scalars(select(Alert).where(Alert.donor_id == profile.id)
                          .order_by(Alert.created_at.desc()).limit(100)).all()
        return jsonify([serialize_request_alert(alert) for alert in rows])


@app.post("/api/requests")
@login_required("patient", "blood_bank")
def create_request():
    data = request.get_json(silent=True) or {}
    required = ["patient_name", "blood_group_needed", "hospital_name", "contact_mobile"]
    if any(not str(data.get(key, "")).strip() for key in required):
        return api_error("Complete the patient, blood group, hospital and contact fields.")
    location = valid_location(data)
    if not location or data.get("blood_group_needed") not in BLOOD_GROUPS:
        return api_error("Enter a valid hospital location and blood group.")
    try:
        units = int(data.get("units_needed", 1))
        if units < 1 or units > 100:
            raise ValueError
    except (TypeError, ValueError):
        return api_error("Units must be a whole number from 1 to 100.")
    urgency = data.get("urgency", "High")
    if urgency not in ("Critical", "High", "Normal"):
        return api_error("Choose a valid urgency.")
    with SessionLocal() as db:
        account = db.get(Account, g.account_id)
        patient_request = PatientRequest(
            requester_id=account.id, patient_name=str(data["patient_name"]).strip()[:120],
            blood_group_needed=data["blood_group_needed"], units_needed=units,
            hospital_name=str(data["hospital_name"]).strip()[:180],
            location_name=location[0], latitude=location[1], longitude=location[2],
            urgency=urgency, contact_mobile=str(data["contact_mobile"]).strip()[:30], status="searching")
        db.add(patient_request)
        db.flush()
        all_matches = donor_matches(db, patient_request)
        first_wave = dispatch_next_wave(db, patient_request, all_matches[:INITIAL_ALERT_BATCH])
        notify_requester(db, patient_request, "Blood request is active",
                         f"Your request is searching for compatible donors within {SEARCH_RADIUS_KM} km. The nearest match has been notified.")
        db.commit()
        return jsonify({"message": "Request posted.", "request_id": patient_request.id,
                        "initial_alert_count": len(first_wave), "eligible_donor_count": len(all_matches),
                        "search_radius_km": SEARCH_RADIUS_KM}), 201


@app.get("/api/my/requests")
@login_required("patient", "blood_bank")
def my_requests():
    with SessionLocal() as db:
        rows = db.scalars(select(PatientRequest).where(PatientRequest.requester_id == g.account_id)
                          .order_by(PatientRequest.created_at.desc()).limit(50)).all()
        return jsonify([{"id": p.id, "patient_name": p.patient_name,
                         "blood_group_needed": p.blood_group_needed, "hospital_name": p.hospital_name,
                         "status": p.status, "created_at": p.created_at.isoformat() + "Z"}
                        for p in rows])


@app.get("/api/requests/<int:request_id>/alerts")
@login_required("patient", "blood_bank")
def request_alerts(request_id):
    with SessionLocal() as db:
        patient_request = db.get(PatientRequest, request_id)
        if patient_request is None or patient_request.requester_id != g.account_id:
            return api_error("Request not found.", 404)
        rows = db.scalars(select(Alert).where(Alert.patient_id == request_id)
                          .order_by(Alert.distance_km.asc())).all()
        alerts = []
        for alert in rows:
            row = serialize_request_alert(alert, expose_contact=False)
            if alert.status not in ("accepted", "confirmed"):
                row["donor_name"] = "Donor"
                row["donor_mobile"] = None
            alerts.append(row)
        return jsonify({"request": {"id": patient_request.id, "status": patient_request.status,
                                     "patient_name": patient_request.patient_name,
                                     "blood_group_needed": patient_request.blood_group_needed,
                                     "units_needed": patient_request.units_needed,
                                     "hospital_name": patient_request.hospital_name},
                        "alerts": alerts})


@app.post("/api/alerts/<int:alert_id>/respond")
@login_required("donor", "nss_volunteer")
def respond_to_alert(alert_id):
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("accept", "decline", "confirm"):
        return api_error("Action must be accept, decline, or confirm.")
    with SessionLocal() as db:
        account = db.get(Account, g.account_id)
        alert = db.get(Alert, alert_id)
        if alert is None or alert.donor_id != account.donor_profile.id:
            return api_error("Alert not found.", 404)
        patient_request = db.get(PatientRequest, alert.patient_id)
        if patient_request.status != "searching":
            return api_error("This request is no longer active.", 409)
        if action == "accept" and alert.status == "pending":
            alert.status = "accepted"
            alert.accepted_at = utcnow()
            account.donor_profile.available = False
            notify_requester(db, patient_request, "A donor accepted your request",
                             f"A compatible donor accepted the {patient_request.blood_group_needed} request. They need to confirm they are on the way within 25 minutes.",
                             kind="accepted", alert_id=alert.id)
        elif action == "decline" and alert.status == "pending":
            alert.status = "declined"
            # A decline releases the next closest donor immediately if there is no other active response.
            active = db.scalar(select(Alert.id).where(Alert.patient_id == patient_request.id,
                          Alert.status.in_(["pending", "accepted"]), Alert.id != alert.id).limit(1))
            if active is None:
                prior = db.scalars(select(Alert).where(Alert.patient_id == patient_request.id)).all()
                excluded = {item.donor_id for item in prior}
                next_alerts = dispatch_next_wave(db, patient_request,
                                                  donor_matches(db, patient_request, excluded)[:1])
                if next_alerts:
                    notify_requester(db, patient_request, "Finding the next donor",
                                     "A donor declined; the next eligible nearby donor has been notified.", kind="escalation")
        elif action == "confirm" and alert.status == "accepted" and alert.confirmed_at is None:
            alert.status = "confirmed"
            alert.confirmed_at = utcnow()
            patient_request.status = "fulfilled"
            notify_requester(db, patient_request, "Donor confirmed they are on the way",
                             f"A donor confirmed the request. Please contact them using the details in your request dashboard.",
                             kind="confirmed", alert_id=alert.id)
        else:
            return api_error("That response is not valid for this alert's current status.", 409)
        db.commit()
        return jsonify({"message": f"Response recorded: {action}.", "status": alert.status})


def _run_scheduler():
    if os.environ.get("DISABLE_BACKGROUND_SCHEDULER") == "1":
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
        scheduler.add_job(process_escalations, "interval", seconds=30,
                          id="blood-request-escalation", max_instances=1,
                          coalesce=True, misfire_grace_time=60)
        scheduler.start()
        atexit.register(lambda: scheduler.shutdown(wait=False) if scheduler.running else None)
        return scheduler
    except Exception:
        logger.exception("Could not start escalation scheduler")
        return None


scheduler = _run_scheduler()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
