//! The adapter facade over one canonical [`StateStore`].
//!
//! Unlike the runtime adapters, there is no write mode to gate: the matrix
//! pins Claude Desktop as a read-only documentation contract, so this type
//! exposes no vendor mutation surface at all and no admission receipt path.
//! Its only writes are canonical-store imports of user-provided export
//! files; its only file writes are rendered documents at user-authorized
//! destinations.

use crate::export::export_session;
use crate::files::{read_export_file, write_export_file};
use crate::import::{ImportReport, import_export};
use crate::types::{
    DesktopAdapterError, DesktopConversation, ParsedExport, parse_export, render_conversations,
};
use soleaux_state::StateStore;
use std::path::Path;
use uuid::Uuid;

pub struct ClaudeDesktopAdapter {
    state: StateStore,
}

impl ClaudeDesktopAdapter {
    pub fn new(state: StateStore) -> Self {
        Self { state }
    }

    /// Import a parsed export into `workspace_id`, all-or-nothing per
    /// conversation.
    pub fn import_export(&self, workspace_id: Uuid, export: &ParsedExport) -> ImportReport {
        import_export(&self.state, workspace_id, export)
    }

    /// Read, parse, and import a user-provided export file.
    pub fn import_export_file(
        &self,
        workspace_id: Uuid,
        path: &Path,
    ) -> Result<ImportReport, DesktopAdapterError> {
        let bytes = read_export_file(path)?;
        let export = parse_export(&bytes)?;
        Ok(self.import_export(workspace_id, &export))
    }

    /// Represent one canonical session as a Desktop-shaped conversation.
    pub fn export_session(
        &self,
        session_id: Uuid,
    ) -> Result<DesktopConversation, DesktopAdapterError> {
        export_session(&self.state, session_id)
    }

    /// Render one canonical session into an export-file document at a
    /// user-authorized destination.
    pub fn write_export_file(
        &self,
        session_id: Uuid,
        path: &Path,
    ) -> Result<(), DesktopAdapterError> {
        let conversation = self.export_session(session_id)?;
        write_export_file(
            path,
            &render_conversations(std::slice::from_ref(&conversation)),
        )
    }
}
