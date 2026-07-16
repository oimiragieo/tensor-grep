"""Converts an amount between two currency denominations."""


def convert_amount(amount, rate_table, source_code, target_code):
    return amount * rate_table[source_code][target_code]
