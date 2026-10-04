//! Partial-apply reporting for `tg run --apply` (bug hunt 2026-10-03, J-04).

use std::fmt;
use std::path::PathBuf;

#[derive(Debug)]
pub struct PartialApplyError {
    pub files_written: Vec<PathBuf>,
    pub files_failed: Vec<(PathBuf, String)>,
}

impl fmt::Display for PartialApplyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(
            f,
            "partial apply: {} file(s) written, {} file(s) failed; written files were NOT rolled \
             back, and FAILED files may be PARTIALLY modified (writes truncate in place) -- \
             inspect and restore every listed file from version control before re-running",
            self.files_written.len(),
            self.files_failed.len()
        )?;
        writeln!(f, "files_written:")?;
        for path in &self.files_written {
            writeln!(f, "  {}", path.display())?;
        }
        writeln!(f, "files_failed:")?;
        for (path, error) in &self.files_failed {
            writeln!(f, "  {}: {error}", path.display())?;
        }
        Ok(())
    }
}

impl std::error::Error for PartialApplyError {}

/// `wrote(&value)` says whether an Ok outcome actually modified its file.
pub fn partition_apply_results<T>(
    outcomes: Vec<(PathBuf, anyhow::Result<T>)>,
    wrote: impl Fn(&T) -> bool,
) -> anyhow::Result<Vec<T>> {
    partition_apply_results_with_prior(&[], outcomes, wrote)
}

/// Like [`partition_apply_results`], but `prior_written` lists files an EARLIER batch group
/// already wrote, so a later group's failure still reports them.
pub fn partition_apply_results_with_prior<T>(
    prior_written: &[PathBuf],
    outcomes: Vec<(PathBuf, anyhow::Result<T>)>,
    wrote: impl Fn(&T) -> bool,
) -> anyhow::Result<Vec<T>> {
    let (mut values, mut written, mut failed) = (Vec::new(), prior_written.to_vec(), Vec::new());
    for (path, result) in outcomes {
        match result {
            Ok(value) => {
                if wrote(&value) {
                    written.push(path);
                }
                values.push(value);
            }
            Err(err) => failed.push((path, err)),
        }
    }
    if failed.is_empty() {
        return Ok(values);
    }
    if written.is_empty() && failed.len() == 1 {
        // "Nothing written" does not prove the failed file is intact (writes truncate in place):
        // keep the original message FIRST (existing substring pins) and warn.
        let (path, err) = failed.remove(0);
        return Err(anyhow::anyhow!(
            "{err:#}\n{} may be PARTIALLY modified -- restore it from version control before re-running",
            path.display()
        ));
    }
    Err(PartialApplyError {
        files_written: written,
        files_failed: failed
            .into_iter()
            .map(|(path, err)| (path, format!("{err:#}")))
            .collect(),
    }
    .into())
}

/// One batch group: pairs `files` with `results`, reports through `written_so_far`, and on
/// success appends this group's writes so a LATER group's failure can name them (J-04).
pub fn collect_group_results<T>(
    files: &[PathBuf],
    results: Vec<anyhow::Result<T>>,
    written_so_far: &mut Vec<PathBuf>,
    wrote: impl Fn(&T) -> bool,
) -> anyhow::Result<Vec<T>> {
    let outcomes = files.iter().cloned().zip(results).collect();
    let values = partition_apply_results_with_prior(written_so_far, outcomes, &wrote)?;
    let wrote_here = files.iter().zip(&values).filter(|(_, v)| wrote(v));
    written_so_far.extend(wrote_here.map(|(file, _)| file.clone()));
    Ok(values)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    fn p(n: &str) -> PathBuf {
        PathBuf::from(n)
    }

    #[test]
    fn mixed_results_report_written_and_failed_files() {
        let outcomes = vec![
            (p("a.py"), Ok(true)),
            (p("b.py"), Err(anyhow::anyhow!("Access is denied"))),
            (p("c.py"), Ok(true)),
            (p("d.py"), Ok(false)),
        ];
        let err = partition_apply_results(outcomes, |wrote| *wrote).unwrap_err();
        let partial = err
            .downcast_ref::<PartialApplyError>()
            .expect("typed partial error");
        assert_eq!(partial.files_written, vec![p("a.py"), p("c.py")]);
        assert_eq!(partial.files_failed.len(), 1);
        let msg = format!("{err:#}");
        assert!(
            msg.contains("2 file(s) written")
                && msg.contains("b.py")
                && msg.contains("Access is denied")
                && msg.contains("NOT rolled back")
                && msg.contains("PARTIALLY modified"),
            "{msg}"
        );
    }

    #[test]
    fn lone_failure_keeps_the_original_error_first_and_warns_partial() {
        let err = partition_apply_results(
            vec![(
                p("b.py"),
                Err::<bool, _>(anyhow::anyhow!("invalid UTF-8 in b.py")),
            )],
            |w| *w,
        )
        .unwrap_err();
        assert!(err.downcast_ref::<PartialApplyError>().is_none());
        let msg = format!("{err}");
        assert!(msg.starts_with("invalid UTF-8 in b.py"), "{msg}");
        assert!(msg.contains("b.py may be PARTIALLY modified"), "{msg}");
    }

    #[test]
    fn all_ok_returns_values_in_order() {
        let out =
            partition_apply_results(vec![(p("a"), Ok(1)), (p("b"), Ok(2))], |_| true).unwrap();
        assert_eq!(out, vec![1, 2]);
    }

    #[test]
    fn later_group_failure_reports_earlier_group_writes() {
        let prior = vec![p("a.py")];
        let err = partition_apply_results_with_prior(
            &prior,
            vec![(
                p("b.rs"),
                Err::<bool, _>(anyhow::anyhow!("Access is denied")),
            )],
            |w| *w,
        )
        .unwrap_err();
        let partial = err
            .downcast_ref::<PartialApplyError>()
            .expect("earlier writes force the typed error");
        assert_eq!(partial.files_written, vec![p("a.py")]);
        assert_eq!(partial.files_failed.len(), 1);
    }

    #[test]
    fn all_groups_succeed_control() {
        let prior = vec![p("a.py")];
        assert!(
            partition_apply_results_with_prior(&prior, vec![(p("b.rs"), Ok(true))], |w| *w).is_ok()
        );
    }

    #[test]
    fn collect_group_results_accumulates_writes_across_groups() {
        let mut written = Vec::new();
        let first = vec![p("a.py"), p("skip.py")];
        let ok = collect_group_results(&first, vec![Ok(true), Ok(false)], &mut written, |w| *w);
        assert_eq!(ok.unwrap(), vec![true, false]);
        assert_eq!(written, vec![p("a.py")], "only files that were written");

        let second = vec![p("b.rs")];
        let failing = vec![Err::<bool, _>(anyhow::anyhow!("Access is denied"))];
        let err = collect_group_results(&second, failing, &mut written, |w| *w).unwrap_err();
        let partial = err
            .downcast_ref::<PartialApplyError>()
            .expect("typed error");
        assert_eq!(partial.files_written, vec![p("a.py")]);
        assert_eq!(partial.files_failed.len(), 1);
    }
}
