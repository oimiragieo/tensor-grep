from src.payments import create_invoice_simple


def build_receipt_simple(total):
    return create_invoice_simple(total)
