use std::ffi::OsString;
use std::path::PathBuf;

use clap::{Args, Parser, Subcommand, ValueEnum};

#[derive(Debug, Parser)]
#[command(
    name = "chatwaifu-appcontainer-host",
    about = "Run stdio MCP plugins in a Windows AppContainer",
    disable_version_flag = true
)]
pub(crate) struct Cli {
    #[command(subcommand)]
    pub(crate) command: Command,
}

#[derive(Debug, Subcommand)]
pub(crate) enum Command {
    /// Create/reuse a stable AppContainer profile and run one child process.
    Run(RunArgs),
    /// Revoke this profile's filesystem ACEs and delete its profile and journal.
    Revoke(ProfileArgs),
    /// Revoke journals that are not present in the active profile set.
    Reconcile(ReconcileArgs),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub(crate) enum NetworkPolicy {
    Deny,
    Allow,
}

#[derive(Debug, Args)]
pub(crate) struct RunArgs {
    #[arg(long)]
    pub(crate) profile_name: String,

    #[arg(long)]
    pub(crate) state_dir: PathBuf,

    #[arg(long)]
    pub(crate) cwd: PathBuf,

    #[arg(long)]
    pub(crate) read_only: Vec<PathBuf>,

    #[arg(long)]
    pub(crate) writable: Vec<PathBuf>,

    #[arg(long, value_enum)]
    pub(crate) network: NetworkPolicy,

    #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
    pub(crate) memory_bytes: u64,

    #[arg(long, value_parser = clap::value_parser!(u32).range(1..))]
    pub(crate) max_processes: u32,

    /// Absolute executable followed by arguments. Values are passed to CreateProcessW, not a shell.
    #[arg(last = true, required = true, num_args = 1..)]
    pub(crate) child: Vec<OsString>,
}

#[derive(Debug, Args)]
pub(crate) struct ProfileArgs {
    #[arg(long)]
    pub(crate) profile_name: String,

    #[arg(long)]
    pub(crate) state_dir: PathBuf,
}

#[derive(Debug, Args)]
pub(crate) struct ReconcileArgs {
    #[arg(long)]
    pub(crate) state_dir: PathBuf,

    #[arg(long)]
    pub(crate) active_profile_name: Vec<String>,
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use clap::Parser;

    use super::{Cli, Command, NetworkPolicy};

    #[test]
    fn parses_run_without_reinterpreting_child_arguments() {
        let cli = Cli::try_parse_from([
            "host",
            "run",
            "--profile-name",
            "ChatWaifu.Plugin.example",
            "--state-dir",
            r"C:\trusted\state",
            "--cwd",
            r"C:\plugins\example-data",
            "--read-only",
            r"C:\plugins\example",
            "--writable",
            r"C:\plugins\example-data",
            "--network",
            "deny",
            "--memory-bytes",
            "536870912",
            "--max-processes",
            "8",
            "--",
            r"C:\runtime\python.exe",
            "-I",
            "entry.py",
            "--plugin-flag",
        ])
        .expect("valid run command");

        let Command::Run(run) = cli.command else {
            panic!("expected run command");
        };
        assert_eq!(run.network, NetworkPolicy::Deny);
        assert_eq!(run.cwd, PathBuf::from(r"C:\plugins\example-data"));
        assert_eq!(run.child[1], "-I");
        assert_eq!(run.child[3], "--plugin-flag");
    }

    #[test]
    fn refuses_run_without_a_child_command() {
        let result = Cli::try_parse_from([
            "host",
            "run",
            "--profile-name",
            "ChatWaifu.Plugin.example",
            "--state-dir",
            r"C:\trusted\state",
            "--cwd",
            r"C:\plugins\example-data",
            "--network",
            "deny",
            "--memory-bytes",
            "1",
            "--max-processes",
            "1",
        ]);
        assert!(result.is_err());
    }

    #[test]
    fn parses_reconcile_active_profiles() {
        let cli = Cli::try_parse_from([
            "host",
            "reconcile",
            "--state-dir",
            r"C:\trusted\state",
            "--active-profile-name",
            "ChatWaifu.Plugin.a",
            "--active-profile-name",
            "ChatWaifu.Plugin.b",
        ])
        .expect("valid reconcile command");

        let Command::Reconcile(args) = cli.command else {
            panic!("expected reconcile command");
        };
        assert_eq!(args.active_profile_name.len(), 2);
    }
}
