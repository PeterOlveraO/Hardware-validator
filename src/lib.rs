//! Hardware Validator: a non-invasive local Linux hardware inventory and an
//! explicit, bounded CPU load test.

#[cfg(not(target_os = "linux"))]
compile_error!("Hardware Validator reads Linux procfs and sysfs and supports Linux only.");

pub mod collector;
pub mod host;
pub mod models;
pub mod text;
