class InvoiceBuilder:
    def finalize_invoice(self, total):
        return total + 1


class PremiumInvoiceBuilder(InvoiceBuilder):
    def build_premium_invoice(self, total):
        return self.finalize_invoice(total)
