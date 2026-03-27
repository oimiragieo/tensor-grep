use crate::billing::issue_invoice_trait;

pub trait BillingEngine {
    fn dispatch(&self) -> usize;
}

pub struct InvoiceService;

impl BillingEngine for InvoiceService {
    fn dispatch(&self) -> usize {
        issue_invoice_trait()
    }
}
