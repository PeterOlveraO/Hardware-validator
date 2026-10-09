//! Command-line parsing. Invalid arguments exit with code 2.

use crate::cpu_test::{Config, parse_duration, parse_load, parse_temperature};

const MAIN_USAGE: &str = "usage: hardware-validator [-h] [--verbose] [--version] {test} ...";
const TEST_USAGE: &str = "usage: hardware-validator test [-h] {cpu} ...";
const CPU_USAGE: &str = "usage: hardware-validator test cpu [-h] [--duration DURATION] [--load PERCENT] [--max-temperature CELSIUS] [--allow-no-temperature] [--verbose]";

const MAIN_HELP: &str = "\
Print a non-invasive local Linux hardware inventory or run an explicit CPU test.

positional arguments:
  {test}
    test       run an explicit hardware test

options:
  -h, --help   show this help message and exit
  --verbose    show the complete technical inventory report
  --version    show program's version number and exit";

const TEST_HELP: &str = "\
positional arguments:
  {cpu}
    cpu       run the moderate CPU load test

options:
  -h, --help  show this help message and exit";

const CPU_HELP: &str = "\
options:
  -h, --help            show this help message and exit
  --duration DURATION   test duration from 10s through 24h (default: 10m)
  --load PERCENT        worker duty cycle from 10 through 90 (default: 60)
  --max-temperature CELSIUS
                        thermal safeguard from 50 through 100 C (default: 85)
  --allow-no-temperature
                        run with a warning when no usable temperature sensor exists
  --verbose             show per-sample CPU, frequency, sensor, and worker details";

#[derive(Debug, Clone, PartialEq)]
pub enum Command {
    /// Text for stdout, exit code 0.
    Help(String),
    Version,
    Inventory {
        verbose: bool,
    },
    CpuTest(Config),
}

/// A usage error: the full message for stderr.
fn usage_error(usage: &str, prog: &str, message: &str) -> String {
    format!("{usage}\n{prog}: error: {message}")
}

pub fn parse(arguments: &[String]) -> Result<Command, String> {
    let mut verbose = false;
    let mut rest = arguments.iter();
    while let Some(argument) = rest.next() {
        match argument.as_str() {
            "-h" | "--help" => return Ok(Command::Help(format!("{MAIN_USAGE}\n\n{MAIN_HELP}"))),
            "--version" => return Ok(Command::Version),
            "--verbose" => verbose = true,
            "test" => return parse_test(rest.as_slice(), verbose),
            other => {
                let message = format!("unrecognized arguments: {other}");
                return Err(usage_error(MAIN_USAGE, "hardware-validator", &message));
            }
        }
    }
    Ok(Command::Inventory { verbose })
}

fn parse_test(arguments: &[String], verbose: bool) -> Result<Command, String> {
    let prog = "hardware-validator test";
    match arguments.first().map(String::as_str) {
        Some("-h" | "--help") => Ok(Command::Help(format!("{TEST_USAGE}\n\n{TEST_HELP}"))),
        Some("cpu") => parse_cpu(&arguments[1..], verbose),
        Some(other) => {
            let message = format!("argument {{cpu}}: invalid choice: '{other}' (choose from cpu)");
            Err(usage_error(TEST_USAGE, prog, &message))
        }
        None => Err(usage_error(TEST_USAGE, prog, "the following arguments are required: {cpu}")),
    }
}

fn parse_cpu(arguments: &[String], verbose: bool) -> Result<Command, String> {
    let prog = "hardware-validator test cpu";
    let error = |message: String| usage_error(CPU_USAGE, prog, &message);
    let mut config = Config { verbose, ..Config::default() };
    let mut rest = arguments.iter();
    while let Some(argument) = rest.next() {
        let (option, inline) = match argument.split_once('=') {
            Some((option, value)) if option.starts_with("--") => (option, Some(value.to_owned())),
            _ => (argument.as_str(), None),
        };
        match option {
            "-h" | "--help" => return Ok(Command::Help(format!("{CPU_USAGE}\n\n{CPU_HELP}"))),
            "--allow-no-temperature" => config.allow_no_temperature = true,
            "--verbose" => config.verbose = true,
            "--duration" | "--load" | "--max-temperature" => {
                let Some(value) = inline.or_else(|| rest.next().cloned()) else {
                    return Err(error(format!("argument {option}: expected one argument")));
                };
                if let Err(message) = apply(&mut config, option, &value) {
                    return Err(error(format!("argument {option} {}: {message}", metavar(option))));
                }
            }
            other => return Err(error(format!("unrecognized arguments: {other}"))),
        }
    }
    Ok(Command::CpuTest(config))
}

fn metavar(option: &str) -> &'static str {
    match option {
        "--duration" => "DURATION",
        "--load" => "PERCENT",
        _ => "CELSIUS",
    }
}

fn apply(config: &mut Config, option: &str, value: &str) -> Result<(), String> {
    match option {
        "--duration" => config.duration_seconds = parse_duration(value)?,
        "--load" => config.load_percent = parse_load(value)?,
        _ => config.maximum_temperature_celsius = parse_temperature(value)?,
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse_args(arguments: &[&str]) -> Result<Command, String> {
        parse(&arguments.iter().map(|argument| argument.to_string()).collect::<Vec<_>>())
    }

    #[test]
    fn inventory_help_and_version() {
        assert_eq!(parse_args(&[]), Ok(Command::Inventory { verbose: false }));
        assert_eq!(parse_args(&["--verbose"]), Ok(Command::Inventory { verbose: true }));
        assert_eq!(parse_args(&["--version"]), Ok(Command::Version));
        assert!(matches!(parse_args(&["-h"]), Ok(Command::Help(text)) if text.contains("test")));
        let error = parse_args(&["--bogus"]).unwrap_err();
        assert!(error.ends_with("hardware-validator: error: unrecognized arguments: --bogus"));
    }

    #[test]
    fn cpu_test_options_are_normalized() {
        assert_eq!(parse_args(&["test", "cpu"]), Ok(Command::CpuTest(Config::default())));
        let expected = Config {
            duration_seconds: 30,
            load_percent: 75,
            maximum_temperature_celsius: 80.5,
            allow_no_temperature: true,
            verbose: true,
        };
        let arguments = [
            "--verbose",
            "test",
            "cpu",
            "--duration",
            "30s",
            "--load=75",
            "--max-temperature",
            "80.5",
            "--allow-no-temperature",
        ];
        assert_eq!(parse_args(&arguments), Ok(Command::CpuTest(expected)));
        assert!(matches!(parse_args(&["test", "cpu", "--help"]), Ok(Command::Help(_))));
    }

    #[test]
    fn invalid_cpu_test_arguments_are_usage_errors() {
        let error = parse_args(&["test", "cpu", "--load", "100"]).unwrap_err();
        assert!(error.starts_with(CPU_USAGE));
        assert!(error.ends_with("argument --load PERCENT: load must be between 10 and 90"));
        assert!(parse_args(&["test", "cpu", "--duration"]).unwrap_err().contains("expected one"));
        assert!(parse_args(&["test", "gpu"]).unwrap_err().contains("invalid choice: 'gpu'"));
        assert!(parse_args(&["test"]).unwrap_err().contains("required: {cpu}"));
        assert!(parse_args(&["test", "cpu", "extra"]).unwrap_err().contains("extra"));
    }
}
