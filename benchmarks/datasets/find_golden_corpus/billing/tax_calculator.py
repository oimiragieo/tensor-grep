"""Computes the tax owed on a sale amount."""


def compute_owed(sale_amount, rate_percent):
    return round(sale_amount * rate_percent / 100.0, 2)
