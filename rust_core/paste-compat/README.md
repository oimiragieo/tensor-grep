# Tokenizer macro compatibility

`tokenizers` 0.23.2 imports `paste::paste!`, but the original `paste` crate is
archived (RUSTSEC-2024-0436). This local crate preserves that import and re-exports
the maintained `pastey` 0.2.3 macro. It contains no original `paste` implementation.
Cargo.lock pins the replacement's registry checksum. The normal advisory and
license checks remain enabled without an advisory exception.

The tokenizer's generated component conversions exercise this macro during
compilation; native tokenizer/model parity is checked against the reference
implementation before shipping the extension. The local crate must be included
in the source distribution so installation from source uses the same replacement.
