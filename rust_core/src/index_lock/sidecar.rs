use std::io;
use std::path::Path;
use std::time::{Duration, Instant};

pub(super) struct Sidecar;

pub(super) fn acquire(_path: &Path, _deadline: Instant, _poll: Duration) -> io::Result<Sidecar> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "sidecar lock is not implemented",
    ))
}

#[cfg(test)]
mod tests {
    include!("../../tests/support/index_lock_sidecar.rs");
}
