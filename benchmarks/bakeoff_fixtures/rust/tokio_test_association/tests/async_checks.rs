use tokio_test_association::billing::issue_invoice_async;

#[tokio::test]
async fn issue_invoice_async_smoke() {
    assert_eq!(issue_invoice_async(), 1);
}
