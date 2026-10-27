use super::{
    execute_native_search, exit_on_native_multi_pattern_ceiling_refusal, native_json_text_fields,
    native_search_config_path_display, NativeSearchConfig, SearchMatchJson,
};
use std::collections::BTreeSet;
use std::fs;
use std::sync::{Arc, Mutex};
use tensor_grep_rs::native_search::{run_native_fixed_multi_pattern_search, NativeOutputTarget};

pub(super) fn collect_native_multi_pattern_matches(
    patterns: &[String],
    mut base_config: NativeSearchConfig,
) -> anyhow::Result<(Vec<SearchMatchJson>, Option<usize>, usize)> {
    let include_pattern_metadata = patterns.len() > 1;
    let json_output = base_config.json;
    let structured_output = json_output || base_config.ndjson;
    let refusal_path = native_search_config_path_display(&base_config.paths);
    let fast_path_matches = run_native_fixed_multi_pattern_search(base_config.clone(), patterns)
        .map_err(|err| {
            exit_on_native_multi_pattern_ceiling_refusal(err, json_output, &refusal_path)
        })?;
    if let Some(matches) = fast_path_matches {
        return Ok((
            matches
                .into_iter()
                .map(|matched| {
                    let (text, bytes) = native_json_text_fields(&matched.raw);
                    SearchMatchJson {
                        file: matched.path.to_string_lossy().into_owned(),
                        line: matched.line_number as usize,
                        text: text.map(str::to_string),
                        bytes,
                        raw: matched.raw,
                        range: None,
                        meta_variables: None,
                        pattern_id: include_pattern_metadata.then_some(matched.pattern_id),
                        pattern_text: include_pattern_metadata.then_some(matched.pattern_text),
                    }
                })
                .collect(),
            None,
            0,
        ));
    }

    base_config.json = false;
    base_config.ndjson = false;
    base_config.count = false;
    base_config.output_target = NativeOutputTarget::Buffer(Arc::new(Mutex::new(Vec::new())));

    let mut matches = Vec::new();
    let mut binary_match_paths = BTreeSet::new();
    let mut walk_errors = 0;
    for (pattern_id, pattern) in patterns.iter().enumerate() {
        let mut pattern_config = base_config.clone();
        pattern_config.pattern = pattern.clone();
        pattern_config.track_binary_match_paths = structured_output;
        let stats = execute_native_search(pattern_config).map_err(|err| {
            exit_on_native_multi_pattern_ceiling_refusal(err, json_output, &refusal_path)
        })?;
        walk_errors += stats.walk_errors;
        binary_match_paths.extend(
            stats
                .binary_match_paths
                .into_iter()
                .map(|path| fs::canonicalize(&path).unwrap_or(path)),
        );
        matches.extend(stats.matches.into_iter().map(|matched| {
            let (text, bytes) = native_json_text_fields(&matched.raw);
            SearchMatchJson {
                file: matched.path.to_string_lossy().into_owned(),
                line: matched.line_number.unwrap_or(0) as usize,
                text: text.map(str::to_string),
                bytes,
                raw: matched.raw,
                range: None,
                meta_variables: None,
                pattern_id: include_pattern_metadata.then_some(pattern_id),
                pattern_text: include_pattern_metadata.then(|| pattern.clone()),
            }
        }));
    }

    Ok((matches, Some(walk_errors), binary_match_paths.len()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn multi_pattern_json_counts_unique_matching_binary_files() {
        let dir = tempfile::tempdir().expect("fixture directory must be created");
        let both = dir.path().join("both.bin");
        let other = dir.path().join("other.bin");
        let text = dir.path().join("text.txt");
        std::fs::write(&both, b"needle other\0payload").expect("binary fixture must be written");
        std::fs::write(&other, b"other\0payload").expect("binary fixture must be written");
        std::fs::write(&text, b"needle in text\n").expect("text fixture must be written");
        let absolute_both = std::fs::canonicalize(&both).expect("binary path must resolve");
        let config = NativeSearchConfig {
            pattern: "needle".to_string(),
            paths: vec![both, absolute_both, other, dir.path().to_path_buf()],
            fixed_strings: true,
            json: true,
            ..NativeSearchConfig::default()
        };

        let (matches, incomplete_paths, binary_match_files) = collect_native_multi_pattern_matches(
            &["needle".to_string(), "other".to_string()],
            config,
        )
        .expect("multi-pattern search must complete");

        assert_eq!(matches.len(), 1);
        assert_eq!(binary_match_files, 2);
        assert_eq!(incomplete_paths, Some(0));
    }
}
