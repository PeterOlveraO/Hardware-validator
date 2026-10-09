//! Command-line entry point.

use std::io::{self, Write};
use std::process::ExitCode;

use hardware_validator::collector::collect_inventory;
use hardware_validator::host::Host;
use hardware_validator::report::{render_inventory, render_summary};

const USAGE: &str = "usage: hardware-validator [-h] [--verbose] [--version]";

const HELP: &str = "\
usage: hardware-validator [-h] [--verbose] [--version]

Print a non-invasive local Linux hardware inventory.

options:
  -h, --help  show this help message and exit
  --verbose   show the complete technical inventory report
  --version   show program's version number and exit";

fn main() -> ExitCode {
    let mut verbose = false;
    for argument in std::env::args().skip(1) {
        match argument.as_str() {
            "-h" | "--help" => return print(HELP),
            "--version" => return print(env!("CARGO_PKG_VERSION")),
            "--verbose" => verbose = true,
            other => {
                eprintln!("{USAGE}\nhardware-validator: error: unrecognized arguments: {other}");
                return ExitCode::from(2);
            }
        }
    }

    let results = collect_inventory(&Host::local());
    let report = if verbose { render_inventory(&results) } else { render_summary(&results) };
    if let Err(error) = writeln!(io::stdout().lock(), "{report}") {
        eprintln!("Unable to write the hardware inventory: {error}");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

fn print(text: &str) -> ExitCode {
    match writeln!(io::stdout().lock(), "{text}") {
        Ok(()) => ExitCode::SUCCESS,
        Err(_) => ExitCode::FAILURE,
    }
}
