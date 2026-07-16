"""Puts a mailing address into a consistent shape."""


def canonical_form(street, city, region, postal_code):
    return f"{street.strip()}, {city.strip()}, {region.strip()} {postal_code.strip()}"
