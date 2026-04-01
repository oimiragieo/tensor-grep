use provider_alias_use_chain::service::build_receipt;

#[test]
fn build_receipt_uses_use_alias_chain() {
    assert_eq!(build_receipt(2), 3);
}
