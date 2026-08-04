use anyhow::{Context, Result, bail};
use clap::Args;
use std::{
    env,
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpStream,
    process::{Child, Command},
    time::{sleep, timeout},
};

const TELEMETRY_ADDRESS: &str = "127.0.0.1:43120";
const DASHBOARD_ADDRESS: &str = "127.0.0.1:43121";
const READINESS_ATTEMPTS: u32 = 60;
const READINESS_INTERVAL: Duration = Duration::from_millis(250);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(5);

#[derive(Debug, Args)]
pub struct UpArguments {
    #[arg(default_value = ".")]
    pub repo: PathBuf,
    #[arg(long)]
    pub dashboard_dist: Option<PathBuf>,
    #[arg(long, default_value = "127.0.0.1:37432")]
    pub mcp_address: String,
}

pub async fn run(soleauxd: &Path, arguments: UpArguments) -> Result<()> {
    let repo = std::fs::canonicalize(&arguments.repo)
        .with_context(|| format!("resolving repository {}", arguments.repo.display()))?;
    let telemetry_daemon = telemetry_daemon_executable()?;
    let mcp_token = uuid::Uuid::new_v4().simple().to_string();
    let dashboard_dist = resolve_dashboard_dist(arguments.dashboard_dist.clone(), &repo);

    let mut telemetry_command = Command::new(&telemetry_daemon);
    telemetry_command.stdin(Stdio::null()).kill_on_drop(true);
    match &dashboard_dist {
        Some(dist) => {
            telemetry_command.env("SOLEAUX_DASHBOARD_DIST", dist);
        }
        None => {
            telemetry_command.env_remove("SOLEAUX_DASHBOARD_DIST");
        }
    }
    let mut telemetry_child = telemetry_command
        .spawn()
        .with_context(|| format!("starting telemetry daemon {}", telemetry_daemon.display()))?;

    let mut mcp_command = Command::new(soleauxd);
    mcp_command
        .arg("http")
        .arg(&repo)
        .arg("--address")
        .arg(&arguments.mcp_address)
        .env("SOLEAUX_MCP_HTTP_TOKEN", &mcp_token)
        .stdin(Stdio::null())
        .kill_on_drop(true);
    let mut mcp_child = mcp_command
        .spawn()
        .with_context(|| format!("starting MCP server {}", soleauxd.display()))?;

    let readiness = ready_stack(
        &mut telemetry_child,
        &mut mcp_child,
        dashboard_dist.as_deref(),
        &arguments.mcp_address,
        &mcp_token,
    )
    .await;
    if let Err(error) = readiness {
        shutdown_children(&mut telemetry_child, &mut mcp_child).await;
        return Err(error);
    }

    print_status(
        &repo,
        dashboard_dist.as_deref(),
        &arguments.mcp_address,
        &mcp_token,
    );
    let outcome = supervise(&mut telemetry_child, &mut mcp_child).await;
    shutdown_children(&mut telemetry_child, &mut mcp_child).await;
    outcome
}

fn telemetry_daemon_executable() -> Result<PathBuf> {
    if let Some(value) = env::var_os("SOLEAUX_TELEMETRY_DAEMON") {
        return Ok(PathBuf::from(value));
    }
    let current = env::current_exe().context("resolving soleaux executable")?;
    let sibling = current.with_file_name(if cfg!(windows) {
        "soleaux-daemon.exe"
    } else {
        "soleaux-daemon"
    });
    if sibling.is_file() {
        return Ok(sibling);
    }
    Ok(PathBuf::from("soleaux-daemon"))
}

fn resolve_dashboard_dist(flag: Option<PathBuf>, repo: &Path) -> Option<PathBuf> {
    if let Some(dist) = flag {
        return Some(dist);
    }
    if let Some(value) = env::var_os("SOLEAUX_DASHBOARD_DIST")
        && !value.is_empty()
    {
        return Some(PathBuf::from(value));
    }
    let default = repo.join("telemetry").join("dashboard").join("out");
    default.is_dir().then_some(default)
}

async fn ready_stack(
    telemetry_child: &mut Child,
    mcp_child: &mut Child,
    dashboard_dist: Option<&Path>,
    mcp_address: &str,
    mcp_token: &str,
) -> Result<()> {
    wait_until_ready(
        telemetry_child,
        "telemetry daemon",
        TELEMETRY_ADDRESS,
        "/api/v1/health",
        None,
    )
    .await?;
    if dashboard_dist.is_some() {
        wait_until_ready(telemetry_child, "dashboard", DASHBOARD_ADDRESS, "/", None).await?;
    }
    wait_until_ready(
        mcp_child,
        "MCP server",
        mcp_address,
        "/health",
        Some(mcp_token),
    )
    .await
}

async fn wait_until_ready(
    child: &mut Child,
    name: &str,
    address: &str,
    path: &str,
    bearer: Option<&str>,
) -> Result<()> {
    for _ in 0..READINESS_ATTEMPTS {
        if let Some(status) = child.try_wait().context("polling child process")? {
            bail!("{name} exited during startup with {status}");
        }
        if let Ok(200) = http_status(address, path, bearer).await {
            return Ok(());
        }
        sleep(READINESS_INTERVAL).await;
    }
    bail!("{name} did not become healthy at http://{address}{path}")
}

async fn http_status(address: &str, path: &str, bearer: Option<&str>) -> Result<u16> {
    let mut stream = TcpStream::connect(address).await?;
    let authorization = match bearer {
        Some(token) => format!("Authorization: Bearer {token}\r\n"),
        None => String::new(),
    };
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {address}\r\n{authorization}Connection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).await?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response).await?;
    let head = String::from_utf8_lossy(&response);
    head.split_whitespace()
        .nth(1)
        .context("missing HTTP status line")?
        .parse()
        .context("invalid HTTP status code")
}

fn print_status(repo: &Path, dashboard_dist: Option<&Path>, mcp_address: &str, mcp_token: &str) {
    println!("Soleaux product stack is up");
    println!("  repository        {}", repo.display());
    println!("  telemetry daemon  http://{TELEMETRY_ADDRESS}/api/v1/health");
    match dashboard_dist {
        Some(dist) => {
            println!(
                "  dashboard         http://{DASHBOARD_ADDRESS}/ ({})",
                dist.display()
            );
        }
        None => {
            println!(
                "  dashboard         not served; run `pnpm --dir telemetry/dashboard build:export` first"
            );
        }
    }
    println!("  mcp streamable    http://{mcp_address}/mcp");
    println!("  mcp bearer token  {mcp_token}");
    println!(
        "  mcp stdio config  {{\"command\":\"soleauxd\",\"args\":[\"serve\",\"{}\"],\"type\":\"stdio\"}}",
        repo.display()
    );
    println!("Press Ctrl-C to stop");
}

async fn supervise(telemetry_child: &mut Child, mcp_child: &mut Child) -> Result<()> {
    let interrupted = shutdown_requested();
    tokio::pin!(interrupted);
    tokio::select! {
        _ = &mut interrupted => Ok(()),
        status = telemetry_child.wait() => {
            bail!("telemetry daemon exited unexpectedly with {}", status.context("waiting for telemetry daemon")?)
        }
        status = mcp_child.wait() => {
            bail!("MCP server exited unexpectedly with {}", status.context("waiting for MCP server")?)
        }
    }
}

#[cfg(unix)]
async fn shutdown_requested() {
    use tokio::signal::unix::{SignalKind, signal};
    let mut terminate = match signal(SignalKind::terminate()) {
        Ok(stream) => stream,
        Err(_) => {
            let _ = tokio::signal::ctrl_c().await;
            return;
        }
    };
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {}
        _ = terminate.recv() => {}
    }
}

#[cfg(not(unix))]
async fn shutdown_requested() {
    let _ = tokio::signal::ctrl_c().await;
}

async fn shutdown_children(telemetry_child: &mut Child, mcp_child: &mut Child) {
    request_stop(telemetry_child);
    request_stop(mcp_child);
    reap(telemetry_child).await;
    reap(mcp_child).await;
}

#[cfg(unix)]
fn request_stop(child: &mut Child) {
    if matches!(child.try_wait(), Ok(Some(_))) {
        return;
    }
    match child.id() {
        Some(pid) => unsafe {
            libc::kill(pid as libc::pid_t, libc::SIGTERM);
        },
        None => {
            let _ = child.start_kill();
        }
    }
}

#[cfg(not(unix))]
fn request_stop(child: &mut Child) {
    let _ = child.start_kill();
}

async fn reap(child: &mut Child) {
    if timeout(SHUTDOWN_GRACE, child.wait()).await.is_err() {
        let _ = child.start_kill();
        let _ = child.wait().await;
    }
}
