import os
from datetime import timedelta

os.environ["DATABASE_URL"] = "sqlite:///data/test_rakthsetu.db"
os.environ["SECRET_KEY"] = "pytest-secret-only"
os.environ["APP_ENV"] = "test"
os.environ["DISABLE_BACKGROUND_SCHEDULER"] = "1"

from sqlalchemy import delete, select
from app import app, process_escalations
from database import Account, Alert, DonorProfile, Notification, PatientRequest, SessionLocal, utcnow

def clear_database():
    with SessionLocal() as db:
        for model in (Notification, Alert, PatientRequest, DonorProfile, Account):
            db.execute(delete(model))
        db.commit()

def register(client, role, email, **extra):
    payload = {"name": email.split("@")[0], "email": email, "mobile": "5550000000",
               "password": "testing-pass-123", "role": role, **extra}
    return client.post("/api/register", json=payload)

def test_email_login_authorization_and_25_min_escalation():
    clear_database()
    app.config["TESTING"] = True
    donor = app.test_client()
    patient = app.test_client()
    d = register(donor, "donor", "donor@example.test", blood_group="O-",
                 location_name="Warangal", latitude=17.9689, longitude=79.5941)
    assert d.status_code == 201
    assert "password_hash" not in d.get_json()["account"]
    login = app.test_client().post("/api/login", json={"email":"donor@example.test",
                                                        "password":"testing-pass-123"})
    assert login.status_code == 200
    assert register(patient, "patient", "patient@example.test").status_code == 201
    created = patient.post("/api/requests", json={"patient_name":"Patient",
        "blood_group_needed":"A+", "units_needed":1, "hospital_name":"City Hospital",
        "contact_mobile":"5551234567", "urgency":"High", "location_name":"Warangal",
        "latitude":17.9690, "longitude":79.5942})
    assert created.status_code == 201
    request_id = created.get_json()["request_id"]
    patient_alerts = patient.get(f"/api/requests/{request_id}/alerts").get_json()
    assert len(patient_alerts["alerts"]) == 1
    assert patient_alerts["alerts"][0]["donor_mobile"] is None
    alerts = donor.get("/api/donor/alerts").get_json()
    assert len(alerts) == 1
    alert_id = alerts[0]["id"]
    assert donor.post(f"/api/alerts/{alert_id}/respond", json={"action":"accept"}).status_code == 200
    with SessionLocal() as db:
        alert = db.get(Alert, alert_id)
        alert.accepted_at = utcnow() - timedelta(minutes=26)
        second = Account(name="Second", email="second@example.test", mobile="5550000001",
                         role="nss_volunteer", password_hash="unused")
        second.donor_profile = DonorProfile(blood_group="O-", is_nss_volunteer=True,
                         location_name="Nearby", latitude=17.9700, longitude=79.5941)
        db.add(second)
        db.commit()
    assert process_escalations() == 1
    with SessionLocal() as db:
        statuses = db.scalars(select(Alert).where(Alert.patient_id == request_id)).all()
        assert sorted(a.status for a in statuses) == ["expired", "pending"]
        second_account = db.scalar(select(Account).where(Account.email == "second@example.test"))
        assert db.scalar(select(Notification.id).where(Notification.account_id == second_account.id).limit(1)) is not None

def test_roles_and_private_routes_are_enforced():
    clear_database()
    app.config["TESTING"] = True
    client = app.test_client()
    assert client.get("/api/donor/alerts").status_code == 401
    assert register(client, "blood_bank", "bank@example.test").status_code == 201
    assert client.get("/api/donor/alerts").status_code == 403
    response = client.post("/api/requests", json={"patient_name":"Test",
        "blood_group_needed":"O+", "hospital_name":"Hospital", "contact_mobile":"123",
        "location_name":"Site", "latitude":1000, "longitude":0})
    assert response.status_code == 400

def test_request_with_no_initial_match_notifies_when_donor_registers_later():
    clear_database()
    app.config["TESTING"] = True
    patient = app.test_client()
    assert register(patient, "patient", "waiting@example.test").status_code == 201
    created = patient.post("/api/requests", json={
        "patient_name": "Waiting Patient", "blood_group_needed": "A+",
        "hospital_name": "City Hospital", "contact_mobile": "5551112222",
        "location_name": "Warangal", "latitude": 17.9689, "longitude": 79.5941,
    })
    assert created.status_code == 201
    assert created.get_json()["initial_alert_count"] == 0
    donor = app.test_client()
    assert register(donor, "donor", "later@example.test", blood_group="O-",
                   location_name="Nearby", latitude=17.9700,
                   longitude=79.5941).status_code == 201
    assert process_escalations() == 1
    assert len(donor.get("/api/donor/alerts").get_json()) == 1
