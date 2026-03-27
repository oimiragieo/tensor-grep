from src.builders import PremiumInvoiceBuilder


def test_build_premium_invoice():
    builder = PremiumInvoiceBuilder()
    assert builder.build_premium_invoice(2) == 3
