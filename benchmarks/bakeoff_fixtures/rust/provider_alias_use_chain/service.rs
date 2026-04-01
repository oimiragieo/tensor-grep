use crate::payments::create_invoice_provider_rust as invoice;

pub fn build_receipt(total: i32) -> i32 {
    let run_invoice = invoice;
    run_invoice(total)
}
