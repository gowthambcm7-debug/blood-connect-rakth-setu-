"""
matching.py
-----------
The "brain" of the app: given a patient's request, find every
available donor who is (a) blood-compatible and (b) within 30 km.

Kept separate from app.py so the matching RULES are easy to find,
read, and change without touching any web/route code.
"""

import math  # for the trigonometry used in the distance formula

# Radius (in kilometres) within which we consider a donor "reachable"
# for an emergency. Change this one number to change the whole app's range.
SEARCH_RADIUS_KM = 30

# ---------------------------------------------------------------------------
# Blood group compatibility chart.
# Key   = donor's blood group
# Value = list of patient blood groups that donor is allowed to give blood to
#
# This is the real medical compatibility chart (not just "exact match"),
# so e.g. an O- donor will be alerted for ANY patient, since O- is the
# universal donor.
# ---------------------------------------------------------------------------
COMPATIBILITY = {
    "O-":  ["O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"],  # universal donor
    "O+":  ["O+", "A+", "B+", "AB+"],
    "A-":  ["A-", "A+", "AB-", "AB+"],
    "A+":  ["A+", "AB+"],
    "B-":  ["B-", "B+", "AB-", "AB+"],
    "B+":  ["B+", "AB+"],
    "AB-": ["AB-", "AB+"],
    "AB+": ["AB+"],   # AB+ can only donate to another AB+ (but can RECEIVE from anyone)
}


def haversine_km(lat1, lon1, lat2, lon2):
    """
    Calculates the "as the crow flies" distance in kilometres between
    two (latitude, longitude) points on the Earth's surface.

    This is the standard Haversine formula - it accounts for the fact
    that the Earth is a sphere, not a flat grid, so we can't just use
    plain Pythagoras (a^2 + b^2 = c^2) on the raw coordinates.
    """
    R = 6371.0  # average radius of the Earth, in kilometres

    # Convert all four coordinates from degrees to radians,
    # because Python's math.sin / math.cos expect radians.
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    # 'a' is the square of half the chord length between the points
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)

    # 'c' is the angular distance in radians
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c  # distance in km


def compatible_donor_groups(patient_blood_group):
    """
    Given the blood group a PATIENT needs, return the list of donor
    blood groups that are allowed to give to them.
    (This is just COMPATIBILITY inverted/filtered.)
    """
    return [donor_group for donor_group, can_give_to in COMPATIBILITY.items()
            if patient_blood_group in can_give_to]


def find_matching_donors(patient_request, all_donors):
    """
    patient_request: a sqlite3.Row (or dict) with latitude/longitude/blood_group_needed
    all_donors:      a list of sqlite3.Row (or dict) donor records

    Returns a list of dicts: [{ "donor": <row>, "distance_km": <float> }, ...]
    sorted by distance (closest / fastest-to-reach donor first),
    containing only donors who are AVAILABLE, BLOOD-COMPATIBLE, and
    within SEARCH_RADIUS_KM.
    """
    good_groups = set(compatible_donor_groups(patient_request["blood_group_needed"]))
    matches = []

    for donor in all_donors:
        # Skip donors who've paused themselves (e.g. recently donated / unwell)
        if not donor["available"]:
            continue

        # Skip donors whose blood group can't help this patient
        if donor["blood_group"] not in good_groups:
            continue

        distance = haversine_km(
            patient_request["latitude"], patient_request["longitude"],
            donor["latitude"], donor["longitude"],
        )

        if distance <= SEARCH_RADIUS_KM:
            matches.append({"donor": donor, "distance_km": round(distance, 1)})

    # Closest donor first - they can reach the hospital fastest.
    matches.sort(key=lambda m: m["distance_km"])
    return matches
