use super::write_owner_record;
use std::io::{self, Write};

#[test]
fn token_format_is_attempted_after_pid_error_and_first_error_is_retained() {
    struct RecordingWriter {
        attempts: Vec<String>,
    }

    impl Write for RecordingWriter {
        fn write(&mut self, _bytes: &[u8]) -> io::Result<usize> {
            panic!("this control observes formatted attempts, not byte writes");
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }

        fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> io::Result<()> {
            self.attempts.push(args.to_string());
            let code = if self.attempts.len() == 1 { 5 } else { 13 };
            Err(io::Error::from_raw_os_error(code))
        }
    }

    let first_error = io::Error::from_raw_os_error(5);
    let second_error = io::Error::from_raw_os_error(13);
    assert_ne!(first_error.kind(), io::ErrorKind::Interrupted);
    assert_ne!(second_error.kind(), io::ErrorKind::Interrupted);
    let mut writer = RecordingWriter {
        attempts: Vec::new(),
    };

    let error = write_owner_record(&mut writer, "test-token").unwrap_err();

    assert_eq!(
        writer.attempts,
        vec![format!("{}\n", std::process::id()), "test-token\n".into()]
    );
    assert_eq!(error.raw_os_error(), first_error.raw_os_error());
    assert_eq!(error.kind(), first_error.kind());
    assert_eq!(error.to_string(), first_error.to_string());
}

#[test]
fn writable_file_contains_exact_pid_and_token_lines() {
    let mut file = tempfile::tempfile().unwrap();

    write_owner_record(&mut file, "test-token").unwrap();

    use std::io::{Read, Seek};
    file.rewind().unwrap();
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes).unwrap();
    assert_eq!(
        bytes,
        format!("{}\ntest-token\n", std::process::id()).into_bytes()
    );
}
