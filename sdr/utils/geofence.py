import math


def distance_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    r_lat1 = math.radians(lat1)
    r_lat2 = math.radians(lat2)
    sin_d_lat = math.sin(d_lat / 2)
    sin_d_lon = math.sin(d_lon / 2)
    h = sin_d_lat * sin_d_lat + math.cos(r_lat1) * math.cos(r_lat2) * sin_d_lon * sin_d_lon
    return 2 * R * math.asin(math.sqrt(h))


def is_outside_geofence(lat, lon, center_lat, center_lon, radius_m):
    return distance_m(lat, lon, center_lat, center_lon) > radius_m
