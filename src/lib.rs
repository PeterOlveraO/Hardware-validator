//! Hardware Validator: a non-invasive local Linux hardware inventory and an
//! explicit, bounded CPU load test.

#[cfg(not(target_os = "linux"))]
compile_error!("Hardware Validator reads Linux procfs and sysfs and supports Linux only.");

pub mod cli;
pub mod collector;
pub mod cpu_test;
pub mod detectors;
pub mod host;
pub mod models;
pub mod report;
pub mod text;

#[cfg(test)]
mod testutil;
