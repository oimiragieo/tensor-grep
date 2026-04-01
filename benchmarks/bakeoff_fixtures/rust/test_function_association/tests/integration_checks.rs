use test_function_association::billing::issue_invoice_tested;

#[test]
fn issue_invoice_tested_smoke() {
    assert_eq!(issue_invoice_tested(), 1);
}
