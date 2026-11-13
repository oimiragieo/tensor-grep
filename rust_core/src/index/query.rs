use super::{IndexQueryResult, RegexCandidateSelection, TrigramIndex, MAX_INDEXED_FILE_BYTES};
use anyhow::{Context, Result};
use grep_regex::RegexMatcher;
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

type IndexQueryOutput = (Vec<IndexQueryResult>, Vec<(PathBuf, u64)>);
type FileQueryMatches = (Vec<IndexQueryResult>, Option<(PathBuf, u64)>);

#[cfg(test)]
mod tests;

enum SearchMatcher {
    Fixed {
        needle: String,
        lower_needle: Option<String>,
    },
    Regex(regex::Regex),
}

impl SearchMatcher {
    fn new(pattern: &str, ignore_case: bool, fixed_strings: bool) -> Result<Self> {
        if fixed_strings {
            return Ok(Self::Fixed {
                needle: pattern.to_string(),
                lower_needle: ignore_case.then(|| pattern.to_lowercase()),
            });
        }

        regex::RegexBuilder::new(pattern)
            .case_insensitive(ignore_case)
            .build()
            .context(format!(
                "failed to compile index search pattern '{pattern}'"
            ))
            .map(Self::Regex)
    }

    fn is_match(&self, line: &str) -> bool {
        match self {
            Self::Fixed {
                needle,
                lower_needle,
            } => {
                if let Some(lower_needle) = lower_needle {
                    line.to_lowercase().contains(lower_needle)
                } else {
                    line.contains(needle)
                }
            }
            Self::Regex(re) => re.is_match(line),
        }
    }
}

impl TrigramIndex {
    pub fn search(
        &self,
        pattern: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> Result<Vec<IndexQueryResult>> {
        self.search_with_binary_matches(pattern, ignore_case, fixed_strings)
            .map(|(matches, _)| matches)
    }

    /// Returns text matches and matching binary files with their first detected NUL offsets.
    /// The original `search` API remains the text-only `Vec<IndexQueryResult>` contract.
    pub fn search_with_binary_matches(
        &self,
        pattern: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> Result<IndexQueryOutput> {
        self.ensure_searchable()?;
        let selection = if fixed_strings {
            self.fixed_string_candidate_selection(pattern, ignore_case)?
        } else {
            self.regex_candidate_selection(pattern, ignore_case)?
        };

        // A path is canonical-root-relative in the index and dereferenced through the
        // canonical root here. `None` means scan every line, including oversized entries.
        let mut work: HashMap<PathBuf, Option<Vec<usize>>> = HashMap::new();
        match selection {
            RegexCandidateSelection::Indexed(candidates) => {
                for (path, line) in candidates {
                    match work.entry(path) {
                        std::collections::hash_map::Entry::Vacant(entry) => {
                            entry.insert(Some(vec![line]));
                        }
                        std::collections::hash_map::Entry::Occupied(mut entry) => {
                            if let Some(lines) = entry.get_mut() {
                                lines.push(line);
                            }
                        }
                    }
                }
                // Oversized files deliberately have no postings. Add them before an empty
                // posting result can be mistaken for a complete no-match answer.
                for entry in self
                    .files
                    .iter()
                    .filter(|entry| !entry.deleted && entry.size > MAX_INDEXED_FILE_BYTES)
                {
                    work.insert(self.deref_path(&entry.path), None);
                }
            }
            RegexCandidateSelection::FullScan => {
                for entry in self.files.iter().filter(|entry| !entry.deleted) {
                    work.insert(self.deref_path(&entry.path), None);
                }
            }
        }

        let matcher = SearchMatcher::new(pattern, ignore_case, fixed_strings)?;
        let binary_matcher =
            crate::native_search::index_binary_matcher(pattern, ignore_case, fixed_strings);
        let mut files: Vec<_> = work.into_iter().collect();
        files.sort_by(|left, right| left.0.cmp(&right.0));
        let scanned: Vec<Result<FileQueryMatches>> = files
            .par_iter()
            .map(|(path, lines)| collect_matches(path, lines.as_deref(), &matcher, &binary_matcher))
            .collect();

        let mut matches = Vec::new();
        let mut binary_matches = Vec::new();
        for result in scanned {
            let (file_matches, binary_match) = result?;
            matches.extend(file_matches);
            binary_matches.extend(binary_match);
        }
        matches.sort_by(|left, right| left.file.cmp(&right.file).then(left.line.cmp(&right.line)));
        binary_matches.sort();
        binary_matches.dedup_by(|left, right| left.0 == right.0);
        Ok((matches, binary_matches))
    }
}

fn collect_matches(
    file: &Path,
    candidate_lines: Option<&[usize]>,
    matcher: &SearchMatcher,
    binary_matcher: &Result<RegexMatcher>,
) -> Result<FileQueryMatches> {
    if let Some(offset) = crate::native_search::first_binary_nul_offset(file)? {
        let binary_matcher = binary_matcher
            .as_ref()
            .map_err(|err| anyhow::anyhow!("failed to compile binary search pattern: {err:#}"))?;
        let matches =
            crate::native_search::binary_file_matches_pattern(binary_matcher, file, true)?;
        return Ok((Vec::new(), matches.then(|| (file.to_path_buf(), offset))));
    }

    let candidate_lines: Option<HashSet<usize>> =
        candidate_lines.map(|lines| lines.iter().copied().collect());
    let mut reader = BufReader::new(
        File::open(file).with_context(|| format!("failed to read {}", file.display()))?,
    );
    let mut bytes = Vec::new();
    let mut line_number = 0usize;
    let mut results = Vec::new();
    let mut saw_late_nul = false;
    loop {
        bytes.clear();
        let read = reader
            .read_until(b'\n', &mut bytes)
            .with_context(|| format!("failed to read {}", file.display()))?;
        if read == 0 {
            break;
        }
        saw_late_nul |= bytes.contains(&0);
        line_number += 1;
        let terminated = bytes.last() == Some(&b'\n');
        if terminated {
            bytes.pop();
            if bytes.last() == Some(&b'\r') {
                bytes.pop();
            }
        }
        if candidate_lines
            .as_ref()
            .is_some_and(|lines| !lines.contains(&line_number))
        {
            continue;
        }
        let line = String::from_utf8_lossy(&bytes);
        if matcher.is_match(&line) {
            results.push(IndexQueryResult {
                file: file.to_path_buf(),
                line: line_number,
                text: line.into_owned(),
            });
        }
    }
    if saw_late_nul {
        if let Ok(binary_matcher) = binary_matcher.as_ref() {
            if let Some(offset) =
                crate::native_search::binary::native_binary_nul_offset(binary_matcher, file)?
            {
                let matches = crate::native_search::binary::binary_file_matches_pattern(
                    binary_matcher,
                    file,
                    true,
                )?;
                return Ok((Vec::new(), matches.then(|| (file.to_path_buf(), offset))));
            }
        }
    }
    Ok((results, None))
}
