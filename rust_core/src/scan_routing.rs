use std::ffi::OsString;

pub(crate) fn config_only_path(args: &[String]) -> Option<Option<String>> {
    let mut config = None;
    let mut index = 0;
    while index < args.len() {
        let argument = &args[index];
        if argument == "--config" || argument == "-c" {
            if config.is_some() || index + 1 >= args.len() || args[index + 1].is_empty() {
                return None;
            }
            config = Some(args[index + 1].clone());
            index += 2;
        } else if let Some(value) = argument.strip_prefix("--config=") {
            if config.is_some() || value.is_empty() {
                return None;
            }
            config = Some(value.to_string());
            index += 1;
        } else {
            return None;
        }
    }
    Some(config)
}

pub(crate) fn scan_args_with_separator(raw_args: &[OsString]) -> Option<Vec<String>> {
    if raw_args.get(1)?.to_str()? != "scan" {
        return None;
    }
    let args = raw_args.get(2..)?;
    let mut index = 0;
    while index < args.len() {
        match args[index].to_str()? {
            "--" => {
                return Some(
                    args.iter()
                        .map(|arg| arg.to_string_lossy().into_owned())
                        .collect(),
                )
            }
            "--config" | "-c" if index + 1 < args.len() => {
                if args[index + 1].to_str() == Some("--") {
                    return Some(
                        args.iter()
                            .map(|arg| arg.to_string_lossy().into_owned())
                            .collect(),
                    );
                }
                index += 2;
            }
            argument if argument.starts_with("--config=") => index += 1,
            _ => index += 1,
        }
    }
    None
}
