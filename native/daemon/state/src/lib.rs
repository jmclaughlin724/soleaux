//! Canonical local state for Soleaux sessions, memory, handoffs, runs, and control-plane records.
//!
//! The state database is separate from the repository index database. Each database owns one
//! serialized SQLite writer, while reads use short-lived read-only connections. The canonical
//! model is deliberately platform-neutral and stores vendor-native identifiers only as mappings.

mod model;
mod store;

pub use model::*;
pub use store::{SCHEMA_VERSION, StateStore};
