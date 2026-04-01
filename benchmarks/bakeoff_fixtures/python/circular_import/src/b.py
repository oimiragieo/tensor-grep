from src.a import wrap_a
from src.payments import create_invoice_cycle


def wrap_b(total):
    if total < 0:
        return wrap_a(total + 1)
    return create_invoice_cycle(total)
