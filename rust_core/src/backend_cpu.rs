use memchr::memmem;
use memmap2::{MmapMut, MmapOptions};
use rayon::prelude::*;
use regex::bytes::RegexBuilder;
use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

pub struct CpuBackend;

struct ReplacementOp {
    start: usize,
    end: usize,
    bytes: Vec<u8>,
}

impl Default for CpuBackend {
    fn default() -> Self {
        Self::new()
    }
}

impl CpuBackend {
    pub fn new() -> Self {
        Self
    }

    pub fn search(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
    ) -> anyhow::Result<Vec<(usize, String)>> {
        let path_obj = Path::new(path);
        let mut results = Vec::new();

        if fixed_strings && !ignore_case {
            let pat_bytes = pattern.as_bytes();
            if path_obj.is_file() {
                if let Ok(file_results) =
                    self.search_file_memmem(pat_bytes, &path_obj.to_path_buf(), invert_match)
                {
                    results.extend(file_results);
                }
            } else if path_obj.is_dir() {
                for entry in WalkDir::new(path_obj).into_iter().filter_map(|e| e.ok()) {
                    if entry.file_type().is_file() {
                        if let Ok(file_results) = self.search_file_memmem(
                            pat_bytes,
                            &entry.path().to_path_buf(),
                            invert_match,
                        ) {
                            results.extend(file_results);
                        }
                    }
                }
            }
            return Ok(results);
        }

        let re = if fixed_strings {
            RegexBuilder::new(&regex::escape(pattern))
                .case_insensitive(ignore_case)
                .build()?
        } else {
            RegexBuilder::new(pattern)
                .case_insensitive(ignore_case)
                .build()?
        };

        if path_obj.is_file() {
            if let Ok(file_results) =
                self.search_file_regex(&re, &path_obj.to_path_buf(), invert_match)
            {
                results.extend(file_results);
            }
        } else if path_obj.is_dir() {
            for entry in WalkDir::new(path_obj).into_iter().filter_map(|e| e.ok()) {
                if entry.file_type().is_file() {
                    if let Ok(file_results) =
                        self.search_file_regex(&re, &entry.path().to_path_buf(), invert_match)
                    {
                        results.extend(file_results);
                    }
                }
            }
        }

        Ok(results)
    }

    fn search_file_memmem(
        &self,
        pattern: &[u8],
        path: &PathBuf,
        invert_match: bool,
    ) -> anyhow::Result<Vec<(usize, String)>> {
        let file = File::open(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        let mut results = Vec::new();
        let mut line_num = 1;
        let mut start = 0;

        for (i, &byte) in mmap.iter().enumerate() {
            if byte == b'\n' {
                let line_bytes = &mmap[start..i];
                let is_match = memmem::find(line_bytes, pattern).is_some();
                let should_include = if invert_match { !is_match } else { is_match };

                if should_include && !line_bytes.is_empty() {
                    let line_str = String::from_utf8_lossy(line_bytes).into_owned();
                    results.push((line_num, line_str));
                }
                start = i + 1;
                line_num += 1;
            }
        }

        if start < mmap.len() {
            let line_bytes = &mmap[start..];
            let is_match = memmem::find(line_bytes, pattern).is_some();
            let should_include = if invert_match { !is_match } else { is_match };

            if should_include && !line_bytes.is_empty() {
                let line_str = String::from_utf8_lossy(line_bytes).into_owned();
                results.push((line_num, line_str));
            }
        }

        Ok(results)
    }

    fn search_file_regex(
        &self,
        re: &regex::bytes::Regex,
        path: &PathBuf,
        invert_match: bool,
    ) -> anyhow::Result<Vec<(usize, String)>> {
        let file = File::open(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        let mut results = Vec::new();
        let mut line_num = 1;
        let mut start = 0;

        for (i, &byte) in mmap.iter().enumerate() {
            if byte == b'\n' {
                let line_bytes = &mmap[start..i];
                let is_match = re.is_match(line_bytes);
                let should_include = if invert_match { !is_match } else { is_match };

                if should_include && !line_bytes.is_empty() {
                    let line_str = String::from_utf8_lossy(line_bytes).into_owned();
                    results.push((line_num, line_str));
                }
                start = i + 1;
                line_num += 1;
            }
        }

        if start < mmap.len() {
            let line_bytes = &mmap[start..];
            let is_match = re.is_match(line_bytes);
            let should_include = if invert_match { !is_match } else { is_match };

            if should_include && !line_bytes.is_empty() {
                let line_str = String::from_utf8_lossy(line_bytes).into_owned();
                results.push((line_num, line_str));
            }
        }

        Ok(results)
    }

    pub fn replace_in_place(
        &self,
        pattern: &str,
        replacement: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> anyhow::Result<()> {
        let path_obj = Path::new(path);

        if fixed_strings && !ignore_case && !pattern.is_empty() {
            if path_obj.is_file() {
                self.replace_file_literal(
                    pattern.as_bytes(),
                    replacement.as_bytes(),
                    &path_obj.to_path_buf(),
                )?;
            } else if path_obj.is_dir() {
                for entry in WalkDir::new(path_obj).into_iter().filter_map(|e| e.ok()) {
                    if entry.file_type().is_file() {
                        let _ = self.replace_file_literal(
                            pattern.as_bytes(),
                            replacement.as_bytes(),
                            &entry.path().to_path_buf(),
                        );
                    }
                }
            }

            return Ok(());
        }

        let re = if fixed_strings {
            RegexBuilder::new(&regex::escape(pattern))
                .case_insensitive(ignore_case)
                .build()?
        } else {
            RegexBuilder::new(pattern)
                .case_insensitive(ignore_case)
                .build()?
        };

        if path_obj.is_file() {
            self.replace_file_regex(&re, replacement, &path_obj.to_path_buf())?;
        } else if path_obj.is_dir() {
            for entry in WalkDir::new(path_obj).into_iter().filter_map(|e| e.ok()) {
                if entry.file_type().is_file() {
                    let _ = self.replace_file_regex(&re, replacement, &entry.path().to_path_buf());
                }
            }
        }

        Ok(())
    }

    fn replace_file_literal(
        &self,
        pattern: &[u8],
        replacement: &[u8],
        path: &PathBuf,
    ) -> anyhow::Result<()> {
        let file = OpenOptions::new().read(true).write(true).open(path)?;

        let source = unsafe { MmapOptions::new().map(&file)? };
        let original_len = source.len();
        if original_len == 0 {
            return Ok(());
        }

        let match_starts: Vec<usize> = memmem::find_iter(&source[..], pattern).collect();
        if match_starts.is_empty() {
            return Ok(());
        }

        if replacement.len() == pattern.len() {
            drop(source);
            let mut mmap = unsafe { MmapOptions::new().map_mut(&file)? };
            for start in match_starts {
                let end = start + replacement.len();
                mmap[start..end].copy_from_slice(replacement);
            }
            mmap.flush()?;
            return Ok(());
        }

        let replacements: Vec<ReplacementOp> = match_starts
            .into_iter()
            .map(|start| ReplacementOp {
                start,
                end: start + pattern.len(),
                bytes: replacement.to_vec(),
            })
            .collect();

        drop(source);

        self.write_replacements_with_mmap(&file, original_len, &replacements)
    }

    fn replace_file_regex(
        &self,
        re: &regex::bytes::Regex,
        replacement: &str,
        path: &PathBuf,
    ) -> anyhow::Result<()> {
        let file = OpenOptions::new().read(true).write(true).open(path)?;

        let source = unsafe { MmapOptions::new().map(&file)? };
        let original_len = source.len();
        if original_len == 0 {
            return Ok(());
        }

        let replacements = self.collect_replacements(re, replacement, &source[..])?;
        if replacements.is_empty() {
            return Ok(());
        }

        drop(source);

        self.write_replacements_with_mmap(&file, original_len, &replacements)
    }

    fn write_replacements_with_mmap(
        &self,
        file: &File,
        original_len: usize,
        replacements: &[ReplacementOp],
    ) -> anyhow::Result<()> {
        let (new_len, max_extra_growth) = self.plan_replacements(original_len, replacements)?;

        let required_len = original_len + max_extra_growth;
        if required_len > original_len {
            file.set_len(required_len as u64)?;
        }

        {
            let mut mmap = unsafe { MmapOptions::new().map_mut(file)? };
            self.apply_replacements_in_place(&mut mmap, original_len, &replacements)?;
        }

        if new_len < required_len {
            file.set_len(new_len as u64)?;
        }

        Ok(())
    }

    fn collect_replacements(
        &self,
        re: &regex::bytes::Regex,
        replacement: &str,
        source: &[u8],
    ) -> anyhow::Result<Vec<ReplacementOp>> {
        let mut replacements = Vec::new();

        for captures in re.captures_iter(source) {
            let matched = captures
                .get(0)
                .ok_or_else(|| anyhow::anyhow!("regex capture missing full match"))?;

            let mut expanded = Vec::new();
            captures.expand(replacement.as_bytes(), &mut expanded);

            replacements.push(ReplacementOp {
                start: matched.start(),
                end: matched.end(),
                bytes: expanded,
            });
        }

        Ok(replacements)
    }

    fn plan_replacements(
        &self,
        original_len: usize,
        replacements: &[ReplacementOp],
    ) -> anyhow::Result<(usize, usize)> {
        let mut final_len = original_len;
        let mut running_delta: isize = 0;
        let mut max_extra_growth = 0usize;

        for replacement in replacements {
            final_len = final_len
                .checked_sub(replacement.end - replacement.start)
                .ok_or_else(|| anyhow::anyhow!("replacement length underflow"))?;
            final_len = final_len
                .checked_add(replacement.bytes.len())
                .ok_or_else(|| anyhow::anyhow!("replacement length overflow"))?;

            let replacement_delta =
                replacement.bytes.len() as isize - (replacement.end - replacement.start) as isize;
            running_delta += replacement_delta;
            max_extra_growth = max_extra_growth.max(running_delta.max(0) as usize);
        }

        Ok((final_len, max_extra_growth))
    }

    fn apply_replacements_in_place(
        &self,
        mmap: &mut MmapMut,
        original_len: usize,
        replacements: &[ReplacementOp],
    ) -> anyhow::Result<()> {
        let mut current_len = original_len;
        let mut delta: isize = 0;

        for replacement in replacements {
            let start = (replacement.start as isize + delta) as usize;
            let end = (replacement.end as isize + delta) as usize;
            let old_match_len = end - start;
            let new_match_len = replacement.bytes.len();
            let tail_start = end;

            if new_match_len > old_match_len {
                let grow_by = new_match_len - old_match_len;
                mmap.copy_within(tail_start..current_len, tail_start + grow_by);
                current_len += grow_by;
                delta += grow_by as isize;
            } else if new_match_len < old_match_len {
                let shrink_by = old_match_len - new_match_len;
                mmap.copy_within(tail_start..current_len, tail_start - shrink_by);
                current_len -= shrink_by;
                delta -= shrink_by as isize;
            }

            let write_end = start + new_match_len;
            mmap[start..write_end].copy_from_slice(&replacement.bytes);
        }

        mmap.flush()?;
        Ok(())
    }

    pub fn count_matches(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
    ) -> anyhow::Result<usize> {
        let path_obj = Path::new(path);
        let mut total_count = 0;

        // Extreme fast path: fixed strings, case sensitive, use memmem
        if fixed_strings && !ignore_case {
            let pat_bytes = pattern.as_bytes();
            if path_obj.is_file() {
                if let Ok(count) =
                    self.count_file_memmem(pat_bytes, &path_obj.to_path_buf(), invert_match)
                {
                    total_count += count;
                }
            } else if path_obj.is_dir() {
                for entry in WalkDir::new(path_obj).into_iter().filter_map(|e| e.ok()) {
                    if entry.file_type().is_file() {
                        if let Ok(count) = self.count_file_memmem(
                            pat_bytes,
                            &entry.path().to_path_buf(),
                            invert_match,
                        ) {
                            total_count += count;
                        }
                    }
                }
            }
            return Ok(total_count);
        }

        let re = if fixed_strings {
            RegexBuilder::new(&regex::escape(pattern))
                .case_insensitive(ignore_case)
                .build()?
        } else {
            RegexBuilder::new(pattern)
                .case_insensitive(ignore_case)
                .build()?
        };

        if path_obj.is_file() {
            if let Ok(count) = self.count_file_regex(&re, &path_obj.to_path_buf(), invert_match) {
                total_count += count;
            }
        } else if path_obj.is_dir() {
            for entry in WalkDir::new(path_obj).into_iter().filter_map(|e| e.ok()) {
                if entry.file_type().is_file() {
                    if let Ok(count) =
                        self.count_file_regex(&re, &entry.path().to_path_buf(), invert_match)
                    {
                        total_count += count;
                    }
                }
            }
        }

        Ok(total_count)
    }

    fn count_file_memmem(
        &self,
        pattern: &[u8],
        path: &PathBuf,
        invert_match: bool,
    ) -> anyhow::Result<usize> {
        let file = File::open(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        // For ripgrep parity on count matches, we count MATCHING LINES, not total occurrences.
        // But for massive speedup without line splitting, we can use par_split
        let count = mmap
            .par_split(|&b| b == b'\n')
            .filter(|line_bytes| !line_bytes.is_empty()) // Prevent counting trailing empty split
            .filter(|line_bytes| {
                let is_match = memmem::find(line_bytes, pattern).is_some();
                if invert_match {
                    !is_match
                } else {
                    is_match
                }
            })
            .count();

        Ok(count)
    }

    fn count_file_regex(
        &self,
        re: &regex::bytes::Regex,
        path: &PathBuf,
        invert_match: bool,
    ) -> anyhow::Result<usize> {
        let file = File::open(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        let count = mmap
            .par_split(|&b| b == b'\n')
            .filter(|line_bytes| !line_bytes.is_empty()) // Prevent counting trailing empty split
            .filter(|line_bytes| {
                let is_match = re.is_match(line_bytes);
                if invert_match {
                    !is_match
                } else {
                    is_match
                }
            })
            .count();

        Ok(count)
    }
}
