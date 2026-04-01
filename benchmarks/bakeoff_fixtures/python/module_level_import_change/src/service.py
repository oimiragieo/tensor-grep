import src.payments as payments


def build_receipt_imported(total):
    return payments.create_invoice_imported(total)
