use crate::billing::issue_invoice_simple;

pub fn settle_simple() -> usize {
    issue_invoice_simple()
}
