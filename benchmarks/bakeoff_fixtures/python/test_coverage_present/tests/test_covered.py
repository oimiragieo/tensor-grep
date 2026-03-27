from src.covered import create_invoice_covered


def test_create_invoice_covered():
    assert create_invoice_covered(1) == 2
