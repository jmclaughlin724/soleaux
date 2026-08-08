//! Supported local-connector materialization.
//!
//! The matrix treats Desktop extensions and supported local connectors as
//! configuration/materialization targets: this module produces the
//! `mcpServers` snippet as a value, and the user applies it through
//! Desktop's own supported configuration flow. The adapter never locates or
//! writes Desktop's configuration store.

use crate::types::DesktopAdapterError;
use serde_json::{Value, json};

fn invalid(detail: impl Into<String>) -> DesktopAdapterError {
    DesktopAdapterError::InvalidConnector {
        detail: detail.into(),
    }
}

/// Materialize one local MCP connector entry in the `mcpServers` shape
/// Desktop's supported configuration flow accepts.
pub fn local_connector_materialization(
    server_name: &str,
    command: &str,
    args: &[&str],
) -> Result<Value, DesktopAdapterError> {
    if server_name.is_empty()
        || !server_name
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
    {
        return Err(invalid(format!(
            "server name {server_name:?} must be non-empty ASCII alphanumeric, '-', or '_'"
        )));
    }
    if command.trim().is_empty() {
        return Err(invalid("command must be non-empty"));
    }
    Ok(json!({
        "mcpServers": {
            server_name: {
                "command": command,
                "args": args,
            }
        }
    }))
}

/// The Soleaux attachment as a local connector: one bounded MCP server over
/// `soleaux serve <repository>`.
pub fn soleaux_local_connector(repository: &str) -> Result<Value, DesktopAdapterError> {
    if repository.trim().is_empty() {
        return Err(invalid("repository path must be non-empty"));
    }
    local_connector_materialization("soleaux", "soleaux", &["serve", repository])
}
