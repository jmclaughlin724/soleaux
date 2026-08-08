//! Daemon-trusted admission verification for the write path (P5-V1).
//!
//! Implementations must consult the daemon (the only MAC holder); a
//! caller-computed acceptance is exactly what the admission design forbids.

use crate::host::HostError;
use soleaux_ipc::{AdmissionReceipt, IpcClient, IpcMethod, IpcRequest, IpcStatus};
use std::path::Path;

pub trait AdmissionVerifier {
    fn verify(
        &self,
        receipt: &AdmissionReceipt,
    ) -> impl Future<Output = Result<(), HostError>> + Send;
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
    async fn verify(&self, receipt: &AdmissionReceipt) -> Result<(), HostError> {
        let request = IpcRequest::new(IpcMethod::AdmissionVerify {
            receipt: receipt.clone(),
        });
        let response = self
            .client
            .call(request)
            .await
            .map_err(|error| HostError::VerifierRejected(format!("{error:#}")))?;
        if response.status != IpcStatus::Ok {
            let detail = response
                .error
                .map(|error| error.message)
                .unwrap_or_else(|| "daemon rejected the receipt".to_string());
            return Err(HostError::VerifierRejected(detail));
        }
        Ok(())
    }
}
