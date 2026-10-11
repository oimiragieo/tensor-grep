use super::ProjectDataV6;
use std::collections::HashSet;
use std::fs;
use std::path::Path;

pub(super) fn empty_rule_ids(data: &ProjectDataV6) -> HashSet<String> {
    let mut ids = HashSet::new();
    for (path, _) in &data.validation_metadata.rule_files {
        let Ok(content) = fs::read_to_string(path) else {
            continue;
        };
        let Ok(mut payload) = serde_yaml::from_str::<serde_yaml::Value>(&content) else {
            continue;
        };
        if payload.apply_merge().is_err() {
            continue;
        }
        let items = payload
            .get("rules")
            .and_then(serde_yaml::Value::as_sequence)
            .cloned()
            .unwrap_or_else(|| vec![payload.clone()]);
        for (idx, item) in items.iter().enumerate() {
            let direct = item.get("pattern").and_then(serde_yaml::Value::as_str);
            let nested = item
                .get("rule")
                .and_then(|rule| rule.get("pattern"))
                .and_then(serde_yaml::Value::as_str);
            if !direct
                .or(nested)
                .is_some_and(|pattern| pattern.trim().is_empty())
            {
                continue;
            }
            let fallback_id = Path::new(path)
                .file_stem()
                .and_then(|stem| stem.to_str())
                .unwrap_or("rule");
            let id = item
                .get("id")
                .and_then(serde_yaml::Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| {
                    if payload.get("rules").is_some() {
                        format!("{fallback_id}-{}", idx + 1)
                    } else {
                        fallback_id.to_string()
                    }
                });
            ids.insert(id);
        }
    }
    ids
}
