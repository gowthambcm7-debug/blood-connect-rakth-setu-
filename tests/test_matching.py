from types import SimpleNamespace
from matching import SEARCH_RADIUS_KM, find_matching_donors, haversine_km

def profile(i, group, lat, lon, available=True):
    return SimpleNamespace(id=i, blood_group=group, latitude=lat, longitude=lon, available=available)

def request(group, lat=17.9689, lon=79.5941):
    return SimpleNamespace(blood_group_needed=group, latitude=lat, longitude=lon)

def test_radius_is_twenty_kilometers():
    assert SEARCH_RADIUS_KM == 20

def test_compatible_available_donors_are_ranked_nearest_first():
    donor_far = profile(2, "O-", 18.10, 79.5941)
    donor_near = profile(1, "O-", 17.98, 79.5941)
    donor_incompatible = profile(3, "AB+", 17.98, 79.5941)
    donor_paused = profile(4, "O-", 17.98, 79.5941, False)
    result = find_matching_donors(request("A+"), [donor_far, donor_near, donor_incompatible, donor_paused])
    assert [m["donor"].id for m in result] == [1, 2]
    assert result[0]["distance_km"] < result[1]["distance_km"]

def test_donor_outside_radius_is_not_matched():
    donor = profile(5, "O-", 18.2, 79.5941)
    assert haversine_km(17.9689, 79.5941, 18.2, 79.5941) > SEARCH_RADIUS_KM
    assert find_matching_donors(request("AB+"), [donor]) == []
