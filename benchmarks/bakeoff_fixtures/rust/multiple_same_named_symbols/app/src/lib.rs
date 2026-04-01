use shared::billing::{Invoice, issue_invoice_dupe as dispatch};

pub fn settle_invoice() -> (Invoice, usize) {
    (Invoice, dispatch())
}
