use provider_alias_re_export_chain::service::build_receipt_chain;

#[test]
fn build_receipt_chain_uses_re_export_alias() {
    assert_eq!(build_receipt_chain(2), 4);
}
