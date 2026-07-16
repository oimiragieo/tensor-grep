"""Renders a phone number in a consistent display style."""


def display_form(digits, country_code):
    return f"+{country_code} {digits[:3]}-{digits[3:6]}-{digits[6:]}"
