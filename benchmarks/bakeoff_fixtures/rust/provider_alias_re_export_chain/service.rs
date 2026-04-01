use crate::exports::invoice_chain;

pub fn build_receipt_chain(total: i32) -> i32 {
    let run_invoice = invoice_chain;
    run_invoice(total)
}
