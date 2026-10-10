"""SPO: Smart Proximity Organizer — transparent blood and geographic matching."""
import math

SEARCH_RADIUS_KM = 20
INITIAL_ALERT_BATCH = 1
ESCALATION_BATCH_SIZE = 5

COMPATIBILITY = {
    "O-": ["O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"],
    "O+": ["O+", "A+", "B+", "AB+"],
    "A-": ["A-", "A+", "AB-", "AB+"],
    "A+": ["A+", "AB+"],
    "B-": ["B-", "B+", "AB-", "AB+"],
    "B+": ["B+", "AB+"],
    "AB-": ["AB-", "AB+"],
    "AB+": ["AB+"],
}


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between coordinates, in kilometers."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def compatible_donor_groups(patient_blood_group):
    return [group for group, recipients in COMPATIBILITY.items() if patient_blood_group in recipients]


def find_matching_donors(patient_request, donor_profiles, exclude_ids=None):
    """Filter to available, compatible donors inside 20 km; nearest first."""
    excluded = set(exclude_ids or ())
    acceptable = set(compatible_donor_groups(patient_request.blood_group_needed))
    matches = []
    for donor in donor_profiles:
        if donor.id in excluded or not donor.available or donor.blood_group not in acceptable:
            continue
        distance = haversine_km(patient_request.latitude, patient_request.longitude,
                                donor.latitude, donor.longitude)
        if distance <= SEARCH_RADIUS_KM:
            matches.append({"donor": donor, "distance_km": round(distance, 1)})
    return sorted(matches, key=lambda item: (item["distance_km"], item["donor"].id))
