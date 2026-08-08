//! Write-mode policy around [`OpencodeClient`].
//!
//! The adapter is born read-only. Write mode requires, together: a probed
//! server version equal to [`crate::PINNED_OPENCODE_VERSION`], and a
//! daemon-issued admission receipt for the `opencode` platform that a
//! daemon-trusted verifier accepts (P5-V1). The receipt's expiry is
//! re-checked before every mutation, so an expired admission demotes the
//! adapter instead of letting one stale grant keep writing. No path here —
//! or anywhere in this crate — touches OpenCode's on-disk stores.

use crate::client::OpencodeClient;
use crate::types::{
    CreateSessionRequest, HealthInfo, MessageEnvelope, OpencodeConfig, PermissionReply,
    PermissionRequest, RevertRequest, Session, SummarizeRequest,
};
use crate::{OPENCODE_PLATFORM_ID, PINNED_OPENCODE_VERSION};
use anyhow::Result;
use soleaux_ipc::{AdmissionReceipt, IpcClient, IpcMethod, IpcRequest, IpcStatus};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

/// Typed refusal reasons for the safe-mode gate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AdapterError {
    /// The probed server version is not the pinned matrix version; the
    /// adapter is permanently read-only for this connection.
    VersionUnpinned {
        probed: String,
        pinned: &'static str,
    },
    /// A mutation was attempted without an admitted write mode.
    ReadOnly,
    /// The admission receipt expired; the adapter demoted itself.
    AdmissionExpired,
    /// The presented receipt does not name the OpenCode platform at the
    /// pinned version.
    ReceiptMismatch(String),
    /// The daemon-trusted verifier rejected the receipt.
    VerifierRejected(String),
}

impl std::fmt::Display for AdapterError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::VersionUnpinned { probed, pinned } => write!(
                formatter,
                "opencode server version {probed} is not the pinned matrix version {pinned}; the adapter stays in read-only safe mode"
            ),
            Self::ReadOnly => write!(
                formatter,
                "the opencode adapter is in read-only safe mode; mutations require a verified admission receipt"
            ),
            Self::AdmissionExpired => write!(
                formatter,
                "the opencode admission receipt expired; the adapter returned to read-only safe mode"
            ),
            Self::ReceiptMismatch(detail) => {
                write!(formatter, "admission receipt mismatch: {detail}")
            }
            Self::VerifierRejected(detail) => {
                write!(formatter, "admission receipt verification failed: {detail}")
            }
        }
    }
}

impl std::error::Error for AdapterError {}

/// Daemon-trusted verification of an admission receipt. Implementations must
/// consult the daemon (the only MAC holder); a caller-computed acceptance is
/// exactly what the admission design forbids.
pub trait AdmissionVerifier {
    fn verify(
        &self,
        receipt: &AdmissionReceipt,
    ) -> impl Future<Output = Result<(), AdapterError>> + Send;
}

/// Verifies receipts through the daemon's `admission_verify` IPC method.
pub struct IpcAdmissionVerifier {
    client: IpcClient,
}

impl IpcAdmissionVerifier {
    pub fn new(endpoint: impl AsRef<Path>) -> Self {
        Self {
            client: IpcClient::new(endpoint),
        }
    }
}

impl AdmissionVerifier for IpcAdmissionVerifier {
    async fn verify(&self, receipt: &AdmissionReceipt) -> Result<(), AdapterError> {
        let request = IpcRequest::new(IpcMethod::AdmissionVerify {
            receipt: receipt.clone(),
        });
        let response = self
            .client
            .call(request)
            .await
            .map_err(|error| AdapterError::VerifierRejected(format!("{error:#}")))?;
        if response.status != IpcStatus::Ok {
            let detail = response
                .error
                .map(|error| error.message)
                .unwrap_or_else(|| "daemon rejected the receipt".to_string());
            return Err(AdapterError::VerifierRejected(detail));
        }
        Ok(())
    }
}

/// The adapter's current authorization toward the OpenCode server.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WriteMode {
    /// Safe mode: read operations only.
    ReadOnly,
    /// Admitted: mutations allowed while the bound receipt stays valid.
    ReadWrite { expires_at_unix_ms: i64 },
}

/// Policy wrapper owning the read-only/read-write decision for one probed
/// OpenCode server connection.
pub struct OpencodeAdapter {
    client: OpencodeClient,
    probed: HealthInfo,
    mode: WriteMode,
}

impl OpencodeAdapter {
    /// Probe the server and start in read-only safe mode. An unhealthy
    /// server refuses connection outright.
    pub async fn connect(client: OpencodeClient) -> Result<Self> {
        let probed = client.health().await?;
        if !probed.healthy {
            anyhow::bail!("opencode server reports unhealthy");
        }
        Ok(Self {
            client,
            probed,
            mode: WriteMode::ReadOnly,
        })
    }

    pub fn probed_version(&self) -> &str {
        &self.probed.version
    }

    pub fn version_pinned(&self) -> bool {
        self.probed.version == PINNED_OPENCODE_VERSION
    }

    pub fn mode(&self) -> &WriteMode {
        &self.mode
    }

    /// Direct access for read paths not wrapped below.
    pub fn client(&self) -> &OpencodeClient {
        &self.client
    }

    /// Enter write mode. Fails closed on: unpinned server version, a receipt
    /// naming another platform or version, an expired receipt, or verifier
    /// rejection — the adapter stays read-only in every failure case.
    pub async fn enable_write<V: AdmissionVerifier>(
        &mut self,
        receipt: &AdmissionReceipt,
        verifier: &V,
    ) -> Result<(), AdapterError> {
        if !self.version_pinned() {
            return Err(AdapterError::VersionUnpinned {
                probed: self.probed.version.clone(),
                pinned: PINNED_OPENCODE_VERSION,
            });
        }
        if receipt.platform != OPENCODE_PLATFORM_ID {
            return Err(AdapterError::ReceiptMismatch(format!(
                "receipt platform {} is not {OPENCODE_PLATFORM_ID}",
                receipt.platform
            )));
        }
        if receipt.client_version != PINNED_OPENCODE_VERSION {
            return Err(AdapterError::ReceiptMismatch(format!(
                "receipt version {} is not the pinned {PINNED_OPENCODE_VERSION}",
                receipt.client_version
            )));
        }
        if receipt.expires_at_unix_ms <= now_unix_ms() {
            return Err(AdapterError::AdmissionExpired);
        }
        verifier.verify(receipt).await?;
        self.mode = WriteMode::ReadWrite {
            expires_at_unix_ms: receipt.expires_at_unix_ms,
        };
        Ok(())
    }

    /// Drop back to read-only safe mode.
    pub fn disable_write(&mut self) {
        self.mode = WriteMode::ReadOnly;
    }

    fn require_write(&mut self) -> Result<(), AdapterError> {
        match self.mode {
            WriteMode::ReadOnly => Err(AdapterError::ReadOnly),
            WriteMode::ReadWrite { expires_at_unix_ms } => {
                if expires_at_unix_ms <= now_unix_ms() {
                    self.mode = WriteMode::ReadOnly;
                    return Err(AdapterError::AdmissionExpired);
                }
                Ok(())
            }
        }
    }

    // Read surface: always available.

    pub async fn list_sessions(&self) -> Result<Vec<Session>> {
        self.client.list_sessions().await
    }

    pub async fn get_session(&self, session_id: &str) -> Result<Session> {
        self.client.get_session(session_id).await
    }

    pub async fn session_children(&self, session_id: &str) -> Result<Vec<Session>> {
        self.client.session_children(session_id).await
    }

    pub async fn list_messages(&self, session_id: &str) -> Result<Vec<MessageEnvelope>> {
        self.client.list_messages(session_id).await
    }

    pub async fn list_permissions(&self) -> Result<Vec<PermissionRequest>> {
        self.client.list_permissions().await
    }

    pub async fn config(&self) -> Result<OpencodeConfig> {
        self.client.config().await
    }

    // Mutation surface: admitted write mode only, checked per call.

    pub async fn create_session(&mut self, request: &CreateSessionRequest) -> Result<Session> {
        self.require_write()?;
        self.client.create_session(request).await
    }

    pub async fn fork_session(
        &mut self,
        session_id: &str,
        message_id: Option<&str>,
    ) -> Result<Session> {
        self.require_write()?;
        self.client.fork_session(session_id, message_id).await
    }

    pub async fn abort_session(&mut self, session_id: &str) -> Result<bool> {
        self.require_write()?;
        self.client.abort_session(session_id).await
    }

    pub async fn summarize_session(
        &mut self,
        session_id: &str,
        request: &SummarizeRequest,
    ) -> Result<bool> {
        self.require_write()?;
        self.client.summarize_session(session_id, request).await
    }

    pub async fn revert_session(
        &mut self,
        session_id: &str,
        request: &RevertRequest,
    ) -> Result<Session> {
        self.require_write()?;
        self.client.revert_session(session_id, request).await
    }

    pub async fn unrevert_session(&mut self, session_id: &str) -> Result<Session> {
        self.require_write()?;
        self.client.unrevert_session(session_id).await
    }

    pub async fn reply_permission(
        &mut self,
        request_id: &str,
        reply: PermissionReply,
        message: Option<&str>,
    ) -> Result<bool> {
        self.require_write()?;
        self.client
            .reply_permission(request_id, reply, message)
            .await
    }

    pub async fn respond_session_permission(
        &mut self,
        session_id: &str,
        permission_id: &str,
        response: PermissionReply,
    ) -> Result<bool> {
        self.require_write()?;
        self.client
            .respond_session_permission(session_id, permission_id, response)
            .await
    }
}

fn now_unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as i64)
        .unwrap_or_default()
}
