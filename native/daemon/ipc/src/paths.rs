use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use std::{env, fs, path::PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SoleauxPaths {
    pub home: PathBuf,
    pub runtime: PathBuf,
    pub endpoint: PathBuf,
    pub state_database: PathBuf,
    pub pid_file: PathBuf,
    pub log_file: PathBuf,
    pub service_manifest: PathBuf,
    pub install_bin: PathBuf,
}

impl SoleauxPaths {
    pub fn resolve() -> Result<Self> {
        let home = resolve_home()?;
        let runtime = resolve_runtime(&home)?;
        let service_manifest = resolve_service_manifest()?;
        let install_bin = resolve_install_bin()?;
        Ok(Self {
            endpoint: runtime.join("soleaux.sock"),
            state_database: home.join("state").join("canonical.sqlite3"),
            pid_file: runtime.join("soleauxd.pid"),
            log_file: home.join("logs").join("soleauxd.log"),
            home,
            runtime,
            service_manifest,
            install_bin,
        })
    }

    pub fn create_directories(&self) -> Result<()> {
        for directory in [
            &self.home,
            &self.runtime,
            self.state_database
                .parent()
                .context("state database has no parent")?,
            self.log_file.parent().context("log file has no parent")?,
            &self.install_bin,
            self.service_manifest
                .parent()
                .context("service manifest has no parent")?,
        ] {
            fs::create_dir_all(directory)
                .with_context(|| format!("creating Soleaux directory {}", directory.display()))?;
            restrict_directory(directory)?;
        }
        Ok(())
    }

    pub fn installed_cli(&self) -> PathBuf {
        self.install_bin.join(if cfg!(windows) {
            "soleaux.exe"
        } else {
            "soleaux"
        })
    }

    pub fn installed_daemon(&self) -> PathBuf {
        self.install_bin.join(if cfg!(windows) {
            "soleauxd.exe"
        } else {
            "soleauxd"
        })
    }
}

fn resolve_home() -> Result<PathBuf> {
    if let Some(value) = env::var_os("SOLEAUX_HOME") {
        let path = PathBuf::from(value);
        if path.as_os_str().is_empty() {
            bail!("SOLEAUX_HOME must not be empty");
        }
        return Ok(path);
    }
    #[cfg(target_os = "windows")]
    {
        let base = env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .context("LOCALAPPDATA is unavailable")?;
        return Ok(base.join("Soleaux"));
    }
    #[cfg(target_os = "macos")]
    {
        Ok(user_home()?
            .join("Library")
            .join("Application Support")
            .join("Soleaux"))
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        if let Some(value) = env::var_os("XDG_STATE_HOME") {
            return Ok(PathBuf::from(value).join("soleaux"));
        }
        Ok(user_home()?.join(".local").join("state").join("soleaux"))
    }
    #[cfg(not(any(unix, target_os = "windows")))]
    {
        bail!("Soleaux per-user home is unsupported on this operating system")
    }
}

fn resolve_runtime(home: &std::path::Path) -> Result<PathBuf> {
    if let Some(value) = env::var_os("SOLEAUX_RUNTIME_DIR") {
        return Ok(PathBuf::from(value));
    }
    #[cfg(target_os = "windows")]
    {
        return Ok(home.join("run"));
    }
    #[cfg(unix)]
    {
        if let Some(value) = env::var_os("XDG_RUNTIME_DIR") {
            return Ok(PathBuf::from(value).join("soleaux"));
        }
        Ok(home.join("run"))
    }
    #[cfg(not(any(unix, target_os = "windows")))]
    {
        Ok(home.join("run"))
    }
}

fn resolve_service_manifest() -> Result<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        Ok(user_home()?
            .join("Library")
            .join("LaunchAgents")
            .join("com.soleaux.daemon.plist"))
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let config = env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .unwrap_or(user_home()?.join(".config"));
        Ok(config.join("systemd").join("user").join("soleaux.service"))
    }
    #[cfg(target_os = "windows")]
    {
        Ok(resolve_home()?.join("service").join("soleaux-task.xml"))
    }
    #[cfg(not(any(unix, target_os = "windows")))]
    {
        bail!("Soleaux service manifests are unsupported on this operating system")
    }
}

fn resolve_install_bin() -> Result<PathBuf> {
    if let Some(value) = env::var_os("SOLEAUX_INSTALL_BIN") {
        return Ok(PathBuf::from(value));
    }
    #[cfg(target_os = "windows")]
    {
        return Ok(resolve_home()?.join("bin"));
    }
    #[cfg(unix)]
    {
        Ok(user_home()?.join(".local").join("bin"))
    }
    #[cfg(not(any(unix, target_os = "windows")))]
    {
        bail!("Soleaux binary installation is unsupported on this operating system")
    }
}

fn user_home() -> Result<PathBuf> {
    env::var_os("HOME")
        .map(PathBuf::from)
        .context("HOME is unavailable")
}

#[cfg(unix)]
fn restrict_directory(path: &std::path::Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .with_context(|| format!("restricting Soleaux directory {}", path.display()))
}

#[cfg(not(unix))]
fn restrict_directory(_path: &std::path::Path) -> Result<()> {
    Ok(())
}
