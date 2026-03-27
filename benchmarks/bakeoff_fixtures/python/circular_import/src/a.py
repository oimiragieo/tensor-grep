from src.b import wrap_b
from src.payments import create_invoice_cycle


def wrap_a(total):
    return wrap_b(create_invoice_cycle(total))
