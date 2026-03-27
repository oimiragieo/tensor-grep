from src.payments import create_invoice_multi


def build_receipt_a(total):
    return create_invoice_multi(total)
