//! The explicit, bounded CPU load test. It only runs on request, never
//! changes processor settings, and reports what happened without claiming
//! anything about CPU health.

pub mod engine;
pub mod local;
pub mod presentation;
pub mod temperature;
pub mod workers;

use std::io::{self, Write};

#[derive(Debug, Clone, PartialEq)]
pub struct Config {
    pub duration_seconds: u32,
    pub load_percent: u32,
    pub maximum_temperature_celsius: f64,
    pub allow_no_temperature: bool,
    pub verbose: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            duration_seconds: 600,
            load_percent: 60,
            maximum_temperature_celsius: 85.0,
            allow_no_temperature: false,
            verbose: false,
        }
    }
}

/// `<integer><s|m|h>` from 10 seconds through 24 hours.
pub fn parse_duration(value: &str) -> Result<u32, String> {
    let format_error = || "duration must be an integer followed by s, m, or h".to_owned();
    let Some((split, _)) = value.char_indices().last() else { return Err(format_error()) };
    let (digits, unit) = value.split_at(split);
    let multiplier = match unit {
        "s" => 1,
        "m" => 60,
        "h" => 3600,
        _ => return Err(format_error()),
    };
    if digits.is_empty() || digits.starts_with('0') || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format_error());
    }
    digits
        .parse::<u32>()
        .ok()
        .and_then(|number| number.checked_mul(multiplier))
        .filter(|seconds| (10..=86_400).contains(seconds))
        .ok_or_else(|| "duration must be between 10 seconds and 24 hours".to_owned())
}

/// Requested worker duty cycle in percent, 10 through 90.
pub fn parse_load(value: &str) -> Result<u32, String> {
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err("load must be an integer".to_owned());
    }
    value
        .parse::<u32>()
        .ok()
        .filter(|load| (10..=90).contains(load))
        .ok_or_else(|| "load must be between 10 and 90".to_owned())
}

/// The configurable thermal safeguard, 50 through 100 °C.
pub fn parse_temperature(value: &str) -> Result<f64, String> {
    let celsius: f64 = value
        .trim()
        .parse()
        .ok()
        .filter(|celsius: &f64| celsius.is_finite())
        .ok_or_else(|| "maximum temperature must be a finite number".to_owned())?;
    if !(50.0..=100.0).contains(&celsius) {
        return Err("maximum temperature must be between 50 and 100".to_owned());
    }
    Ok(celsius)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Completed,
    Cancelled,
    TemperatureLimit,
    Error,
}

impl Status {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Cancelled => "cancelled",
            Self::TemperatureLimit => "temperature_limit",
            Self::Error => "error",
        }
    }
}

/// One sensor value as read; `critical_celsius` comes from the sensor itself.
#[derive(Debug, Clone, PartialEq)]
pub struct TemperatureReading {
    pub identity: String,
    pub current_celsius: f64,
    pub critical_celsius: Option<f64>,
}

/// A sensor under watch with its effective limit: the configured safeguard,
/// or the sensor's own critical limit when that is lower.
#[derive(Debug, Clone, PartialEq)]
pub struct MonitoredTemperature {
    pub identity: String,
    pub current_celsius: f64,
    pub limit_celsius: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Ready {
    pub worker_count: u32,
    pub temperatures: Vec<MonitoredTemperature>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Sample {
    pub elapsed_seconds: f64,
    pub total_utilization_percent: f64,
    /// Only collected with `--verbose`.
    pub per_processor_utilization_percent: Option<Vec<f64>>,
    pub aggregate_frequency_hz: Option<f64>,
    /// Only collected with `--verbose`.
    pub per_processor_frequency_hz: Option<Vec<Option<f64>>>,
    pub temperatures: Vec<MonitoredTemperature>,
    pub workers_alive: u32,
    pub worker_count: u32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TestResult {
    pub status: Status,
    pub reason: String,
    pub requested_duration_seconds: u32,
    pub actual_duration_seconds: f64,
    pub requested_load_percent: u32,
    pub worker_count: u32,
    pub sample_count: u32,
    pub utilization_average_percent: Option<f64>,
    pub utilization_minimum_percent: Option<f64>,
    pub utilization_maximum_percent: Option<f64>,
    pub initial_temperature_celsius: Option<f64>,
    pub maximum_temperature_celsius: Option<f64>,
    pub initial_frequency_hz: Option<f64>,
    pub minimum_frequency_hz: Option<f64>,
    pub maximum_frequency_hz: Option<f64>,
    pub warnings: Vec<String>,
}

pub const CTRL_C_REASON: &str = "Interrupted by Ctrl+C.";

/// Runs the test on this machine and maps the outcome to an exit code:
/// 0 completed, 130 Ctrl+C, 1 anything else.
pub fn run_command(config: &Config) -> u8 {
    let processor_model = crate::detectors::cpu::detect(&crate::host::Host::local())
        .value
        .model_names
        .into_iter()
        .next();
    let mut environment = local::LocalEnvironment::install();
    let mut reporter = presentation::ConsoleReporter::new(config, processor_model);
    let result = engine::run(config, &mut environment, &mut reporter);
    drop(environment);
    let rendered = presentation::render_result(&result);
    if let Err(error) = writeln!(io::stdout().lock(), "{rendered}") {
        let _ = writeln!(io::stderr(), "Unable to write the CPU test result: {error}");
        return 1;
    }
    match result.status {
        Status::Completed => 0,
        Status::Cancelled if result.reason == CTRL_C_REASON => 130,
        _ => 1,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duration_units_and_boundaries() {
        assert_eq!(parse_duration("10s"), Ok(10));
        assert_eq!(parse_duration("10m"), Ok(600));
        assert_eq!(parse_duration("24h"), Ok(86_400));
        for invalid in ["9s", "25h", "1441m", "99999999999999999999h"] {
            assert_eq!(
                parse_duration(invalid),
                Err("duration must be between 10 seconds and 24 hours".into()),
                "{invalid}"
            );
        }
        for malformed in ["", "s", "10", "010s", "1.5m", "10 s", "-10s", "10S", "1d", "10é"] {
            assert!(parse_duration(malformed).unwrap_err().contains("integer"), "{malformed}");
        }
    }

    #[test]
    fn load_and_temperature_boundaries() {
        assert_eq!(parse_load("10"), Ok(10));
        assert_eq!(parse_load("90"), Ok(90));
        assert!(parse_load("100").is_err());
        assert!(parse_load("9").is_err());
        assert_eq!(parse_load("6O"), Err("load must be an integer".into()));
        assert_eq!(parse_temperature("85"), Ok(85.0));
        assert_eq!(parse_temperature(" 50.5 "), Ok(50.5));
        assert_eq!(parse_temperature("100"), Ok(100.0));
        assert!(parse_temperature("100.1").is_err());
        assert!(parse_temperature("49").is_err());
        assert!(parse_temperature("nan").unwrap_err().contains("finite"));
        assert!(parse_temperature("inf").unwrap_err().contains("finite"));
        assert!(parse_temperature("").unwrap_err().contains("finite"));
    }
}
