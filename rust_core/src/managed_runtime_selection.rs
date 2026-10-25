use serde::Deserialize;
use std::fmt;

const MAX_RECORD_BYTES: usize = 512;
const CANONICAL_OVERHEAD: usize = br#"{"schema_version":1,"generation":"","version":""}"#.len() + 1;

#[derive(Debug, Eq, PartialEq)]
pub struct ManagedRuntimeSelection {
    generation: String,
    version: String,
}

impl ManagedRuntimeSelection {
    pub fn generation(&self) -> &str {
        &self.generation
    }

    pub fn version(&self) -> &str {
        &self.version
    }
}

#[derive(Debug, Eq, PartialEq)]
pub enum SelectionError {
    TooLarge,
    InvalidRecord,
}

impl fmt::Display for SelectionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::TooLarge => formatter.write_str("selection record exceeds 512 bytes"),
            Self::InvalidRecord => formatter.write_str("selection record is invalid"),
        }
    }
}

impl std::error::Error for SelectionError {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SelectionWire {
    schema_version: u64,
    generation: String,
    version: String,
}

pub fn decode_selection(payload: &[u8]) -> Result<ManagedRuntimeSelection, SelectionError> {
    if payload.len() > MAX_RECORD_BYTES {
        return Err(SelectionError::TooLarge);
    }

    // JSON whitespace is SPACE, TAB, CR, LF.
    if payload
        .iter()
        .copied()
        .find(|byte| !matches!(*byte, 0x20 | 0x09 | 0x0d | 0x0a))
        != Some(b'{')
    {
        return Err(SelectionError::InvalidRecord);
    }

    let wire: SelectionWire =
        serde_json::from_slice(payload).map_err(|_| SelectionError::InvalidRecord)?;
    if wire.schema_version != 1
        || !valid_generation(&wire.generation)
        || !valid_version(&wire.version)
    {
        return Err(SelectionError::InvalidRecord);
    }

    if CANONICAL_OVERHEAD + wire.generation.len() + wire.version.len() > MAX_RECORD_BYTES {
        return Err(SelectionError::TooLarge);
    }

    Ok(ManagedRuntimeSelection {
        generation: wire.generation,
        version: wire.version,
    })
}

fn valid_generation(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_version(value: &str) -> bool {
    let mut components = value.split('.');
    let Some(major) = components.next() else {
        return false;
    };
    let Some(minor) = components.next() else {
        return false;
    };
    let Some(patch) = components.next() else {
        return false;
    };
    components.next().is_none()
        && valid_version_component(major)
        && valid_version_component(minor)
        && valid_version_component(patch)
}

fn valid_version_component(value: &str) -> bool {
    !value.is_empty()
        && (value.len() == 1 || !value.starts_with('0'))
        && value.bytes().all(|byte| byte.is_ascii_digit())
}

#[cfg(test)]
mod tests {
    use super::{decode_selection, SelectionError};

    const VALID: &[u8] = b"{\"schema_version\":1,\"generation\":\"0123456789abcdef0123456789abcdef\",\"version\":\"0.1.0\"}";
    const GENERATION: &str = "0123456789abcdef0123456789abcdef";
    const CANONICAL_MAX_VERSION: &str = "999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999.0.0";
    const CANONICAL_OVER_VERSION: &str = "9999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999.0.0";
    const BIG_VERSION: &str = "9999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999.0.0";

    #[test]
    fn decode_valid_record_returns_exact_fields() {
        let selection = decode_selection(VALID).unwrap();

        assert_eq!(selection.generation(), GENERATION);
        assert_eq!(selection.version(), "0.1.0");
    }

    #[test]
    fn decode_large_numeric_version_parts_accepts_without_integer_conversion() {
        let payload = format!("{{\"schema_version\":1,\"generation\":\"{GENERATION}\",\"version\":\"{BIG_VERSION}\"}}");

        let selection = decode_selection(payload.as_bytes()).unwrap();

        assert_eq!(selection.version(), BIG_VERSION);
    }

    #[test]
    fn decode_512_byte_record_accepts_trailing_json_whitespace() {
        let mut payload = VALID.to_vec();
        payload.resize(512, b' ');

        let selection = decode_selection(&payload).unwrap();

        assert_eq!(payload.len(), 512);
        assert_eq!(selection.generation(), GENERATION);
        assert_eq!(selection.version(), "0.1.0");
    }

    #[test]
    fn decode_oversized_malformed_record_returns_too_large_first() {
        let payload = [0xff; 513];

        let result = decode_selection(&payload);

        assert_eq!(result, Err(SelectionError::TooLarge));
    }

    #[test]
    fn selection_error_too_large_displays_stable_message() {
        let error = SelectionError::TooLarge;

        let message = error.to_string();

        assert_eq!(message, "selection record exceeds 512 bytes");
    }

    #[test]
    fn selection_error_invalid_record_displays_stable_message() {
        let error = SelectionError::InvalidRecord;

        let message = error.to_string();

        assert_eq!(message, "selection record is invalid");
    }

    #[test]
    fn decode_duplicate_keys_rejects_record() {
        let payload = br#"{"schema_version":1,"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"0.1.0"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_unknown_field_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"0.1.0","extra":0}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_missing_field_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_boolean_schema_rejects_record() {
        let payload = br#"{"schema_version":true,"generation":"0123456789abcdef0123456789abcdef","version":"0.1.0"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_float_schema_rejects_record() {
        let payload = br#"{"schema_version":1.0,"generation":"0123456789abcdef0123456789abcdef","version":"0.1.0"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_unsupported_schema_rejects_record() {
        let payload = br#"{"schema_version":2,"generation":"0123456789abcdef0123456789abcdef","version":"0.1.0"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_invalid_utf8_rejects_record() {
        let payload = &[0xff];

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_nonobject_rejects_record() {
        let payload = b"[]";

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_trailing_data_rejects_record() {
        let payload = b"{} trailing";

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_nonstring_generation_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":7,"version":"0.1.0"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_nonstring_version_rejects_record() {
        let payload =
            br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":7}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_uppercase_generation_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789ABCDEF0123456789ABCDEF","version":"0.1.0"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_nonascii_generation_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcde\u0661","version":"0.1.0"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_traversal_generation_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"../0123456789abcdef0123456789abcdef","version":"0.1.0"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_windows_path_generation_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"C:\\0123456789abcdef0123456789abcdef","version":"0.1.0"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_leadingzero_version_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"1.02.3"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_prerelease_version_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"1.2.3rc1"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_local_version_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"1.2.3+local"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_nonascii_version_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"\u0661.2.3"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_short_generation_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcde","version":"1.2.3"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_long_generation_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef0","version":"1.2.3"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_missing_version_component_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"1.2"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_extra_version_component_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"1.2.3.4"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_empty_version_component_rejects_record() {
        let payload = br#"{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"1..3"}"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_parity_populated_array_rejects_record() {
        let payload = br#"[1,"0123456789abcdef0123456789abcdef","0.1.0"]"#;

        let result = decode_selection(payload);

        assert_eq!(result, Err(SelectionError::InvalidRecord));
    }

    #[test]
    fn decode_parity_canonical_max_accepts_record() {
        let payload = format!(
            r#"{{"schema_version":1,"generation":"{GENERATION}","version":"{CANONICAL_MAX_VERSION}"}}"#
        );

        let selection = decode_selection(payload.as_bytes()).unwrap();

        assert_eq!(payload.len(), 511);
        assert_eq!(selection.generation(), GENERATION);
        assert_eq!(selection.version(), CANONICAL_MAX_VERSION);
    }

    #[test]
    fn decode_parity_canonical_overflow_rejects_record() {
        let payload = format!(
            r#"{{"schema_version":1,"generation":"{GENERATION}","version":"{CANONICAL_OVER_VERSION}"}}"#
        );

        let result = decode_selection(payload.as_bytes());

        assert_eq!(payload.len(), 512);
        assert_eq!(result, Err(SelectionError::TooLarge));
    }

    #[test]
    fn decode_parity_leading_json_whitespace_accepts_object() {
        let mut payload = b" \t\r\n".to_vec();
        payload.extend_from_slice(VALID);

        let selection = decode_selection(&payload).unwrap();

        assert_eq!(selection.generation(), GENERATION);
        assert_eq!(selection.version(), "0.1.0");
    }
}
