"""Maps a coordinate pair to an administrative region name."""


def region_for_coordinates(latitude, longitude):
    return f"region-{int(latitude)}-{int(longitude)}"
