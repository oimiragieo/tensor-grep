use clap::{Arg, Command};
use std::{
    ffi::OsString,
    io::{self, Write},
};

const SUMMARY: &str = "Search code, follow symbols, and prepare a bounded edit.";
const EXAMPLES: &[&str] = &[
    "tg search -F TODO src",
    "tg callers src create_invoice --json",
    "tg prepare src \"add invoice tax\" --json",
    "tg mcp",
];
const FOOTER: &[&str] = &[
    "Search supports a validated rg-compatible subset. GPU execution is experimental.",
    "Use tg COMMAND --help for details.",
];
const WIDTH: usize = 78;
const GROUPS: &[(&str, &str)] = &[
    (
        "Search and structure",
        "search find run scan test ast-info new classify sql",
    ),
    (
        "Navigate",
        concat!(
            "defs refs source impact callers imports importers blast-radius ",
            "blast-radius-render blast-radius-plan file-api"
        ),
    ),
    (
        "Agent",
        concat!(
            "agent prepare edit-plan diff-impact route-test map orient codemap ",
            "context context-render inventory docs-coverage"
        ),
    ),
    (
        "State and evidence",
        concat!(
            "session checkpoint freshness coverage rulesets audit audit-verify ",
            "audit-history audit-diff review-bundle evidence ledger"
        ),
    ),
    (
        "Operations",
        concat!(
            "calibrate upgrade update repair-launcher dogfood devices lsp lsp-setup ",
            "install-dense install uninstall repair-env doctor mcp"
        ),
    ),
];

pub fn is_root_help_invocation(args: &[OsString]) -> bool {
    args.len() == 1 || (args.len() == 2 && matches!(args[1].to_str(), Some("--help" | "-h")))
}

pub fn print_help(command: &Command, writer: &mut impl Write) -> io::Result<()> {
    let mut root = command.clone();
    root.build();
    writeln!(writer, "{}\n\n{SUMMARY}\n", root.render_usage())?;
    writeln!(writer, "Examples:")?;
    for example in EXAMPLES {
        writeln!(writer, "{example}")?;
    }
    writeln!(writer, "\nOptions:")?;
    for arg in root.get_arguments().filter(|arg| !arg.is_hide_set()) {
        write_row(
            &arg_label(arg),
            &plain(arg.get_help().map(ToString::to_string).unwrap_or_default()),
            writer,
        )?;
    }
    let mut visible: Vec<_> = root
        .get_subcommands()
        .filter(|cmd| !cmd.is_hide_set())
        .collect();
    visible.sort_by(|left, right| {
        let left_search = left.get_name() == "search";
        let right_search = right.get_name() == "search";
        right_search
            .cmp(&left_search)
            .then_with(|| left.get_name().cmp(right.get_name()))
    });
    for (index, (heading, names)) in GROUPS.iter().enumerate() {
        writeln!(writer, "\n{heading}:")?;
        for cmd in &visible {
            let name = cmd.get_name();
            let assigned = contains_name(names, name);
            let future = index == GROUPS.len() - 1
                && !GROUPS[..index]
                    .iter()
                    .any(|(_, group)| contains_name(group, name))
                && !assigned;
            if assigned || future {
                write_row(
                    &command_label(cmd),
                    &plain(cmd.get_about().map(ToString::to_string).unwrap_or_default()),
                    writer,
                )?;
            }
        }
    }
    writeln!(writer, "\n{}", FOOTER.join("\n"))
}

fn contains_name(names: &str, name: &str) -> bool {
    names.split_ascii_whitespace().any(|item| item == name)
}

fn arg_label(arg: &Arg) -> String {
    let short = arg.get_short().map(|ch| format!("-{ch}"));
    let long = arg.get_long().map(|name| format!("--{name}"));
    [short, long]
        .into_iter()
        .flatten()
        .collect::<Vec<_>>()
        .join(", ")
}

fn command_label(cmd: &Command) -> String {
    let aliases: Vec<_> = cmd.get_visible_aliases().collect();
    if aliases.is_empty() {
        cmd.get_name().to_owned()
    } else {
        format!("{}  [aliases: {}]", cmd.get_name(), aliases.join(", "))
    }
}

fn plain(text: String) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .replace("**", "")
        .replace('`', "")
}

fn write_row(label: &str, description: &str, writer: &mut impl Write) -> io::Result<()> {
    let lead = if label.is_empty() {
        String::new()
    } else {
        format!("  {}  ", label.chars().take(WIDTH - 5).collect::<String>())
    };
    let max = WIDTH.saturating_sub(lead.chars().count());
    let mut short = description.to_owned();
    if short.chars().count() > max {
        short = format!(
            "{}…",
            short
                .chars()
                .take(max.saturating_sub(1))
                .collect::<String>()
                .trim_end()
        );
    }
    writeln!(writer, "{lead}{short}")
}

#[cfg(test)]
mod tests {
    use super::{print_help, EXAMPLES, FOOTER, GROUPS, WIDTH};
    use crate::CommandCli;
    use clap::CommandFactory;

    fn render(command: &clap::Command) -> String {
        let mut bytes = Vec::new();
        print_help(command, &mut bytes).expect("render root help");
        String::from_utf8(bytes).expect("UTF-8 help")
    }
    fn visible_rows_once(command: &clap::Command, output: &str) -> bool {
        let mut root = command.clone();
        root.build();
        root.get_subcommands()
            .filter(|cmd| !cmd.is_hide_set())
            .all(|cmd| {
                let row = format!("  {} ", super::command_label(cmd));
                output.matches(row.as_str()).count() == 1
            })
    }
    fn group_ordered(output: &str) -> bool {
        let positions: Vec<_> = GROUPS
            .iter()
            .filter_map(|(name, _)| output.find(&format!("\n{name}:\n")))
            .collect();
        positions.len() == GROUPS.len() && positions.windows(2).all(|pair| pair[0] < pair[1])
    }

    #[test]
    fn test_root_help__action_render__condition_visible_commands__expected_outcome_once_and_hidden_omitted(
    ) {
        let output = render(&CommandCli::command());
        assert!(
            visible_rows_once(&CommandCli::command(), &output),
            "{output}"
        );
        assert!(
            output.contains("\nSearch and structure:\n  search  "),
            "{output}"
        );
        assert!(
            !output.contains("__gpu-") && !output.contains("worker"),
            "{output}"
        );
    }
    #[test]
    fn test_root_help__action_render__condition_groups__expected_outcome_ordered_with_operations_fallback(
    ) {
        let output = render(&CommandCli::command());
        assert!(group_ordered(&output), "{output}");
        let command = clap::Command::new("tg")
            .subcommand(clap::Command::new("future-visible").about("A future command description"));
        let future = render(&command);
        assert!(
            future.contains("\nOperations:\n  future-visible"),
            "{future}"
        );
    }
    #[test]
    fn test_root_help__action_render__condition_plain_width__expected_outcome_copyable_examples_and_usage(
    ) {
        let output = render(&CommandCli::command());
        assert!(output.starts_with("Usage: tg <COMMAND>\n"), "{output}");
        assert!(output.lines().all(|line| line.chars().count() <= WIDTH));
        assert!(
            !output.contains('`') && !output.contains("**") && !output.contains('\u{1b}'),
            "{output}"
        );
        assert!(
            EXAMPLES
                .iter()
                .all(|example| output.lines().any(|line| line == *example)),
            "{output}"
        );
        assert!(FOOTER
            .iter()
            .all(|line| output.lines().any(|row| row == *line)));
    }
    #[test]
    fn test_root_help__action_render__condition_visible_alias__expected_outcome_single_bounded_row()
    {
        let command = clap::Command::new("tg").subcommand(clap::Command::new("primary").visible_alias("alt").about("A synthetic `long` **description** with enough words to exceed the row width when extended"));
        let output = render(&command);
        assert_eq!(
            output.matches("primary  [aliases: alt]").count(),
            1,
            "{output}"
        );
        assert!(!output.contains("**") && !output.contains('`'), "{output}");
        assert!(
            output.lines().all(|line| line.chars().count() <= WIDTH),
            "{output}"
        );
    }
}
