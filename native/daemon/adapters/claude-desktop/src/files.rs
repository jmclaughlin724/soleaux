//! The crate's only filesystem surface, confined to user-authorized paths.
//!
//! Both functions take a path the user explicitly provided: the export file
//! they downloaded from Desktop, or the destination they chose for a
//! rendered document. Nothing in this crate resolves, guesses, or accepts a
//! Desktop database or configuration-store location — a test scans the
//! sources and fails if filesystem access appears anywhere else.

use crate::types::DesktopAdapterError;
use serde_json::Value;
use std::path::Path;

fn io_error(path: &Path, error: std::io::Error) -> DesktopAdapterError {
    DesktopAdapterError::Io {
        path: path.display().to_string(),
        detail: error.to_string(),
    }
}

/// Read a user-provided export file's bytes.
pub fn read_export_file(path: &Path) -> Result<Vec<u8>, DesktopAdapterError> {
    std::fs::read(path).map_err(|error| io_error(path, error))
}

/// Write a rendered export document to a user-authorized destination.
pub fn write_export_file(path: &Path, document: &Value) -> Result<(), DesktopAdapterError> {
    let mut rendered =
        serde_json::to_string_pretty(document).map_err(|error| DesktopAdapterError::Io {
            path: path.display().to_string(),
            detail: error.to_string(),
        })?;
    rendered.push('\n');
    std::fs::write(path, rendered).map_err(|error| io_error(path, error))
}
