from django.test import TestCase
from sdr.utils.geofence import distance_m, is_outside_geofence


class GeofenceTestCase(TestCase):
    def test_distance_between_known_points_about_one_km_apart(self):
        # Two points roughly 1km apart along a meridian: 1 degree of latitude is
        # ~111.32km, so 1/111.32 degrees of latitude is ~1000m.
        lat1, lon1 = 51.5, -0.1
        lat2, lon2 = 51.5 + (1 / 111.32), -0.1

        distance = distance_m(lat1, lon1, lat2, lon2)

        self.assertAlmostEqual(distance, 1000, delta=5)

    def test_point_at_center_is_zero_distance(self):
        distance = distance_m(51.5, -0.1, 51.5, -0.1)

        self.assertAlmostEqual(distance, 0, delta=0.001)

    def test_point_exactly_at_center_is_never_outside(self):
        self.assertFalse(is_outside_geofence(51.5, -0.1, 51.5, -0.1, 1))

    def test_point_far_away_is_always_outside(self):
        # London vs. New York - thousands of km apart, well outside any sane radius.
        self.assertTrue(is_outside_geofence(51.5, -0.1, 40.7, -74.0, 1000))

    def test_point_within_radius_is_not_outside(self):
        lat1, lon1 = 51.5, -0.1
        lat2, lon2 = 51.5 + (1 / 111.32), -0.1  # ~1000m away

        self.assertFalse(is_outside_geofence(lat1, lon1, lat2, lon2, 1500))

    def test_point_beyond_radius_is_outside(self):
        lat1, lon1 = 51.5, -0.1
        lat2, lon2 = 51.5 + (1 / 111.32), -0.1  # ~1000m away

        self.assertTrue(is_outside_geofence(lat1, lon1, lat2, lon2, 500))
