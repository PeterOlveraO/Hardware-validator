//! Command-line entry point.

use std::io::{self, Write};
use std::process::ExitCode;

use hardware_validator::cli::{self, Command};
use hardware_validator::collector::collect_inventory;
use hardware_validator::cpu_test;
use hardware_validator::host::Host;
use hardware_validator::report::{render_inventory, render_summary};

fn main() -> ExitCode {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let command = match cli::parse(&arguments) {
        Ok(command) => command,
        Err(message) => {
            eprintln!("{message}");
            return ExitCode::from(2);
        }
    };
    match command {
        Command::Help(text) => print(&text),
        Command::Version => print(env!("CARGO_PKG_VERSION")),
        Command::CpuTest(config) => ExitCode::from(cpu_test::run_command(&config)),
        Command::Inventory { verbose } => {
            let results = collect_inventory(&Host::local());
            let report =
                if verbose { render_inventory(&results) } else { render_summary(&results) };
            if let Err(error) = writeln!(io::stdout().lock(), "{report}") {
                eprintln!("Unable to write the hardware inventory: {error}");
                return ExitCode::FAILURE;
            }
            ExitCode::SUCCESS
        }
    }
}

fn print(text: &str) -> ExitCode {
    match writeln!(io::stdout().lock(), "{text}") {
        Ok(()) => ExitCode::SUCCESS,
        Err(_) => ExitCode::FAILURE,
    }
}
