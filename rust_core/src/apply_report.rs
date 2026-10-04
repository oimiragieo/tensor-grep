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
        write!(f, "partial apply")
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
    _prior_written: &[PathBuf],
    _outcomes: Vec<(PathBuf, anyhow::Result<T>)>,
    _wrote: impl Fn(&T) -> bool,
) -> anyhow::Result<Vec<T>> {
    unimplemented!("RED stub: partial-apply reporting")
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
}
