//! Plain-text CPU test progress (stdout), diagnostics (stderr), and result.

use std::collections::BTreeMap;
use std::io::{self, Write};

use super::engine::Observer;
use super::{Config, Ready, Sample, TestResult};
use crate::text::plain_text;

const UNKNOWN: &str = "Unknown";

fn number(value: Option<f64>, unit: &str, digits: usize) -> String {
    value.map_or_else(|| UNKNOWN.to_owned(), |value| format!("{value:.digits$} {unit}"))
}

fn frequency(value: Option<f64>) -> String {
    match value {
        None => UNKNOWN.to_owned(),
        Some(hz) if hz >= 1e9 => format!("{:.2} GHz", hz / 1e9),
        Some(hz) if hz >= 1e6 => format!("{:.2} MHz", hz / 1e6),
        Some(hz) => format!("{hz:.0} Hz"),
    }
}

/// Six significant digits without trailing zeros, like Python's `{:g}`.
fn general(value: f64) -> String {
    let integer_digits =
        if value.abs() >= 1.0 { value.abs().log10().floor() as usize + 1 } else { 1 };
    let text = format!("{:.*}", 6usize.saturating_sub(integer_digits), value);
    if text.contains('.') {
        text.trim_end_matches('0').trim_end_matches('.').to_owned()
    } else {
        text
    }
}

pub struct ConsoleReporter<O: Write, E: Write> {
    config: Config,
    processor_model: Option<String>,
    out: O,
    err: E,
    next_compact_update: f64,
    sample_sequence: u32,
    last_sample_elapsed: Option<f64>,
    /// identity -> (minimum, maximum, last) observed temperature.
    ranges: BTreeMap<String, (f64, f64, f64)>,
    /// identity -> effective limit, for every sensor monitored since the start.
    limits: BTreeMap<String, f64>,
}

impl ConsoleReporter<io::Stdout, io::Stderr> {
    pub fn new(config: &Config, processor_model: Option<String>) -> Self {
        Self::with_streams(config, processor_model, io::stdout(), io::stderr())
    }
}

impl<O: Write, E: Write> ConsoleReporter<O, E> {
    pub fn with_streams(config: &Config, processor_model: Option<String>, out: O, err: E) -> Self {
        Self {
            config: config.clone(),
            processor_model,
            out,
            err,
            next_compact_update: 5.0,
            sample_sequence: 0,
            last_sample_elapsed: None,
            ranges: BTreeMap::new(),
            limits: BTreeMap::new(),
        }
    }

    pub fn into_streams(self) -> (O, E) {
        (self.out, self.err)
    }

    fn write_ready(&mut self, ready: &Ready) -> io::Result<()> {
        let config = &self.config;
        let model = self.processor_model.as_deref().map_or_else(|| UNKNOWN.to_owned(), plain_text);
        let out = &mut self.out;
        writeln!(out, "CPU Test")?;
        writeln!(out, "  Processor Model: {model}")?;
        writeln!(out, "  Requested Duration: {} seconds", config.duration_seconds)?;
        writeln!(out, "  Requested Load: {}% duty cycle", config.load_percent)?;
        writeln!(out, "  Workers: {}", ready.worker_count)?;
        writeln!(out, "  Thermal Limit: {} C", general(config.maximum_temperature_celsius))?;
        let monitoring = if ready.temperatures.is_empty() { "Unavailable" } else { "Active" };
        writeln!(out, "  Thermal Monitoring: {monitoring}")?;
        let lower: Vec<_> = ready
            .temperatures
            .iter()
            .filter(|t| t.limit_celsius < config.maximum_temperature_celsius)
            .collect();
        if !lower.is_empty() {
            writeln!(out, "  Lower Sensor Effective Limits:")?;
            for reading in lower {
                writeln!(
                    out,
                    "    {}: {:.1} C",
                    plain_text(&reading.identity),
                    reading.limit_celsius
                )?;
            }
        }
        Ok(())
    }

    fn write_details(&mut self, sample: &Sample, interval: Option<f64>) -> io::Result<()> {
        let out = &mut self.out;
        let rate = interval.filter(|interval| *interval != 0.0).map(|interval| 1.0 / interval);
        writeln!(
            out,
            "  Sample: sequence={} interval={} rate={}",
            self.sample_sequence,
            number(interval, "s", 2),
            number(rate, "samples/s", 2)
        )?;
        let utilization = match &sample.per_processor_utilization_percent {
            None => UNKNOWN.to_owned(),
            Some(values) => {
                let values: Vec<String> = values
                    .iter()
                    .enumerate()
                    .map(|(index, value)| format!("CPU {index}={value:.1}%"))
                    .collect();
                values.join(", ")
            }
        };
        writeln!(out, "  Per-processor utilization: {utilization}")?;
        writeln!(out, "  Aggregate frequency: {}", frequency(sample.aggregate_frequency_hz))?;
        if let Some(values) = &sample.per_processor_frequency_hz {
            let values: Vec<String> = values
                .iter()
                .enumerate()
                .map(|(index, value)| format!("CPU {index}={}", frequency(*value)))
                .collect();
            writeln!(out, "  Per-processor frequencies: {}", values.join(", "))?;
        }
        if sample.temperatures.is_empty() && self.limits.is_empty() {
            writeln!(out, "  Monitored temperature sensors: Unknown")?;
            return Ok(());
        }
        writeln!(out, "  Monitored temperature sensors:")?;
        for (identity, limit) in &self.limits {
            let (minimum, maximum, last) = self.ranges[identity];
            let current = match sample.temperatures.iter().find(|t| &t.identity == identity) {
                Some(reading) => format!("{:.1} C", reading.current_celsius),
                None => "Unavailable".to_owned(),
            };
            writeln!(
                out,
                "    {}: {current} (effective limit {limit:.1} C, min {minimum:.1} C, max {maximum:.1} C, last {last:.1} C)",
                plain_text(identity)
            )?;
        }
        Ok(())
    }
}

impl<O: Write, E: Write> Observer for ConsoleReporter<O, E> {
    fn on_ready(&mut self, ready: &Ready) -> io::Result<()> {
        for reading in &ready.temperatures {
            let value = reading.current_celsius;
            self.ranges.insert(reading.identity.clone(), (value, value, value));
            self.limits.insert(reading.identity.clone(), reading.limit_celsius);
        }
        self.write_ready(ready)
    }

    fn on_sample(&mut self, sample: &Sample) -> io::Result<()> {
        self.sample_sequence += 1;
        let interval = self.last_sample_elapsed.map(|last| sample.elapsed_seconds - last);
        self.last_sample_elapsed = Some(sample.elapsed_seconds);
        for reading in &sample.temperatures {
            let value = reading.current_celsius;
            self.ranges
                .entry(reading.identity.clone())
                .and_modify(|range| *range = (range.0.min(value), range.1.max(value), value))
                .or_insert((value, value, value));
        }
        if !self.config.verbose && sample.elapsed_seconds < self.next_compact_update {
            return Ok(());
        }
        let remaining = (f64::from(self.config.duration_seconds) - sample.elapsed_seconds).max(0.0);
        let temperature = sample.temperatures.iter().map(|t| t.current_celsius).reduce(f64::max);
        writeln!(
            self.out,
            "Progress: elapsed={:.1}s remaining={remaining:.1}s cpu={:.1}% frequency={} temperature={} workers={}/{}",
            sample.elapsed_seconds,
            sample.total_utilization_percent,
            frequency(sample.aggregate_frequency_hz),
            number(temperature, "C", 1),
            sample.workers_alive,
            sample.worker_count
        )?;
        if self.config.verbose {
            self.write_details(sample, interval)
        } else {
            self.next_compact_update = sample.elapsed_seconds + 5.0;
            Ok(())
        }
    }

    fn on_worker_event(&mut self, message: &str) {
        if self.config.verbose {
            let _ = writeln!(self.out, "Worker event: {}", plain_text(message));
        }
    }

    fn on_warning(&mut self, message: &str) {
        let _ = writeln!(self.err, "Warning: {}", plain_text(message));
    }

    fn on_error(&mut self, message: &str) {
        let prefix = if message.contains("Interrupt") { "Interrupted" } else { "Error" };
        let _ = writeln!(self.err, "{prefix}: {}", plain_text(message));
    }
}

/// The final result, without health or benchmark claims.
pub fn render_result(result: &TestResult) -> String {
    let mut lines = vec![
        "CPU Test Result".to_owned(),
        format!("  Status: {}", result.status.as_str()),
        format!("  Reason: {}", plain_text(&result.reason)),
        format!("  Requested Duration: {} seconds", result.requested_duration_seconds),
        format!("  Actual Duration: {:.2} seconds", result.actual_duration_seconds),
        format!("  Requested Load: {}% duty cycle", result.requested_load_percent),
        format!("  Workers: {}", result.worker_count),
        format!("  Valid Samples: {}", result.sample_count),
        format!("  Average Utilization: {}", number(result.utilization_average_percent, "%", 1)),
        format!("  Minimum Utilization: {}", number(result.utilization_minimum_percent, "%", 1)),
        format!("  Maximum Utilization: {}", number(result.utilization_maximum_percent, "%", 1)),
        format!("  Initial Temperature: {}", number(result.initial_temperature_celsius, "C", 1)),
        format!("  Maximum Temperature: {}", number(result.maximum_temperature_celsius, "C", 1)),
        format!("  Initial Frequency: {}", frequency(result.initial_frequency_hz)),
        format!("  Minimum Frequency: {}", frequency(result.minimum_frequency_hz)),
        format!("  Maximum Frequency: {}", frequency(result.maximum_frequency_hz)),
    ];
    if result.warnings.is_empty() {
        lines.push("  Warnings: None".to_owned());
    } else {
        lines.push("  Warnings:".to_owned());
        lines
            .extend(result.warnings.iter().map(|warning| format!("    - {}", plain_text(warning))));
    }
    lines.push("  This exercise does not certify CPU health or long-term reliability.".to_owned());
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers_frequencies_and_general_format() {
        assert_eq!(number(Some(60.04), "%", 1), "60.0 %");
        assert_eq!(number(None, "C", 1), UNKNOWN);
        assert_eq!(frequency(Some(3_456_000_000.0)), "3.46 GHz");
        assert_eq!(frequency(Some(800_000_000.0)), "800.00 MHz");
        assert_eq!(frequency(Some(999.6)), "1000 Hz");
        assert_eq!(general(85.0), "85");
        assert_eq!(general(85.5), "85.5");
        assert_eq!(general(100.0), "100");
        assert_eq!(general(50.123456), "50.1235");
    }
}
