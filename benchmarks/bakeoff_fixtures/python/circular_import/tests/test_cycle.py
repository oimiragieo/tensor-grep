from src.payments import create_invoice_cycle


def test_create_invoice_cycle():
    assert create_invoice_cycle(1) == 2
