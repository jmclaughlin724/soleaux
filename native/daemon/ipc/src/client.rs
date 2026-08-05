use crate::{IPC_MAX_FRAME_BYTES, IPC_SCHEMA_VERSION, IpcRequest, IpcResponse, IpcStatus};
use anyhow::{Context, Result, bail};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct IpcClient {
    endpoint: PathBuf,
}

impl IpcClient {
    pub fn new(endpoint: impl AsRef<Path>) -> Self {
        Self {
            endpoint: endpoint.as_ref().to_path_buf(),
        }
    }

    pub fn endpoint(&self) -> &Path {
        &self.endpoint
    }

    #[cfg(unix)]
    pub async fn call(&self, request: IpcRequest) -> Result<IpcResponse> {
        use tokio::{
            io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
            net::UnixStream,
        };

        if request.schema_version != IPC_SCHEMA_VERSION {
            bail!("unsupported local IPC request schema");
        }
        let stream = UnixStream::connect(&self.endpoint)
            .await
            .with_context(|| format!("connecting to Soleaux IPC {}", self.endpoint.display()))?;
        let (reader, mut writer) = stream.into_split();
        let encoded = serde_json::to_vec(&request)?;
        if encoded.len() > IPC_MAX_FRAME_BYTES {
            bail!("local IPC request exceeds one MiB");
        }
        writer.write_all(&encoded).await?;
        writer.write_all(b"\n").await?;
        writer.flush().await?;

        let mut reader = BufReader::new(reader);
        let mut frame = Vec::new();
        let length = reader.read_until(b'\n', &mut frame).await?;
        if length == 0 {
            bail!("Soleaux IPC closed before returning a response");
        }
        if frame.len() > IPC_MAX_FRAME_BYTES {
            bail!("local IPC response exceeds one MiB");
        }
        if frame.last() == Some(&b'\n') {
            frame.pop();
        }
        let response: IpcResponse =
            serde_json::from_slice(&frame).context("decoding Soleaux IPC response")?;
        if response.schema_version != IPC_SCHEMA_VERSION || response.request_id != request.request_id {
            bail!("Soleaux IPC response identity does not match the request");
        }
        if response.status == IpcStatus::Error {
            let error = response
                .error
                .as_ref()
                .context("error response omitted its typed error")?;
            bail!("{}: {}", error.code, error.message);
        }
        Ok(response)
    }

    #[cfg(not(unix))]
    pub async fn call(&self, _request: IpcRequest) -> Result<IpcResponse> {
        bail!("Soleaux local IPC is not yet available on this operating system")
    }
}
