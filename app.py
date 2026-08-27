"""
app.py
------
The web server. Every URL the browser can call lives here as a
"route". Each route does three things, in order:
  1. Read whatever the browser sent (JSON body, or a URL piece like <id>)
  2. Talk to the database / matching.py to do the actual work
  3. Send a JSON answer back

Run it with:   python app.py
Then open:     http://127.0.0.1:5000  in your browser.
"""

from flask import Flask, request, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import os

from database import init_db, get_connection
from matching import find_matching_donors, SEARCH_RADIUS_KM

app = Flask(__name__, static_folder="static")

# Build the database (creates the .db file + tables the first time this runs)
init_db()


# ============================================================================
# FRONTEND - just serve the single index.html page for the browser
# ============================================================================
@app.route("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


# ============================================================================
# DONOR / NSS VOLUNTEER ROUTES
# ============================================================================

@app.route("/api/donor/register", methods=["POST"])
def donor_register():
    """
    A new donor (or NSS volunteer) signs up.
    Expects JSON body with: name, mobile, email, password, blood_group,
    platelet_count (optional), is_nss_volunteer (true/false),
    location_name, latitude, longitude
    """
    data = request.get_json()

    # ---- Basic validation: reject the request early if something required is missing ----
    required = ["name", "mobile", "password", "blood_group",
                "location_name", "latitude", "longitude"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO donors
                (name, mobile, email, password_hash, blood_group, platelet_count,
                 is_nss_volunteer, location_name, latitude, longitude)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["name"],
            data["mobile"],
            data.get("email", ""),
            generate_password_hash(data["password"]),   # never store plain-text passwords
            data["blood_group"],
            data.get("platelet_count") or None,
            1 if data.get("is_nss_volunteer") else 0,
            data["location_name"],
            data["latitude"],
            data["longitude"],
        ))
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        return jsonify({"message": "Registered successfully", "donor_id": new_id}), 201

    except Exception as e:
        # Most likely cause: that mobile number is already registered (UNIQUE constraint)
        return jsonify({"error": "This mobile number is already registered.", "detail": str(e)}), 409
    finally:
        conn.close()


@app.route("/api/donor/login", methods=["POST"])
def donor_login():
    """
    Simple login: match mobile number, then check the password hash.
    Expects JSON body with: mobile, password
    """
    data = request.get_json()
    conn = get_connection()
    donor = conn.execute("SELECT * FROM donors WHERE mobile = ?", (data.get("mobile"),)).fetchone()
    conn.close()

    if donor is None or not check_password_hash(donor["password_hash"], data.get("password", "")):
        return jsonify({"error": "Invalid mobile number or password"}), 401

    return jsonify(dict(donor))


@app.route("/api/donor/<int:donor_id>", methods=["GET"])
def donor_profile(donor_id):
    conn = get_connection()
    donor = conn.execute("SELECT * FROM donors WHERE id = ?", (donor_id,)).fetchone()
    conn.close()
    if donor is None:
        return jsonify({"error": "Donor not found"}), 404
    return jsonify(dict(donor))


@app.route("/api/donor/<int:donor_id>/availability", methods=["PUT"])
def set_availability(donor_id):
    """Lets a donor pause/resume being alerted (e.g. right after donating)."""
    data = request.get_json()
    conn = get_connection()
    conn.execute("UPDATE donors SET available = ? WHERE id = ?",
                 (1 if data.get("available") else 0, donor_id))
    conn.commit()
    conn.close()
    return jsonify({"message": "Availability updated"})


@app.route("/api/donor/<int:donor_id>/alerts", methods=["GET"])
def donor_alerts(donor_id):
    """
    Every alert (past + present) sent to this donor, newest first,
    joined with the patient/hospital details so the donor's screen
    has everything it needs in one call.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT alerts.id, alerts.status, alerts.distance_km, alerts.created_at,
               patient_requests.patient_name, patient_requests.blood_group_needed,
               patient_requests.hospital_name, patient_requests.location_name,
               patient_requests.urgency, patient_requests.contact_mobile
        FROM alerts
        JOIN patient_requests ON patient_requests.id = alerts.patient_id
        WHERE alerts.donor_id = ?
        ORDER BY alerts.created_at DESC
    """, (donor_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ============================================================================
# PATIENT REQUEST ROUTES
# ============================================================================

@app.route("/api/patient/request", methods=["POST"])
def create_patient_request():
    """
    A patient / hospital / attendant posts an emergency blood requirement.
    This is the step that TRIGGERS the matching + alert-sending.
    """
    data = request.get_json()

    required = ["patient_name", "blood_group_needed", "hospital_name",
                "location_name", "latitude", "longitude", "contact_mobile"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    conn = get_connection()

    # 1) Save the request itself
    conn.execute("""
        INSERT INTO patient_requests
            (patient_name, blood_group_needed, units_needed, hospital_name,
             location_name, latitude, longitude, urgency, contact_mobile)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["patient_name"], data["blood_group_needed"], data.get("units_needed", 1),
        data["hospital_name"], data["location_name"], data["latitude"], data["longitude"],
        data.get("urgency", "High"), data["contact_mobile"],
    ))
    conn.commit()
    patient_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    patient_request = conn.execute("SELECT * FROM patient_requests WHERE id = ?", (patient_id,)).fetchone()

    # 2) Run the matching algorithm against every donor in the database
    all_donors = conn.execute("SELECT * FROM donors").fetchall()
    matches = find_matching_donors(patient_request, all_donors)

    # 3) Create one "alert" row per matched donor.
    #    (This is our SIMULATED call/SMS: in a real deployment, this loop
    #     is exactly where you'd call the Twilio API to actually ring/text
    #     the donor's mobile number.)
    for m in matches:
        conn.execute("""
            INSERT INTO alerts (patient_id, donor_id, distance_km, status)
            VALUES (?, ?, ?, 'pending')
        """, (patient_id, m["donor"]["id"], m["distance_km"]))
    conn.commit()
    conn.close()

    return jsonify({
        "message": f"Request posted. {len(matches)} matching donor(s) alerted within {SEARCH_RADIUS_KM} km.",
        "patient_id": patient_id,
        "alerted_count": len(matches),
    }), 201


@app.route("/api/patient/<int:patient_id>/alerts", methods=["GET"])
def patient_alert_status(patient_id):
    """
    Lets the patient's screen watch, in near-real-time, whether any
    alerted donor has accepted. Donor's identity/contact is only
    revealed once they've ACCEPTED (privacy: no point exposing a
    donor's phone number to every patient who merely matched).
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT alerts.id, alerts.status, alerts.distance_km,
               donors.name AS donor_name, donors.mobile AS donor_mobile,
               donors.blood_group, donors.is_nss_volunteer
        FROM alerts
        JOIN donors ON donors.id = alerts.donor_id
        WHERE alerts.patient_id = ?
        ORDER BY alerts.distance_km ASC
    """, (patient_id,)).fetchall()
    conn.close()

    result = []
    for r in rows:
        row = dict(r)
        if row["status"] != "accepted":
            # Hide personal contact info until the donor actually accepts
            row["donor_name"] = "Donor #" + str(row["id"])
            row["donor_mobile"] = None
        result.append(row)
    return jsonify(result)


# ============================================================================
# ALERT RESPONSE ROUTE (donor taps Accept / Decline)
# ============================================================================

@app.route("/api/alert/<int:alert_id>/respond", methods=["POST"])
def respond_to_alert(alert_id):
    data = request.get_json()
    response = data.get("response")  # expected: "accepted" or "declined"

    if response not in ("accepted", "declined"):
        return jsonify({"error": "response must be 'accepted' or 'declined'"}), 400

    conn = get_connection()
    conn.execute("""
        UPDATE alerts SET status = ?, responded_at = datetime('now') WHERE id = ?
    """, (response, alert_id))

    # If a donor accepted, mark the patient's request as fulfilled so
    # we stop treating it as an open emergency (other donors already
    # alerted can still see/accept it too, in case more units are needed).
    if response == "accepted":
        alert = conn.execute("SELECT patient_id FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        conn.execute("UPDATE patient_requests SET status = 'fulfilled' WHERE id = ?",
                     (alert["patient_id"],))

    conn.commit()
    conn.close()
    return jsonify({"message": f"Alert {response}"})


if __name__ == "__main__":
    import os
    # Hosting platforms (like Render) tell the app which port to listen on
    # via the PORT environment variable. Locally, it just falls back to 5000.
    port = int(os.environ.get("PORT", 5000))
    # host="0.0.0.0" means "accept connections from anywhere", not just this computer -
    # required for a hosting platform to be able to reach the app.
    app.run(host="0.0.0.0", port=port, debug=False)
