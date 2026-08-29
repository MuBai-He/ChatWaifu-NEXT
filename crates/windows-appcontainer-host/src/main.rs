mod cli;
mod error;
mod journal;
mod platform;

use clap::Parser;

use crate::cli::Cli;

fn main() {
    let cli = Cli::parse();
    match platform::execute(cli.command) {
        Ok(code) => std::process::exit(code),
        Err(error) => {
            // stdout is reserved exclusively for the child MCP byte stream.
            eprintln!("chatwaifu-appcontainer-host: {error}");
            std::process::exit(error.exit_code());
        }
    }
}
