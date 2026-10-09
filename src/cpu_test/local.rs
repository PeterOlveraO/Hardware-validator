//! The real machine behind the CPU test: `/proc/stat` utilization, cpufreq,
//! hwmon/thermal sensors, native worker threads, and SIGINT/SIGTERM.

use std::sync::atomic::{AtomicI32, Ordering};
use std::time::{Duration, Instant};

use super::engine::{Environment, Metrics, Signal, StartError, TemperatureRound};
use super::temperature;
use super::workers::Workers;
use crate::detectors::cpu::frequencies;
use crate::host::Host;
use crate::text::read_optional;

/// First SIGINT/SIGTERM received while the test runs; 0 when none.
static PENDING_SIGNAL: AtomicI32 = AtomicI32::new(0);

extern "C" fn record_signal(signal: libc::c_int) {
    // Only an atomic store: async-signal-safe. Cleanup stays in normal control flow.
    let _ = PENDING_SIGNAL.compare_exchange(0, signal, Ordering::SeqCst, Ordering::SeqCst);
}

/// Installs the recording handlers and restores the previous ones on drop.
struct SignalGuard {
    previous: [(libc::c_int, libc::sighandler_t); 2],
}

impl SignalGuard {
    fn install() -> Self {
        PENDING_SIGNAL.store(0, Ordering::SeqCst);
        let handler = record_signal as extern "C" fn(libc::c_int) as libc::sighandler_t;
        // SAFETY: the handler only performs an atomic store.
        let previous = [libc::SIGINT, libc::SIGTERM]
            .map(|signal| (signal, unsafe { libc::signal(signal, handler) }));
        Self { previous }
    }
}

impl Drop for SignalGuard {
    fn drop(&mut self) {
        for (signal, handler) in self.previous {
            // SAFETY: restores the disposition that was active before install().
            unsafe { libc::signal(signal, handler) };
        }
    }
}

/// Cumulative `/proc/stat` jiffies of one CPU line: (busy, total).
fn cpu_times(fields: &[u64]) -> (u64, u64) {
    // user nice system idle iowait irq softirq steal guest guest_nice;
    // guest time is already included in user and nice.
    let total: u64 = fields.iter().take(8).sum();
    let idle = fields.get(3).copied().unwrap_or(0) + fields.get(4).copied().unwrap_or(0);
    (total - idle.min(total), total)
}

fn proc_stat(text: &str) -> Vec<(u64, u64)> {
    text.lines()
        .take_while(|line| line.starts_with("cpu"))
        .map(|line| {
            let fields: Vec<u64> =
                line.split_whitespace().skip(1).filter_map(|field| field.parse().ok()).collect();
            cpu_times(&fields)
        })
        .collect()
}

/// Percent busy between two observations, clamped to 0..=100.
fn busy_percent(previous: (u64, u64), current: (u64, u64)) -> f64 {
    let busy = current.0.saturating_sub(previous.0) as f64;
    let total = current.1.saturating_sub(previous.1) as f64;
    if total == 0.0 { 0.0 } else { (busy / total * 100.0).clamp(0.0, 100.0) }
}

pub struct LocalEnvironment {
    host: Host,
    epoch: Instant,
    /// `/proc/stat` baseline: the aggregate line first, then one per CPU.
    baseline: Vec<(u64, u64)>,
    workers: Workers,
    _signals: SignalGuard,
}

impl LocalEnvironment {
    /// Also installs the SIGINT/SIGTERM recorders until the value is dropped.
    pub fn install() -> Self {
        Self {
            host: Host::local(),
            epoch: Instant::now(),
            baseline: Vec::new(),
            workers: Workers::default(),
            _signals: SignalGuard::install(),
        }
    }

    fn read_stat(&self) -> Result<Vec<(u64, u64)>, String> {
        let unavailable = || "Unable to measure CPU utilization.".to_owned();
        let text = read_optional(&self.host.path("/proc/stat")).ok().flatten();
        let times = proc_stat(&text.ok_or_else(unavailable)?);
        if times.is_empty() { Err(unavailable()) } else { Ok(times) }
    }

    fn per_processor_frequency(&self) -> Option<Vec<Option<f64>>> {
        let cpuinfo = read_optional(&self.host.path("/proc/cpuinfo")).ok().flatten();
        let entries = frequencies(&self.host, cpuinfo.as_deref()).ok()?;
        Some(entries.iter().map(|entry| entry.current_hz.map(|hz| hz as f64)).collect())
    }
}

fn average(values: &[Option<f64>]) -> Option<f64> {
    let known: Vec<f64> = values.iter().flatten().copied().collect();
    (!known.is_empty()).then(|| known.iter().sum::<f64>() / known.len() as f64)
}

impl Environment for LocalEnvironment {
    fn logical_processors(&mut self) -> Result<u32, String> {
        self.host
            .online_processors
            .ok_or_else(|| "Logical processor count is unavailable or invalid.".to_owned())
    }

    fn prime_utilization(&mut self) -> Result<(), String> {
        self.baseline = self.read_stat()?;
        Ok(())
    }

    fn frequency(&mut self) -> Option<f64> {
        average(&self.per_processor_frequency()?)
    }

    fn sample(&mut self, per_processor: bool) -> Result<Metrics, String> {
        let current = self.read_stat()?;
        let percents: Vec<f64> =
            self.baseline.iter().zip(&current).map(|(old, new)| busy_percent(*old, *new)).collect();
        self.baseline = current;
        let per_frequency = self.per_processor_frequency();
        Ok(Metrics {
            total_utilization_percent: percents.first().copied().unwrap_or(0.0),
            per_processor_utilization_percent: per_processor
                .then(|| percents.get(1..).unwrap_or_default().to_vec()),
            aggregate_frequency_hz: per_frequency.as_deref().and_then(average),
            per_processor_frequency_hz: per_frequency,
        })
    }

    fn temperatures(&mut self) -> TemperatureRound {
        temperature::read(&self.host.root)
    }

    fn start_workers(&mut self, count: u32, load_percent: u32) -> Result<(), StartError> {
        self.workers.start(count, load_percent)
    }

    fn release_workers(&mut self) {
        self.workers.release();
    }

    fn request_stop(&mut self) {
        self.workers.request_stop();
    }

    fn workers_alive(&mut self) -> u32 {
        self.workers.alive()
    }

    fn unexpected_exit(&mut self) -> Option<u32> {
        self.workers.unexpected_exit()
    }

    fn join_workers(&mut self) -> Vec<u32> {
        self.workers.join()
    }

    fn now(&mut self) -> f64 {
        self.epoch.elapsed().as_secs_f64()
    }

    fn sleep(&mut self, seconds: f64) {
        std::thread::sleep(Duration::from_secs_f64(seconds));
    }

    fn pending_signal(&mut self) -> Option<Signal> {
        match PENDING_SIGNAL.load(Ordering::SeqCst) {
            libc::SIGINT => Some(Signal::Interrupt),
            libc::SIGTERM => Some(Signal::Terminate),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn proc_stat_utilization_matches_psutil_rules() {
        let first = proc_stat("cpu 1 0 1 7 1 0 0 0 5 0\ncpu0 1 0 0 4 0 0 0 0 0 0\nintr 1\n");
        let second = proc_stat("cpu 4 0 2 8 2 0 0 0 9 0\ncpu0 1 0 0 5 0 0 0 0 0 0\n");
        assert_eq!(first, [(2, 10), (1, 5)]);
        assert_eq!(busy_percent(first[0], second[0]), 4.0 / 6.0 * 100.0);
        assert_eq!(busy_percent(first[1], second[1]), 0.0);
        assert_eq!(busy_percent(second[1], second[1]), 0.0, "no elapsed time");
    }

    #[test]
    fn signals_are_recorded_once_and_handlers_restored() {
        let guard = SignalGuard::install();
        record_signal(libc::SIGTERM);
        record_signal(libc::SIGINT);
        assert_eq!(PENDING_SIGNAL.load(Ordering::SeqCst), libc::SIGTERM);
        drop(guard);
        // SAFETY: reading the current disposition by setting and restoring it.
        let current = unsafe { libc::signal(libc::SIGTERM, libc::SIG_DFL) };
        assert_eq!(current, libc::SIG_DFL);
    }
}
