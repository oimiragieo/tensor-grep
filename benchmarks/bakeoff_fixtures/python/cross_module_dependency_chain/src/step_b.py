from src.payments import create_invoice_chain


def call_b(total):
    return create_invoice_chain(total)
