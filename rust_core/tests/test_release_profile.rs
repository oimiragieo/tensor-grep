use std::fs;
use std::path::Path;

#[test]
fn test_release_profile_enables_lto() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let cargo_toml = fs::read_to_string(manifest_dir.join("Cargo.toml")).unwrap();

    assert!(cargo_toml.contains("[profile.release]"));
    assert!(cargo_toml.contains("lto = true"));
}
