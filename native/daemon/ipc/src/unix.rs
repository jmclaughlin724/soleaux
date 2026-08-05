use crate::{
    IPC_MAX_FRAME_BYTES, IpcMethod, IpcRequest, IpcResponse, IpcServer,
    server::{EndpointCleanup, write_pid},
};
use anyhow::{Context, Result, bail};
use std::{fs, path::Path};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::{UnixListener, UnixStream, unix::OwnedWriteHalf},
    sync::watch,
    task::JoinSet,
};

pub(crate) async fn run_server(server: IpcServer) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    prepare_endpoint(&server.paths().endpoint)?;
    let listener = UnixListener::bind(&server.paths().endpoint)
        .with_context(|| format!("binding Soleaux IPC {}", server.paths().endpoint.display()))?;
    fs::set_permissions(&server.paths().endpoint, fs::Permissions::from_mode(0o600)).with_context(
        || {
            format!(
                "restricting Soleaux IPC {}",
                server.paths().endpoint.display()
            )
        },
    )?;
    write_pid(&server.paths().pid_file)?;
    let cleanup = EndpointCleanup::new(
        server.paths().endpoint.clone(),
        server.paths().pid_file.clone(),
    );
    let (shutdown, mut shutdown_receiver) = watch::channel(false);
    let mut clients = JoinSet::new();

    loop {
        tokio::select! {
            changed = shutdown_receiver.changed() => {
                if changed.is_err() || *shutdown_receiver.borrow() {
                    break;
                }
            }
            accepted = listener.accept() => {
                let (stream, _) = accepted.context("accepting Soleaux IPC client")?;
                validate_peer(&stream)?;
                let server = server.clone();
                let shutdown = shutdown.clone();
                clients.spawn(async move {
                    if let Err(error) = handle_client(server, stream, shutdown).await {
                        tracing::warn!(error = %error, "Soleaux IPC client failed");
                    }
                });
            }
        }
    }
    drop(listener);
    while clients.join_next().await.is_some() {}
    drop(cleanup);
    Ok(())
}

async fn handle_client(
    server: IpcServer,
    stream: UnixStream,
    shutdown: watch::Sender<bool>,
) -> Result<()> {
    let (reader, mut writer) = stream.into_split();
    let mut reader = BufReader::new(reader);
    loop {
        let mut frame = Vec::new();
        let length = reader.read_until(b'\n', &mut frame).await?;
        if length == 0 {
            break;
        }
        if frame.len() > IPC_MAX_FRAME_BYTES {
            let response = IpcResponse::error(
                uuid::Uuid::nil(),
                "frame_too_large",
                "local IPC frame exceeds one MiB",
            );
            write_response(&mut writer, &response).await?;
            break;
        }
        if frame.last() == Some(&b'\n') {
            frame.pop();
        }
        let request: IpcRequest = match serde_json::from_slice(&frame) {
            Ok(request) => request,
            Err(error) => {
                let response = IpcResponse::error(
                    uuid::Uuid::nil(),
                    "invalid_request",
                    format!("invalid Soleaux IPC request: {error}"),
                );
                write_response(&mut writer, &response).await?;
                continue;
            }
        };
        let should_shutdown = matches!(request.method, IpcMethod::Shutdown);
        let response = server.dispatch(&request).await;
        write_response(&mut writer, &response).await?;
        if should_shutdown && response.error.is_none() {
            let _ = shutdown.send(true);
            break;
        }
    }
    Ok(())
}

async fn write_response(writer: &mut OwnedWriteHalf, response: &IpcResponse) -> Result<()> {
    let encoded = serde_json::to_vec(response)?;
    if encoded.len() > IPC_MAX_FRAME_BYTES {
        bail!("local IPC response exceeds one MiB");
    }
    writer.write_all(&encoded).await?;
    writer.write_all(b"\n").await?;
    writer.flush().await?;
    Ok(())
}

fn validate_peer(stream: &UnixStream) -> Result<()> {
    let credential = stream
        .peer_cred()
        .context("reading local IPC peer credentials")?;
    let current_uid = rustix::process::geteuid().as_raw();
    if credential.uid() != current_uid {
        bail!("local IPC peer uid does not match the Soleaux daemon user");
    }
    Ok(())
}

fn prepare_endpoint(endpoint: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let parent = endpoint.parent().context("IPC endpoint has no parent")?;
    fs::create_dir_all(parent)
        .with_context(|| format!("creating IPC runtime directory {}", parent.display()))?;
    fs::set_permissions(parent, fs::Permissions::from_mode(0o700))
        .with_context(|| format!("restricting IPC runtime directory {}", parent.display()))?;
    if endpoint.exists() {
        match std::os::unix::net::UnixStream::connect(endpoint) {
            Ok(_) => bail!("another Soleaux daemon is already listening"),
            Err(_) => fs::remove_file(endpoint)
                .with_context(|| format!("removing stale IPC endpoint {}", endpoint.display()))?,
        }
    }
    Ok(())
}
