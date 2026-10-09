//! CPU test control flow, independent of the real machine so every outcome
//! can be tested with a scripted environment.

use super::{
    CTRL_C_REASON, Config, MonitoredTemperature, Ready, Sample, Status, TemperatureReading,
    TestResult,
};

pub const NO_TEMPERATURE_WARNING: &str =
    "No usable temperature sensor is available; Hardware Validator thermal protection is disabled for this run.";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Signal {
    Interrupt,
    Terminate,
}

impl Signal {
    fn reason(self) -> &'static str {
        match self {
            Self::Interrupt => CTRL_C_REASON,
            Self::Terminate => "Interrupted by SIGTERM.",
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct TemperatureRound {
    pub readings: Vec<TemperatureReading>,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Metrics {
    pub total_utilization_percent: f64,
    pub per_processor_utilization_percent: Option<Vec<f64>>,
    pub aggregate_frequency_hz: Option<f64>,
    pub per_processor_frequency_hz: Option<Vec<Option<f64>>>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StartError {
    pub message: String,
    pub warnings: Vec<String>,
}

/// Everything the test needs from the machine.
pub trait Environment {
    fn logical_processors(&mut self) -> Result<u32, String>;
    /// Records the utilization baseline the first sample is measured from.
    fn prime_utilization(&mut self) -> Result<(), String>;
    fn frequency(&mut self) -> Option<f64>;
    fn sample(&mut self, per_processor: bool) -> Result<Metrics, String>;
    fn temperatures(&mut self) -> TemperatureRound;
    /// Starts every worker and waits until each is ready, but keeps them idle.
    fn start_workers(&mut self, count: u32, load_percent: u32) -> Result<(), StartError>;
    fn release_workers(&mut self);
    fn request_stop(&mut self);
    fn workers_alive(&mut self) -> u32;
    /// 1-based index of a worker that exited before a stop was requested.
    fn unexpected_exit(&mut self) -> Option<u32>;
    /// Stops and joins every worker; returns the indexes of workers that panicked.
    fn join_workers(&mut self) -> Vec<u32>;
    /// Seconds on a monotonic clock.
    fn now(&mut self) -> f64;
    fn sleep(&mut self, seconds: f64);
    fn pending_signal(&mut self) -> Option<Signal>;
}

pub trait Observer {
    /// A failure to write the configuration or progress stops the test.
    fn on_ready(&mut self, ready: &Ready) -> std::io::Result<()>;
    fn on_sample(&mut self, sample: &Sample) -> std::io::Result<()>;
    fn on_worker_event(&mut self, message: &str);
    fn on_warning(&mut self, message: &str);
    fn on_error(&mut self, message: &str);
}

#[derive(Default)]
struct Accumulator {
    count: u32,
    total: f64,
    minimum: Option<f64>,
    maximum: Option<f64>,
}

fn max_of(current: Option<f64>, value: f64) -> Option<f64> {
    Some(current.map_or(value, |current| current.max(value)))
}

fn min_of(current: Option<f64>, value: f64) -> Option<f64> {
    Some(current.map_or(value, |current| current.min(value)))
}

impl Accumulator {
    fn add(&mut self, value: f64) {
        self.count += 1;
        self.total += value;
        self.minimum = min_of(self.minimum, value);
        self.maximum = max_of(self.maximum, value);
    }

    fn average(&self) -> Option<f64> {
        (self.count > 0).then(|| self.total / f64::from(self.count))
    }
}

/// The first selected outcome wins; later ones are ignored.
struct Outcome<'a, O: Observer> {
    observer: &'a mut O,
    status: Option<Status>,
    reason: String,
    stop_time: Option<f64>,
    warnings: Vec<String>,
}

impl<O: Observer> Outcome<'_, O> {
    fn select(&mut self, status: Status, reason: &str, at: Option<f64>) {
        if self.status.is_some() {
            return;
        }
        self.status = Some(status);
        self.reason = reason.to_owned();
        self.stop_time = at;
        if status != Status::Completed {
            self.observer.on_error(reason);
        }
    }

    fn warn(&mut self, message: &str) {
        if !self.warnings.iter().any(|known| known == message) {
            self.warnings.push(message.to_owned());
            self.observer.on_warning(message);
        }
    }

    fn done(&self) -> bool {
        self.status.is_some()
    }
}

/// Stops on a pending signal or a worker that died. Returns true when stopped.
fn interrupted<O: Observer>(
    environment: &mut impl Environment,
    outcome: &mut Outcome<O>,
    at: f64,
) -> bool {
    if let Some(signal) = environment.pending_signal() {
        outcome.select(Status::Cancelled, signal.reason(), Some(at));
    } else if let Some(worker) = environment.unexpected_exit() {
        let reason = format!("CPU load worker {worker} exited unexpectedly.");
        outcome.select(Status::Error, &reason, Some(at));
    }
    outcome.done()
}

/// The configured safeguard, or the sensor's own critical limit when lower.
fn effective_limit(critical: Option<f64>, configured: f64) -> f64 {
    critical.map_or(configured, |critical| critical.min(configured))
}

/// Monitored sensors that are still present in a new round, with current values.
fn still_monitored(
    monitored: &[MonitoredTemperature],
    readings: &[TemperatureReading],
) -> Vec<MonitoredTemperature> {
    monitored
        .iter()
        .filter_map(|expected| {
            let reading = readings.iter().find(|reading| reading.identity == expected.identity)?;
            Some(MonitoredTemperature { current_celsius: reading.current_celsius, ..expected.clone() })
        })
        .collect()
}

pub fn run<O: Observer>(
    config: &Config,
    environment: &mut impl Environment,
    observer: &mut O,
) -> TestResult {
    let mut outcome = Outcome {
        observer,
        status: None,
        reason: "CPU test did not start.".to_owned(),
        stop_time: None,
        warnings: Vec::new(),
    };
    let mut utilization = Accumulator::default();
    let mut frequency = Accumulator::default();
    let mut initial_temperature = None;
    let mut maximum_temperature: Option<f64> = None;
    let mut initial_frequency = None;
    let mut worker_count = 0;
    let mut workers_started = false;
    let mut start_time = None;

    match environment.logical_processors() {
        Ok(count) => worker_count = count,
        Err(error) => outcome.select(Status::Error, &error, None),
    }

    let configured_limit = config.maximum_temperature_celsius;
    let mut monitored: Vec<MonitoredTemperature> = Vec::new();
    if !outcome.done() {
        let round = environment.temperatures();
        for warning in &round.warnings {
            outcome.warn(warning);
        }
        monitored = round
            .readings
            .into_iter()
            .map(|reading| MonitoredTemperature {
                limit_celsius: effective_limit(reading.critical_celsius, configured_limit),
                identity: reading.identity,
                current_celsius: reading.current_celsius,
            })
            .collect();
        monitored.sort_by(|a, b| a.identity.cmp(&b.identity));
        let ready = Ready { worker_count, temperatures: monitored.clone() };
        if let Err(error) = outcome.observer.on_ready(&ready) {
            let reason = format!("Unable to write CPU test progress: {error}.");
            outcome.select(Status::Error, &reason, None);
        } else if monitored.is_empty() {
            if config.allow_no_temperature {
                outcome.warn(NO_TEMPERATURE_WARNING);
            } else {
                let reason = "No usable temperature measurement is available.";
                outcome.select(Status::Error, reason, None);
            }
        } else {
            initial_temperature = monitored.iter().map(|t| t.current_celsius).reduce(f64::max);
            maximum_temperature = initial_temperature;
            if let Some(hot) = monitored.iter().find(|t| t.current_celsius >= t.limit_celsius) {
                let reason = format!(
                    "Monitored temperature sensor {} is already at its effective limit.",
                    hot.identity
                );
                outcome.select(Status::TemperatureLimit, &reason, None);
            }
        }
    }

    if !outcome.done() {
        let signal = environment.pending_signal();
        if let Some(signal) = signal {
            outcome.select(Status::Cancelled, signal.reason(), None);
        }
    }
    if !outcome.done() {
        let primed = environment.prime_utilization();
        if let Err(error) = primed {
            outcome.select(Status::Error, &error, None);
        }
    }
    if !outcome.done() {
        initial_frequency = environment.frequency();
        match environment.start_workers(worker_count, config.load_percent) {
            Ok(()) => {
                workers_started = true;
                let message = format!("{worker_count} CPU load workers are ready.");
                outcome.observer.on_worker_event(&message);
            }
            Err(error) => {
                for warning in &error.warnings {
                    outcome.warn(warning);
                }
                match environment.pending_signal() {
                    Some(signal) => outcome.select(Status::Cancelled, signal.reason(), None),
                    None => outcome.select(Status::Error, &error.message, None),
                }
            }
        }
    }
    if !outcome.done() {
        match environment.pending_signal() {
            Some(signal) => outcome.select(Status::Cancelled, signal.reason(), None),
            None => {
                start_time = Some(environment.now());
                environment.release_workers();
                outcome.observer.on_worker_event("CPU load workers started.");
            }
        }
    }

    if let Some(start) = start_time {
        let deadline = start + f64::from(config.duration_seconds);
        let mut next_sample = start + 1.0;
        let mut last_temperature_time = start;
        while !outcome.done() {
            let mut now = environment.now();
            if interrupted(environment, &mut outcome, now) {
                break;
            }
            if now >= next_sample && next_sample <= deadline {
                let metrics = match environment.sample(config.verbose) {
                    Ok(metrics) => metrics,
                    Err(error) => {
                        outcome.select(Status::Error, &error, Some(environment.now()));
                        break;
                    }
                };
                if interrupted(environment, &mut outcome, now) {
                    break;
                }
                utilization.add(metrics.total_utilization_percent);
                if let Some(hz) = metrics.aggregate_frequency_hz {
                    frequency.add(hz);
                }

                let mut current: Vec<MonitoredTemperature> = Vec::new();
                let mut round_warnings = Vec::new();
                let sample_time;
                if monitored.is_empty() {
                    sample_time = environment.now();
                } else {
                    let round = environment.temperatures();
                    round_warnings = round.warnings;
                    sample_time = environment.now();
                    let stopped = interrupted(environment, &mut outcome, sample_time);
                    if !stopped && sample_time - last_temperature_time > 2.0 {
                        let reason =
                            "Temperature monitoring exceeded the two-second safety interval.";
                        outcome.select(Status::Error, reason, Some(sample_time));
                    }
                    current = still_monitored(&monitored, &round.readings);
                    if !outcome.done() && current.is_empty() {
                        let reason = "All monitored temperature sensors were lost.";
                        outcome.select(Status::Error, reason, Some(sample_time));
                    }
                    if outcome.done() {
                        for warning in &round_warnings {
                            outcome.warn(warning);
                        }
                        break;
                    }
                    last_temperature_time = sample_time;
                    let missing: Vec<&str> = monitored
                        .iter()
                        .map(|expected| expected.identity.as_str())
                        .filter(|identity| !current.iter().any(|t| t.identity == *identity))
                        .collect();
                    if !missing.is_empty() {
                        round_warnings.push(format!(
                            "Some monitored temperature sensors are unavailable: {}.",
                            missing.join(", ")
                        ));
                    }
                }
                for reading in &current {
                    maximum_temperature = max_of(maximum_temperature, reading.current_celsius);
                }
                if let Some(hot) = current.iter().find(|t| t.current_celsius >= t.limit_celsius) {
                    let reason = format!(
                        "Monitored temperature sensor {} reached its effective limit.",
                        hot.identity
                    );
                    outcome.select(Status::TemperatureLimit, &reason, Some(sample_time));
                }
                for warning in &round_warnings {
                    outcome.warn(warning);
                }
                let sample = Sample {
                    elapsed_seconds: sample_time - start,
                    total_utilization_percent: metrics.total_utilization_percent,
                    per_processor_utilization_percent: metrics
                        .per_processor_utilization_percent
                        .filter(|_| config.verbose),
                    aggregate_frequency_hz: metrics.aggregate_frequency_hz,
                    per_processor_frequency_hz: metrics
                        .per_processor_frequency_hz
                        .filter(|_| config.verbose),
                    temperatures: current,
                    workers_alive: environment.workers_alive(),
                    worker_count,
                };
                if let Err(error) = outcome.observer.on_sample(&sample) {
                    let reason = format!("Unable to write CPU test progress: {error}.");
                    outcome.select(Status::Error, &reason, Some(sample_time));
                }
                if outcome.done() || interrupted(environment, &mut outcome, sample_time) {
                    break;
                }
                now = sample_time;
                next_sample = sample_time + 1.0;
            }
            if now >= deadline {
                outcome.select(Status::Completed, "Requested duration elapsed.", Some(now));
                break;
            }
            let wait_until = deadline.min(next_sample).min(now + 0.1);
            environment.sleep((wait_until - now).max(0.0));
        }
    }

    if workers_started {
        outcome.observer.on_worker_event("Cooperative worker stop was requested.");
        environment.request_stop();
        let panicked = environment.join_workers();
        if !panicked.is_empty() {
            let list: Vec<String> = panicked.iter().map(u32::to_string).collect();
            outcome.warn(&format!("CPU load workers panicked: {}.", list.join(", ")));
            if outcome.status == Some(Status::Completed) {
                outcome.status = None;
                let reason = "CPU load workers required abnormal cleanup.";
                outcome.select(Status::Error, reason, outcome.stop_time);
            }
        }
        outcome.observer.on_worker_event("CPU load workers exited and were joined.");
    }

    let actual_duration_seconds = match (start_time, outcome.stop_time) {
        (Some(start), Some(stop)) => (stop - start).max(0.0),
        _ => 0.0,
    };
    TestResult {
        status: outcome.status.unwrap_or(Status::Error),
        reason: outcome.reason,
        requested_duration_seconds: config.duration_seconds,
        actual_duration_seconds,
        requested_load_percent: config.load_percent,
        worker_count,
        sample_count: utilization.count,
        utilization_average_percent: utilization.average(),
        utilization_minimum_percent: utilization.minimum,
        utilization_maximum_percent: utilization.maximum,
        initial_temperature_celsius: initial_temperature,
        maximum_temperature_celsius: maximum_temperature,
        initial_frequency_hz: initial_frequency,
        minimum_frequency_hz: frequency.minimum,
        maximum_frequency_hz: frequency.maximum,
        warnings: outcome.warnings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    fn reading(identity: &str, celsius: f64, critical: Option<f64>) -> TemperatureReading {
        TemperatureReading {
            identity: identity.into(),
            current_celsius: celsius,
            critical_celsius: critical,
        }
    }

    fn round(readings: Vec<TemperatureReading>) -> TemperatureRound {
        TemperatureRound { readings, warnings: Vec::new() }
    }

    /// A scripted machine with a manual clock that only moves on sleep.
    struct Fake {
        clock: f64,
        rounds: VecDeque<TemperatureRound>,
        /// Extra seconds every temperature read after the first one takes.
        temperature_delay: f64,
        start_error: Option<StartError>,
        signal_at: Option<(f64, Signal)>,
        worker_exit_at: Option<f64>,
        panicked: Vec<u32>,
        started: bool,
        joined: bool,
        temperature_reads: u32,
    }

    impl Fake {
        fn new(rounds: Vec<TemperatureRound>) -> Self {
            Self {
                clock: 0.0,
                rounds: rounds.into(),
                temperature_delay: 0.0,
                start_error: None,
                signal_at: None,
                worker_exit_at: None,
                panicked: Vec::new(),
                started: false,
                joined: false,
                temperature_reads: 0,
            }
        }

        fn cool() -> Self {
            Self::new(vec![round(vec![reading("hwmon:cpu:Package:0", 40.0, None)])])
        }
    }

    impl Environment for Fake {
        fn logical_processors(&mut self) -> Result<u32, String> {
            Ok(4)
        }

        fn prime_utilization(&mut self) -> Result<(), String> {
            Ok(())
        }

        fn frequency(&mut self) -> Option<f64> {
            Some(3e9)
        }

        fn sample(&mut self, per_processor: bool) -> Result<Metrics, String> {
            Ok(Metrics {
                total_utilization_percent: 50.0 + self.clock.floor(),
                per_processor_utilization_percent: per_processor.then(|| vec![60.0; 4]),
                aggregate_frequency_hz: Some(2e9),
                per_processor_frequency_hz: Some(vec![Some(2e9); 4]),
            })
        }

        fn temperatures(&mut self) -> TemperatureRound {
            if self.temperature_reads > 0 {
                self.clock += self.temperature_delay;
            }
            self.temperature_reads += 1;
            if self.rounds.len() > 1 {
                self.rounds.pop_front().unwrap()
            } else {
                self.rounds.front().cloned().unwrap_or_default()
            }
        }

        fn start_workers(&mut self, _: u32, _: u32) -> Result<(), StartError> {
            match self.start_error.take() {
                Some(error) => Err(error),
                None => {
                    self.started = true;
                    Ok(())
                }
            }
        }

        fn release_workers(&mut self) {}

        fn request_stop(&mut self) {}

        fn workers_alive(&mut self) -> u32 {
            4
        }

        fn unexpected_exit(&mut self) -> Option<u32> {
            self.worker_exit_at.filter(|at| self.clock >= *at).map(|_| 2)
        }

        fn join_workers(&mut self) -> Vec<u32> {
            self.joined = true;
            self.panicked.clone()
        }

        fn now(&mut self) -> f64 {
            self.clock
        }

        fn sleep(&mut self, seconds: f64) {
            self.clock += seconds;
        }

        fn pending_signal(&mut self) -> Option<Signal> {
            self.signal_at.filter(|(at, _)| self.clock >= *at).map(|(_, signal)| signal)
        }
    }

    #[derive(Default)]
    struct Recorder {
        samples: Vec<Sample>,
        events: Vec<String>,
        fail_samples: bool,
    }

    impl Observer for Recorder {
        fn on_ready(&mut self, ready: &Ready) -> std::io::Result<()> {
            self.events.push(format!("ready {}", ready.temperatures.len()));
            Ok(())
        }

        fn on_sample(&mut self, sample: &Sample) -> std::io::Result<()> {
            self.samples.push(sample.clone());
            if self.fail_samples { Err(std::io::Error::other("closed")) } else { Ok(()) }
        }

        fn on_worker_event(&mut self, message: &str) {
            self.events.push(format!("worker {message}"));
        }

        fn on_warning(&mut self, message: &str) {
            self.events.push(format!("warning {message}"));
        }

        fn on_error(&mut self, message: &str) {
            self.events.push(format!("error {message}"));
        }
    }

    fn config(seconds: u32) -> Config {
        Config { duration_seconds: seconds, ..Config::default() }
    }

    fn run_with(config: &Config, fake: &mut Fake) -> (TestResult, Recorder) {
        let mut recorder = Recorder::default();
        let result = run(config, fake, &mut recorder);
        (result, recorder)
    }

    #[test]
    fn normal_completion_samples_once_per_second_and_joins_workers() {
        let mut fake = Fake::cool();
        let (result, recorder) = run_with(&config(10), &mut fake);
        assert_eq!(result.status, Status::Completed, "{}", result.reason);
        assert_eq!(result.reason, "Requested duration elapsed.");
        assert_eq!(result.sample_count, 10);
        assert!((result.actual_duration_seconds - 10.0).abs() < 1e-6);
        assert_eq!(result.utilization_minimum_percent, Some(51.0));
        assert_eq!(result.utilization_maximum_percent, Some(60.0));
        assert_eq!(result.initial_frequency_hz, Some(3e9));
        assert_eq!(result.initial_temperature_celsius, Some(40.0));
        assert!(result.warnings.is_empty());
        assert!(fake.joined);
        assert_eq!(recorder.samples.len(), 10);
        assert!(recorder.samples[0].per_processor_utilization_percent.is_none());
        assert!(recorder.events.contains(&"worker 4 CPU load workers are ready.".to_owned()));
    }

    #[test]
    fn verbose_keeps_per_processor_values() {
        let mut fake = Fake::cool();
        let (_, recorder) = run_with(&Config { verbose: true, ..config(10) }, &mut fake);
        assert_eq!(recorder.samples[0].per_processor_utilization_percent, Some(vec![60.0; 4]));
        assert_eq!(recorder.samples[0].per_processor_frequency_hz, Some(vec![Some(2e9); 4]));
    }

    #[test]
    fn missing_temperature_needs_the_explicit_override() {
        let mut fake = Fake::new(vec![]);
        let (result, _) = run_with(&config(10), &mut fake);
        assert_eq!(result.status, Status::Error);
        assert_eq!(result.reason, "No usable temperature measurement is available.");
        assert!(!fake.started);

        let mut fake = Fake::new(vec![]);
        let allowed = Config { allow_no_temperature: true, ..config(10) };
        let (result, _) = run_with(&allowed, &mut fake);
        assert_eq!(result.status, Status::Completed);
        assert_eq!(result.warnings, [NO_TEMPERATURE_WARNING]);
        assert_eq!(result.maximum_temperature_celsius, None);
    }

    #[test]
    fn hot_sensor_before_start_creates_no_workers() {
        let mut fake = Fake::new(vec![round(vec![reading("thermal:thermal_zone0", 85.0, None)])]);
        let (result, _) = run_with(&config(10), &mut fake);
        assert_eq!(result.status, Status::TemperatureLimit);
        assert!(result.reason.contains("thermal:thermal_zone0 is already at its effective limit"));
        assert!(!fake.started);
        assert_eq!(result.actual_duration_seconds, 0.0);
    }

    #[test]
    fn lower_critical_limit_stops_the_run() {
        let sensor = "hwmon:nvme:Composite:0";
        let mut fake = Fake::new(vec![
            round(vec![reading(sensor, 50.0, Some(70.0))]),
            round(vec![reading(sensor, 60.0, Some(70.0))]),
            round(vec![reading(sensor, 70.5, Some(70.0))]),
        ]);
        let (result, recorder) = run_with(&config(60), &mut fake);
        assert_eq!(result.status, Status::TemperatureLimit);
        assert!(result.reason.ends_with("reached its effective limit."));
        assert_eq!(result.maximum_temperature_celsius, Some(70.5));
        assert_eq!(recorder.samples.len(), 2, "the limit sample is still reported");
        assert_eq!(recorder.samples[1].temperatures[0].limit_celsius, 70.0);
        assert!(fake.joined);
    }

    #[test]
    fn sensor_loss_warns_for_some_and_stops_for_all() {
        let both = vec![reading("a", 40.0, None), reading("b", 41.0, None)];
        let mut fake = Fake::new(vec![round(both.clone()), round(vec![reading("b", 42.0, None)])]);
        let (result, _) = run_with(&config(10), &mut fake);
        assert_eq!(result.status, Status::Completed);
        assert_eq!(result.warnings, ["Some monitored temperature sensors are unavailable: a."]);

        let mut fake = Fake::new(vec![round(both), round(vec![reading("new", 30.0, None)])]);
        let (result, _) = run_with(&config(10), &mut fake);
        assert_eq!(result.status, Status::Error);
        assert_eq!(result.reason, "All monitored temperature sensors were lost.");
    }

    #[test]
    fn slow_temperature_round_is_a_safety_error() {
        let mut fake = Fake::cool();
        fake.temperature_delay = 2.5;
        let (result, _) = run_with(&config(10), &mut fake);
        assert_eq!(result.status, Status::Error);
        assert_eq!(result.reason, "Temperature monitoring exceeded the two-second safety interval.");
    }

    #[test]
    fn worker_exit_stops_the_run() {
        let mut fake = Fake::cool();
        fake.worker_exit_at = Some(3.0);
        let (result, _) = run_with(&config(10), &mut fake);
        assert_eq!(result.status, Status::Error);
        assert_eq!(result.reason, "CPU load worker 2 exited unexpectedly.");
        assert!(fake.joined);
    }

    #[test]
    fn signals_cancel_the_run() {
        let cases = [(Signal::Interrupt, CTRL_C_REASON), (Signal::Terminate, "Interrupted by SIGTERM.")];
        for (signal, reason) in cases {
            let mut fake = Fake::cool();
            fake.signal_at = Some((2.5, signal));
            let (result, recorder) = run_with(&config(10), &mut fake);
            assert_eq!(result.status, Status::Cancelled);
            assert_eq!(result.reason, reason);
            assert!((result.actual_duration_seconds - 2.5).abs() < 0.11);
            assert!(recorder.events.contains(&format!("error {reason}")));
            assert!(fake.joined);
        }
    }

    #[test]
    fn panicked_workers_turn_completion_into_an_error() {
        let mut fake = Fake::cool();
        fake.panicked = vec![3];
        let (result, _) = run_with(&config(10), &mut fake);
        assert_eq!(result.status, Status::Error);
        assert_eq!(result.reason, "CPU load workers required abnormal cleanup.");
        assert_eq!(result.warnings, ["CPU load workers panicked: 3."]);
    }

    #[test]
    fn start_failure_and_output_failure_are_errors() {
        let mut fake = Fake::cool();
        let warnings = vec!["w".to_owned()];
        fake.start_error = Some(StartError { message: "no threads".into(), warnings });
        let (result, _) = run_with(&config(10), &mut fake);
        assert_eq!((result.status, result.reason.as_str()), (Status::Error, "no threads"));
        assert_eq!(result.warnings, ["w"]);
        assert!(!fake.joined);

        let mut fake = Fake::cool();
        let mut recorder = Recorder { fail_samples: true, ..Recorder::default() };
        let result = run(&config(10), &mut fake, &mut recorder);
        assert_eq!(result.status, Status::Error);
        assert_eq!(result.reason, "Unable to write CPU test progress: closed.");
        assert_eq!(result.sample_count, 1);
    }
}
