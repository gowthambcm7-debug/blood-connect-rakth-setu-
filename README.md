# RaktaSetu — NSS Blood Donor Network

A project connecting **blood donors / NSS volunteers** with **patients in
emergencies**, based on **blood group compatibility** and **live location
(30 km radius)**.

## How it works (matches your original idea)

1. A donor / NSS volunteer **registers** with their mobile number, blood
   group, platelet count, and location.
2. A patient (or hospital attendant) **posts an emergency request**: blood
   group needed, hospital name, and hospital location.
3. The server automatically finds every **available, blood-compatible
   donor within 30 km** and creates an **alert** for each of them.
4. Donors see the alert on their dashboard (this is the "call/SMS" step —
   simulated in-app here, since real SMS/calls need a paid Twilio account.
   See "Going further" below for how to wire that in).
5. If a donor **accepts**, the patient's screen immediately reveals that
   donor's name and mobile number so they can be contacted directly.

## Project structure

```
blood-connect/
├── app.py              Flask server — all API routes
├── database.py         SQLite table setup + connection helper
├── matching.py         Distance formula (Haversine) + blood-compatibility rules
├── requirements.txt    Python packages needed
├── static/
│   └── index.html      The entire frontend (React, loaded from a CDN — no build step)
└── blood_connect.db    Created automatically the first time you run app.py
```

## Running it

You need **Python 3.8+** installed. Then, from inside this folder:

```bash
pip install -r requirements.txt
python app.py
```

Open your browser at **http://127.0.0.1:5000**

That's it — one server serves both the API and the webpage, so there's no
separate frontend server and no CORS setup needed.

## Trying it out end-to-end

1. Open the app → click **"Register as a donor"**.
2. Fill the form. Pick a **blood group** and a **city** from the dropdown
   (or click "Use my current location"). Submit — you're logged straight in.
3. Open the app in a **second browser tab** (or an incognito window) →
   click **"Raise a request"**.
4. Fill in a **compatible blood group** and a **city within 30 km** of the
   donor you just registered. Submit.
5. Go back to the **donor's tab** — within ~5 seconds (auto-refresh) you'll
   see the alert appear. Click **Accept**.
6. Go back to the **patient's tab** — within ~4 seconds you'll see the
   donor's name and mobile number appear as "Accepted".

## The matching logic (matching.py)

- **Distance**: uses the Haversine formula to calculate real-world
  distance in km between the donor's coordinates and the hospital's
  coordinates (not a flat-map approximation).
- **Blood compatibility**: uses the real medical donor→recipient chart
  (e.g. an `O-` donor is alerted for *any* patient, since O- is the
  universal donor), not just an exact blood-group match.
- **Radius**: `SEARCH_RADIUS_KM = 30` at the top of `matching.py` — change
  this one number to widen or narrow the search.

## Going further (ideas for your report / viva)

- **Real SMS/call alerts**: swap the comment inside
  `create_patient_request()` in `app.py` for a call to the
  [Twilio API](https://www.twilio.com/docs/sms/quickstart/python) (free
  trial account) to actually text or call the donor's mobile number.
- **Real geocoding**: replace the preset city dropdown with the free
  [OpenStreetMap Nominatim API](https://nominatim.org/) so users can type
  any address and get real coordinates.
- **Auth**: passwords are already hashed with Werkzeug's
  `generate_password_hash` — for a production app you'd add sessions/JWT
  tokens instead of returning the donor row directly on login.
- **NSS admin view**: add a route that lists all NSS-volunteer donors and
  lets a college NSS coordinator broadcast a bulk donation-camp request.
