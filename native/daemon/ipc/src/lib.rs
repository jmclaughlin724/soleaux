//! Typed local IPC and per-user service lifecycle for the Soleaux daemon.
//!
//! The daemon owns canonical state. Concurrent clients use a closed JSONL protocol over a
//! same-user Unix socket and share the daemon-owned state boundary; unsupported transports fail
//! closed rather than silently bypassing peer checks.

mod admission;
mod client;
mod compatibility;
mod memory;
mod paths;
mod protocol;
mod registry;
mod server;
mod service;
mod session;
#[cfg(unix)]
mod unix;

pub use admission::{ADMISSION_RECEIPT_SCHEMA_VERSION, AdmissionError, AdmissionReceipt};
pub use client::IpcClient;
pub use compatibility::{
    CLIENT_CAPABILITY_MATRIX_JSON, CLIENT_CAPABILITY_MATRIX_SCHEMA_VERSION,
    CLIENT_CAPABILITY_PROBE_SCHEMA_VERSION, client_capability_matrix_sha256,
    client_capability_matrix_summary, validate_client_capability_matrix,
};
pub use paths::SoleauxPaths;
pub use protocol::{
    DaemonStatus, IPC_MAX_FRAME_BYTES, IPC_SCHEMA_VERSION, IpcError, IpcMethod, IpcRequest,
    IpcResponse, IpcStatus,
};
pub use registry::{CLIENT_PROTOCOL_VERSION, REGISTRY_SCHEMA_VERSION};
pub use server::IpcServer;
pub use service::{
    InstallationReport, ServiceStatus, UninstallReport, install, install_service, render_manifest,
    restart_service, service_status, start_service, stop_service, uninstall,
};

#[cfg(test)]
mod tests;
