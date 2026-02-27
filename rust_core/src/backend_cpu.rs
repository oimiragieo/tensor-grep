use memchr::memmem;
use memmap2::MmapOptions;
use rayon::prelude::*;
use regex::bytes::RegexBuilder;
use std::fs::File;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

pub struct CpuBackend;

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
    ) -> anyhow::Result<Vec<(usize, String)>> {
        let re = if fixed_strings {
            RegexBuilder::new(&regex::escape(pattern))
                .case_insensitive(ignore_case)
                .build()?
        } else {
            RegexBuilder::new(pattern)
                .case_insensitive(ignore_case)
                .build()?
        };

        let path_obj = Path::new(path);
        let mut results = Vec::new();

        if path_obj.is_file() {
            if let Ok(file_results) = self.search_file(&re, &path_obj.to_path_buf()) {
                results.extend(file_results);
            }
        } else if path_obj.is_dir() {
            for entry in WalkDir::new(path_obj).into_iter().filter_map(|e| e.ok()) {
                if entry.file_type().is_file() {
                    if let Ok(file_results) = self.search_file(&re, &entry.path().to_path_buf()) {
                        results.extend(file_results);
                    }
                }
            }
        }

        Ok(results)
    }

    fn search_file(
        &self,
        re: &regex::bytes::Regex,
        path: &PathBuf,
    ) -> anyhow::Result<Vec<(usize, String)>> {
        let file = File::open(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        let mut results = Vec::new();
        let mut line_num = 1;
        let mut start = 0;

        for (i, &byte) in mmap.iter().enumerate() {
            if byte == b'\n' {
                let line_bytes = &mmap[start..i];
                if re.is_match(line_bytes)
                    && let Ok(line_str) = std::str::from_utf8(line_bytes)
                {
                    results.push((line_num, line_str.to_string()));
                }
                start = i + 1;
                line_num += 1;
            }
        }

        if start < mmap.len() {
            let line_bytes = &mmap[start..];
            if re.is_match(line_bytes)
                && let Ok(line_str) = std::str::from_utf8(line_bytes)
            {
                results.push((line_num, line_str.to_string()));
            }
        }

        Ok(results)
    }

    pub fn count_matches(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> anyhow::Result<usize> {
        let path_obj = Path::new(path);
        let mut total_count = 0;

        // Extreme fast path: fixed strings, case sensitive, use memmem
        if fixed_strings && !ignore_case {
            let pat_bytes = pattern.as_bytes();
            if path_obj.is_file() {
                if let Ok(count) = self.count_file_memmem(pat_bytes, &path_obj.to_path_buf()) {
                    total_count += count;
                }
            } else if path_obj.is_dir() {
                for entry in WalkDir::new(path_obj).into_iter().filter_map(|e| e.ok()) {
                    if entry.file_type().is_file() {
                        if let Ok(count) =
                            self.count_file_memmem(pat_bytes, &entry.path().to_path_buf())
                        {
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
            if let Ok(count) = self.count_file_regex(&re, &path_obj.to_path_buf()) {
                total_count += count;
            }
        } else if path_obj.is_dir() {
            for entry in WalkDir::new(path_obj).into_iter().filter_map(|e| e.ok()) {
                if entry.file_type().is_file() {
                    if let Ok(count) = self.count_file_regex(&re, &entry.path().to_path_buf()) {
                        total_count += count;
                    }
                }
            }
        }

        Ok(total_count)
    }

    fn count_file_memmem(&self, pattern: &[u8], path: &PathBuf) -> anyhow::Result<usize> {
        let file = File::open(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        // For ripgrep parity on count matches, we count MATCHING LINES, not total occurrences.
        // But for massive speedup without line splitting, we can use par_split
        let count = mmap
            .par_split(|&b| b == b'\n')
            .filter(|line_bytes| memmem::find(line_bytes, pattern).is_some())
            .count();

        Ok(count)
    }

    fn count_file_regex(&self, re: &regex::bytes::Regex, path: &PathBuf) -> anyhow::Result<usize> {
        let file = File::open(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        let count = mmap
            .par_split(|&b| b == b'\n')
            .filter(|line_bytes| re.is_match(line_bytes))
            .count();

        Ok(count)
    }
}
