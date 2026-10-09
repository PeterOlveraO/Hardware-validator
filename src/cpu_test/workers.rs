//! One native load thread per logical processor. Threads start idle, wait
//! for a shared release, run an approximate duty cycle, and stop
//! cooperatively within one 100 ms cycle.

use std::hint::black_box;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use super::engine::StartError;

const CYCLE: Duration = Duration::from_millis(100);
const STARTUP_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Default)]
struct Shared {
    released: AtomicBool,
    stopped: AtomicBool,
    ready: AtomicU32,
}

#[derive(Default)]
pub struct Workers {
    shared: Arc<Shared>,
    handles: Vec<JoinHandle<()>>,
}

/// Fixed-size scalar work with no allocation.
fn work_chunk(mut value: f64) -> f64 {
    for _ in 0..128 {
        value = black_box((value * 1.000_000_119 + 0.000_000_357) % 1_000_003.0);
    }
    value
}

/// Sleeps until `deadline` unless a stop is requested first.
fn wait_until(shared: &Shared, deadline: Instant) {
    while !shared.stopped.load(Ordering::Acquire) {
        let now = Instant::now();
        if now >= deadline {
            return;
        }
        thread::park_timeout(deadline - now);
    }
}

fn run_worker(shared: &Shared, load_percent: u32) {
    shared.ready.fetch_add(1, Ordering::AcqRel);
    while !shared.released.load(Ordering::Acquire) {
        if shared.stopped.load(Ordering::Acquire) {
            return;
        }
        thread::park_timeout(Duration::from_millis(10));
    }
    let work = CYCLE * load_percent / 100;
    let mut value = 1.0;
    while !shared.stopped.load(Ordering::Acquire) {
        let cycle_start = Instant::now();
        while cycle_start.elapsed() < work {
            value = work_chunk(value);
            if shared.stopped.load(Ordering::Acquire) {
                return;
            }
        }
        wait_until(shared, cycle_start + CYCLE);
    }
    black_box(value);
}

impl Workers {
    pub fn start(&mut self, count: u32, load_percent: u32) -> Result<(), StartError> {
        for index in 1..=count {
            let shared = Arc::clone(&self.shared);
            let spawned = thread::Builder::new()
                .name(format!("hardware-validator-cpu-{index}"))
                .spawn(move || run_worker(&shared, load_percent));
            match spawned {
                Ok(handle) => self.handles.push(handle),
                Err(_) => {
                    let message = format!("Unable to start CPU load worker {index}.");
                    return Err(self.abort(message));
                }
            }
        }
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        while self.shared.ready.load(Ordering::Acquire) < count {
            if let Some(index) = self.finished() {
                return Err(self.abort(format!("CPU load worker {index} exited during startup.")));
            }
            if Instant::now() >= deadline {
                let message = "CPU load workers did not become ready before timeout.";
                return Err(self.abort(message.to_owned()));
            }
            thread::sleep(Duration::from_millis(10));
        }
        Ok(())
    }

    fn abort(&mut self, message: String) -> StartError {
        let panicked = self.join();
        let panicked: Vec<String> = panicked.iter().map(u32::to_string).collect();
        let warnings = if panicked.is_empty() {
            Vec::new()
        } else {
            vec![format!("CPU load workers panicked during startup: {}.", panicked.join(", "))]
        };
        StartError { message, warnings }
    }

    fn unpark_all(&self) {
        for handle in &self.handles {
            handle.thread().unpark();
        }
    }

    pub fn release(&self) {
        self.shared.released.store(true, Ordering::Release);
        self.unpark_all();
    }

    pub fn request_stop(&self) {
        self.shared.stopped.store(true, Ordering::Release);
        self.unpark_all();
    }

    pub fn alive(&self) -> u32 {
        self.handles.iter().filter(|handle| !handle.is_finished()).count() as u32
    }

    /// 1-based index of the first worker that has already exited.
    fn finished(&self) -> Option<u32> {
        let index = self.handles.iter().position(JoinHandle::is_finished)?;
        Some(index as u32 + 1)
    }

    pub fn unexpected_exit(&self) -> Option<u32> {
        if self.shared.stopped.load(Ordering::Acquire) { None } else { self.finished() }
    }

    /// Requests a stop and joins every worker; returns indexes that panicked.
    pub fn join(&mut self) -> Vec<u32> {
        self.request_stop();
        let mut panicked = Vec::new();
        for (index, handle) in self.handles.drain(..).enumerate() {
            if handle.join().is_err() {
                panicked.push(index as u32 + 1);
            }
        }
        panicked
    }
}

impl Drop for Workers {
    fn drop(&mut self) {
        self.join();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn workers_wait_for_release_and_stop_cooperatively() {
        let mut workers = Workers::default();
        workers.start(3, 50).unwrap();
        assert_eq!(workers.alive(), 3);
        assert_eq!(workers.unexpected_exit(), None);
        workers.release();
        thread::sleep(Duration::from_millis(150));
        assert_eq!(workers.alive(), 3);
        let started = Instant::now();
        assert!(workers.join().is_empty());
        assert!(started.elapsed() < Duration::from_secs(1), "stop takes at most one cycle");
        assert_eq!(workers.alive(), 0);
    }

    #[test]
    fn stop_before_release_ends_idle_workers() {
        let mut workers = Workers::default();
        workers.start(2, 10).unwrap();
        assert!(workers.join().is_empty());
    }
}
