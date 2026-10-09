//! Detection completeness and inventory aggregation.

use crate::models::{CpuInfo, GpuInventory, MemoryInfo, NetworkInfo, StorageInfo, SystemInfo};

/// Internal completeness state; never presented as health, PASS, or FAIL.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DetectionStatus {
    Complete,
    Partial,
    Unavailable,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DetectionResult<T> {
    pub value: T,
    pub status: DetectionStatus,
    /// Empty only for complete results.
    pub issues: Vec<String>,
}

impl<T> DetectionResult<T> {
    pub fn complete(value: T) -> Self {
        Self { value, status: DetectionStatus::Complete, issues: Vec::new() }
    }

    pub fn unavailable(value: T, issue: &str) -> Self {
        Self { value, status: DetectionStatus::Unavailable, issues: vec![issue.to_owned()] }
    }

    /// Standard outcome rule shared by detectors: issues make a result partial
    /// when anything useful was found, unavailable otherwise.
    pub fn from_issues(value: T, issues: Issues, useful: bool, nothing_found: &str) -> Self {
        match (issues.0.is_empty(), useful) {
            (true, true) => Self::complete(value),
            (true, false) => Self::unavailable(value, nothing_found),
            (false, true) => Self { value, status: DetectionStatus::Partial, issues: issues.0 },
            (false, false) => {
                Self { value, status: DetectionStatus::Unavailable, issues: issues.0 }
            }
        }
    }
}

/// Ordered, de-duplicated detector issues.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct Issues(pub Vec<String>);

impl Issues {
    pub fn push(&mut self, issue: impl Into<String>) {
        let issue = issue.into();
        if !self.0.contains(&issue) {
            self.0.push(issue);
        }
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct InventoryResults {
    pub system: DetectionResult<SystemInfo>,
    pub cpu: DetectionResult<CpuInfo>,
    pub memory: DetectionResult<MemoryInfo>,
    pub storage: DetectionResult<StorageInfo>,
    pub gpu: DetectionResult<GpuInventory>,
    pub network: DetectionResult<NetworkInfo>,
}
