use crate::{IpcClient, IpcMethod, IpcRequest, SoleauxPaths};
use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct InstallationReport {
    pub cli: String,
    pub daemon: String,
    pub service_manifest: String,
    pub endpoint: String,
    pub state_database: String,
    pub service_installed: bool,
    pub service_started: bool,
    pub production_claim_allowed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ServiceStatus {
    pub installed: bool,
    pub running: bool,
    pub endpoint: String,
    pub manifest: String,
    pub pid: Option<u32>,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct UninstallReport {
    pub stopped: bool,
    pub removed_manifest: bool,
    pub removed_cli: bool,
    pub removed_daemon: bool,
    pub preserved_state: bool,
}

pub fn install(
    paths: &SoleauxPaths,
    cli_source: &Path,
    daemon_source: &Path,
    start: bool,
) -> Result<InstallationReport> {
    paths.create_directories()?;
    verify_binary(cli_source, "soleaux")?;
    verify_binary(daemon_source, "soleauxd")?;
    copy_executable(cli_source, &paths.installed_cli())?;
    copy_executable(daemon_source, &paths.installed_daemon())?;
    let manifest = render_manifest(paths, &paths.installed_daemon())?;
    atomic_write(&paths.service_manifest, manifest.as_bytes())?;
    let service_started = if start {
        start_service(paths)?;
        true
    } else {
        false
    };
    Ok(InstallationReport {
        cli: paths.installed_cli().to_string_lossy().to_string(),
        daemon: paths.installed_daemon().to_string_lossy().to_string(),
        service_manifest: paths.service_manifest.to_string_lossy().to_string(),
        endpoint: paths.endpoint.to_string_lossy().to_string(),
        state_database: paths.state_database.to_string_lossy().to_string(),
        service_installed: true,
        service_started,
        production_claim_allowed: false,
    })
}

pub fn install_service(paths: &SoleauxPaths, daemon: &Path) -> Result<PathBuf> {
    paths.create_directories()?;
    verify_binary(daemon, "soleauxd")?;
    let manifest = render_manifest(paths, daemon)?;
    atomic_write(&paths.service_manifest, manifest.as_bytes())?;
    Ok(paths.service_manifest.clone())
}

pub fn start_service(paths: &SoleauxPaths) -> Result<()> {
    if !paths.service_manifest.is_file() {
        bail!("Soleaux service is not installed");
    }
    platform_start(paths)
}

pub async fn stop_service(paths: &SoleauxPaths) -> Result<bool> {
    if paths.endpoint.exists() {
        let client = IpcClient::new(&paths.endpoint);
        if client.call(IpcRequest::new(IpcMethod::Shutdown)).await.is_ok() {
            wait_for_endpoint_removal(&paths.endpoint).await;
            return Ok(true);
        }
    }
    platform_stop(paths)?;
    Ok(false)
}

pub async fn restart_service(paths: &SoleauxPaths) -> Result<()> {
    let _ = stop_service(paths).await?;
    start_service(paths)
}

pub async fn service_status(paths: &SoleauxPaths) -> Result<ServiceStatus> {
    let installed = paths.service_manifest.is_file();
    let mut running = false;
    let mut detail = if installed {
        "service manifest is installed".to_string()
    } else {
        "service manifest is absent".to_string()
    };
    let mut pid = read_pid(&paths.pid_file);
    if paths.endpoint.exists() {
        let client = IpcClient::new(&paths.endpoint);
        match client.call(IpcRequest::new(IpcMethod::Ping)).await {
            Ok(response) => {
                running = response
                    .result
                    .as_ref()
                    .and_then(|value| value.get("pong"))
                    .and_then(serde_json::Value::as_bool)
                    == Some(true);
                pid = response
                    .result
                    .as_ref()
                    .and_then(|value| value.get("pid"))
                    .and_then(serde_json::Value::as_u64)
                    .and_then(|value| u32::try_from(value).ok())
                    .or(pid);
                detail = "daemon responded to authenticated local IPC".to_string();
            }
            Err(error) => {
                detail = format!("IPC endpoint exists but did not respond: {error}");
            }
        }
    }
    Ok(ServiceStatus {
        installed,
        running,
        endpoint: paths.endpoint.to_string_lossy().to_string(),
        manifest: paths.service_manifest.to_string_lossy().to_string(),
        pid,
        detail,
    })
}

pub async fn uninstall(paths: &SoleauxPaths, preserve_state: bool) -> Result<UninstallReport> {
    let stopped = stop_service(paths).await.unwrap_or(false);
    let removed_manifest = remove_if_file(&paths.service_manifest)?;
    let removed_cli = remove_if_file(&paths.installed_cli())?;
    let removed_daemon = remove_if_file(&paths.installed_daemon())?;
    let _ = remove_if_file(&paths.endpoint);
    let _ = remove_if_file(&paths.pid_file);
    if !preserve_state && paths.home.exists() {
        fs::remove_dir_all(&paths.home)
            .with_context(|| format!("removing Soleaux home {}", paths.home.display()))?;
    }
    Ok(UninstallReport {
        stopped,
        removed_manifest,
        removed_cli,
        removed_daemon,
        preserved_state: preserve_state,
    })
}

pub fn render_manifest(paths: &SoleauxPaths, daemon: &Path) -> Result<String> {
    let daemon = absolute(daemon)?;
    let endpoint = absolute_or_literal(&paths.endpoint)?;
    let state = absolute_or_literal(&paths.state_database)?;
    let log = absolute_or_literal(&paths.log_file)?;
    #[cfg(target_os = "macos")]
    {
        return Ok(format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict>\n<key>Label</key><string>com.soleaux.daemon</string>\n<key>ProgramArguments</key><array><string>{}</string><string>ipc</string><string>--endpoint</string><string>{}</string><string>--state-db</string><string>{}</string></array>\n<key>RunAtLoad</key><true/>\n<key>KeepAlive</key><true/>\n<key>ProcessType</key><string>Interactive</string>\n<key>StandardOutPath</key><string>{}</string>\n<key>StandardErrorPath</key><string>{}</string>\n</dict></plist>\n",
            xml_escape(&daemon),
            xml_escape(&endpoint),
            xml_escape(&state),
            xml_escape(&log),
            xml_escape(&log),
        ));
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        return Ok(format!(
            "[Unit]\nDescription=Soleaux unified repository intelligence\nAfter=default.target\n\n[Service]\nType=simple\nExecStart={} ipc --endpoint {} --state-db {}\nRestart=on-failure\nRestartSec=2\nUMask=0077\nStandardOutput=append:{}\nStandardError=append:{}\nNoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nReadWritePaths={}\n\n[Install]\nWantedBy=default.target\n",
            systemd_escape(&daemon),
            systemd_escape(&endpoint),
            systemd_escape(&state),
            systemd_escape(&log),
            systemd_escape(&log),
            systemd_escape(&paths.home.to_string_lossy()),
        ));
    }
    #[cfg(target_os = "windows")]
    {
        return Ok(format!(
            "<?xml version=\"1.0\" encoding=\"UTF-16\"?><Task version=\"1.4\" xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\"><Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers><Principals><Principal id=\"Author\"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings><Actions Context=\"Author\"><Exec><Command>{}</Command><Arguments>ipc --endpoint &quot;{}&quot; --state-db &quot;{}&quot;</Arguments></Exec></Actions></Task>",
            xml_escape(&daemon),
            xml_escape(&endpoint),
            xml_escape(&state),
        ));
    }
    #[cfg(not(any(unix, target_os = "windows")))]
    {
        let _ = (daemon, endpoint, state, log);
        bail!("Soleaux service manifests are unsupported on this operating system")
    }
}

fn platform_start(paths: &SoleauxPaths) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        run_status(
            Command::new("launchctl")
                .arg("bootstrap")
                .arg(format!("gui/{}", rustix::process::geteuid().as_raw()))
                .arg(&paths.service_manifest),
            "starting launchd service",
        )?;
        return Ok(());
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        run_status(
            Command::new("systemctl")
                .args(["--user", "daemon-reload"]),
            "reloading systemd user units",
        )?;
        run_status(
            Command::new("systemctl")
                .args(["--user", "enable", "--now", "soleaux.service"]),
            "starting systemd user service",
        )?;
        return Ok(());
    }
    #[cfg(target_os = "windows")]
    {
        run_status(
            Command::new("schtasks.exe")
                .args(["/Create", "/F", "/TN", "Soleaux", "/XML"])
                .arg(&paths.service_manifest),
            "registering Windows Soleaux task",
        )?;
        run_status(
            Command::new("schtasks.exe").args(["/Run", "/TN", "Soleaux"]),
            "starting Windows Soleaux task",
        )?;
        return Ok(());
    }
    #[cfg(not(any(unix, target_os = "windows")))]
    {
        let _ = paths;
        bail!("Soleaux service start is unsupported on this operating system")
    }
}

fn platform_stop(paths: &SoleauxPaths) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        let target = format!(
            "gui/{}/com.soleaux.daemon",
            rustix::process::geteuid().as_raw()
        );
        let _ = Command::new("launchctl").arg("bootout").arg(target).status();
        return Ok(());
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = Command::new("systemctl")
            .args(["--user", "disable", "--now", "soleaux.service"])
            .status();
        return Ok(());
    }
    #[cfg(target_os = "windows")]
    {
        let _ = Command::new("schtasks.exe")
            .args(["/End", "/TN", "Soleaux"])
            .status();
        return Ok(());
    }
    #[cfg(not(any(unix, target_os = "windows")))]
    {
        let _ = paths;
        bail!("Soleaux service stop is unsupported on this operating system")
    }
}

fn run_status(command: &mut Command, action: &str) -> Result<()> {
    let status = command.status().with_context(|| action.to_string())?;
    if !status.success() {
        bail!("{action} failed with {status}");
    }
    Ok(())
}

fn verify_binary(path: &Path, label: &str) -> Result<()> {
    if !path.is_file() {
        bail!("{label} binary does not exist: {}", path.display());
    }
    Ok(())
}

fn copy_executable(source: &Path, destination: &Path) -> Result<()> {
    let parent = destination.parent().context("installed binary has no parent")?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(".{}.tmp", uuid::Uuid::now_v7()));
    fs::copy(source, &temporary).with_context(|| {
        format!(
            "copying installed binary {} to {}",
            source.display(),
            temporary.display()
        )
    })?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(0o755))?;
    }
    fs::rename(&temporary, destination)
        .with_context(|| format!("installing binary {}", destination.display()))?;
    Ok(())
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().context("service manifest has no parent")?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(".{}.tmp", uuid::Uuid::now_v7()));
    fs::write(&temporary, bytes)?;
    fs::rename(&temporary, path)
        .with_context(|| format!("installing service manifest {}", path.display()))?;
    Ok(())
}

fn remove_if_file(path: &Path) -> Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    fs::remove_file(path).with_context(|| format!("removing {}", path.display()))?;
    Ok(true)
}

fn absolute(path: &Path) -> Result<String> {
    path.canonicalize()
        .with_context(|| format!("resolving {}", path.display()))
        .map(|value| value.to_string_lossy().to_string())
}

fn absolute_or_literal(path: &Path) -> Result<String> {
    if path.is_absolute() {
        return Ok(path.to_string_lossy().to_string());
    }
    Ok(std::env::current_dir()?.join(path).to_string_lossy().to_string())
}

fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn systemd_escape(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

fn read_pid(path: &Path) -> Option<u32> {
    fs::read_to_string(path).ok()?.trim().parse().ok()
}

async fn wait_for_endpoint_removal(endpoint: &Path) {
    for _ in 0..50 {
        if !endpoint.exists() {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
    }
}
