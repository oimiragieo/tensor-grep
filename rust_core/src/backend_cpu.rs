use memchr::memmem;
use memmap2::MmapOptions;
use rayon::prelude::*;
use regex::bytes::RegexBuilder;
use std::fs::{File, OpenOptions};
use std::io::Write;
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
                    if entry.file_type().is_file()
                        && let Ok(file_results) = self.search_file_memmem(
                            pat_bytes,
                            &entry.path().to_path_buf(),
                            invert_match,
                        )
                    {
                        results.extend(file_results);
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
                if entry.file_type().is_file()
                    && let Ok(file_results) =
                        self.search_file_regex(&re, &entry.path().to_path_buf(), invert_match)
                {
                    results.extend(file_results);
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

    fn replace_file_regex(
        &self,
        re: &regex::bytes::Regex,
        replacement: &str,
        path: &PathBuf,
    ) -> anyhow::Result<()> {
        let content = std::fs::read(path)?;

        // If there are no matches, don't touch the file
        if !re.is_match(&content) {
            return Ok(());
        }

        let replaced = re.replace_all(&content, replacement.as_bytes());

        let mut file = OpenOptions::new().write(true).truncate(true).open(path)?;

        file.write_all(&replaced)?;
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
                    if entry.file_type().is_file()
                        && let Ok(count) = self.count_file_memmem(
                            pat_bytes,
                            &entry.path().to_path_buf(),
                            invert_match,
                        )
                    {
                        total_count += count;
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
                if entry.file_type().is_file()
                    && let Ok(count) =
                        self.count_file_regex(&re, &entry.path().to_path_buf(), invert_match)
                {
                    total_count += count;
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
                if invert_match { !is_match } else { is_match }
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
                if invert_match { !is_match } else { is_match }
            })
            .count();

        Ok(count)
    }
}
