use crate::billing::{issue_invoice_alias as dispatch_invoice};

pub fn settle_alias() -> usize {
    dispatch_invoice()
}
