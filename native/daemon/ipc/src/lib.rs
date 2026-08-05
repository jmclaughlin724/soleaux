//! Typed local IPC and per-user service lifecycle for the Soleaux daemon.
//!
//! The daemon owns canonical state. Clients use a closed JSONL protocol over a same-user Unix
//! socket; unsupported transports fail closed rather than silently bypassing peer checks.

mod client;
mod paths;
mod protocol;
mod server;
mod service;
#[cfg(unix)]
mod unix;

pub use client::IpcClient;
pub use paths::SoleauxPaths;
pub use protocol::{
    DaemonStatus, IPC_MAX_FRAME_BYTES, IPC_SCHEMA_VERSION, IpcError, IpcMethod, IpcRequest,
    IpcResponse, IpcStatus,
};
pub use server::IpcServer;
pub use service::{
    InstallationReport, ServiceStatus, UninstallReport, install, install_service, render_manifest,
    restart_service, service_status, start_service, stop_service, uninstall,
};

#[cfg(test)]
mod tests;
