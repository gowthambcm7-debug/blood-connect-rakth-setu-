"""
database.py
-----------
Everything related to talking to our SQLite database lives here.
Keeping it in its own file (instead of mixing it into app.py) means
app.py only has to worry about "what happens when a URL is visited",
and this file only has to worry about "how do I read/write the data".

SQLite is used because it needs zero setup (no separate database
server to install) - perfect for a college project / demo.
"""

import sqlite3          # Python's built-in library for talking to SQLite files
import os                # used to build a file path that works on any OS

# The .db file will sit right next to this script.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blood_connect.db")


def get_connection():
    """
    Opens a connection to the database file.
    row_factory = sqlite3.Row lets us access columns by NAME
    (e.g. row["blood_group"]) instead of only by position (row[3]),
    which makes the rest of the code much easier to read.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # enforce donor_id/patient_id links being valid
    return conn


def init_db():
    """
    Creates the three tables we need, only if they don't already exist.
    Called once when the Flask app starts up.
    """
    conn = get_connection()
    cur = conn.cursor()

    # ---- DONORS (this also covers NSS volunteers who register as donors) ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS donors (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            mobile          TEXT NOT NULL UNIQUE,   -- used as the login username
            email           TEXT,
            password_hash   TEXT NOT NULL,
            blood_group     TEXT NOT NULL,           -- e.g. 'O+', 'AB-'
            platelet_count  INTEGER,                 -- optional, in lakhs/cumm, nullable
            is_nss_volunteer INTEGER NOT NULL DEFAULT 0,  -- 1 = yes, 0 = no
            location_name   TEXT NOT NULL,
            latitude        REAL NOT NULL,
            longitude       REAL NOT NULL,
            available       INTEGER NOT NULL DEFAULT 1,   -- donor can pause themselves
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ---- PATIENT BLOOD REQUESTS ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS patient_requests (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name        TEXT NOT NULL,
            blood_group_needed  TEXT NOT NULL,
            units_needed        INTEGER NOT NULL DEFAULT 1,
            hospital_name       TEXT NOT NULL,
            location_name       TEXT NOT NULL,
            latitude            REAL NOT NULL,
            longitude           REAL NOT NULL,
            urgency             TEXT NOT NULL DEFAULT 'High',  -- Critical / High / Normal
            contact_mobile      TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'searching', -- searching / fulfilled / cancelled
            created_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ---- ALERTS: one row per (patient request, matched donor) pair ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id      INTEGER NOT NULL REFERENCES patient_requests(id),
            donor_id        INTEGER NOT NULL REFERENCES donors(id),
            distance_km     REAL NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',  -- pending / accepted / declined
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            responded_at    TEXT
        )
    """)

    conn.commit()
    conn.close()
