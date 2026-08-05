use anyhow::{Context, Result};
use clap::{Args, Parser, Subcommand};
use soleaux_engine::{IntegrationSelection, doctor, index, serve_stdio, serve_streamable_http};
use soleaux_ipc::{IpcServer, SoleauxPaths};
use soleaux_mcp::ToolSubstitution;
use std::{net::SocketAddr, path::PathBuf};

#[derive(Debug, Parser)]
#[command(
    name = "soleauxd",
    version,
    about = "Native Soleaux unified repository-intelligence daemon"
)]
struct Application {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Serve the binding twelve-slot public MCP profile over stdio.
    Serve(ServeArguments),
    /// Serve authenticated Streamable HTTP on loopback.
    Http(HttpArguments),
    /// Serve daemon-owned canonical state over same-user local IPC.
    Ipc(IpcArguments),
    /// Build or refresh the persistent structural index.
    Index(WorkspaceArguments),
    /// Report native profile, provider, and storage health.
    Doctor(WorkspaceArguments),
}

#[derive(Debug, Args)]
struct WorkspaceArguments {
    #[arg(default_value = ".")]
    repo: PathBuf,
    #[command(flatten)]
    profile: ProfileArguments,
}

#[derive(Debug, Args)]
struct ServeArguments {
    #[arg(default_value = ".")]
    repo: PathBuf,
    #[command(flatten)]
    profile: ProfileArguments,
}

#[derive(Debug, Args)]
struct HttpArguments {
    #[arg(default_value = ".")]
    repo: PathBuf,
    #[arg(long, default_value = "127.0.0.1:37432")]
    address: SocketAddr,
    #[arg(long, env = "SOLEAUX_MCP_HTTP_TOKEN", hide_env_values = true)]
    token: String,
    #[command(flatten)]
    profile: ProfileArguments,
}

#[derive(Debug, Args)]
struct IpcArguments {
    /// Override the canonical per-user IPC endpoint.
    #[arg(long)]
    endpoint: Option<PathBuf>,
    /// Override the canonical daemon-owned state database.
    #[arg(long)]
    state_db: Option<PathBuf>,
}

#[derive(Debug, Clone, Default, Args)]
struct ProfileArguments {
    /// Explicit one-for-one public tool substitution, for example restart_lsp=turborepo.packages.
    #[arg(long = "substitute", value_parser = parse_substitution)]
    substitutions: Vec<ToolSubstitution>,
}

impl From<ProfileArguments> for IntegrationSelection {
    fn from(value: ProfileArguments) -> Self {
        Self {
            substitutions: value.substitutions,
        }
    }
}

fn parse_substitution(value: &str) -> std::result::Result<ToolSubstitution, String> {
    let (replace, with) = value
        .split_once('=')
        .ok_or_else(|| "substitution must use replace=with framing".to_string())?;
    if replace.is_empty() || with.is_empty() {
        return Err("substitution replace and with values must be non-empty".to_string());
    }
    Ok(ToolSubstitution {
        replace: replace.to_string(),
        with: with.to_string(),
    })
}

fn init_tracing() {
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn"));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .without_time()
        .init();
}

#[tokio::main]
async fn main() -> Result<()> {
    init_tracing();
    let application = Application::parse();
    match application.command {
        Command::Serve(arguments) => serve_stdio(arguments.repo, arguments.profile.into()).await,
        Command::Http(arguments) => {
            serve_streamable_http(
                arguments.repo,
                arguments.profile.into(),
                arguments.address,
                arguments.token,
            )
            .await
        }
        Command::Ipc(arguments) => {
            let mut paths = SoleauxPaths::resolve()?;
            if let Some(endpoint) = arguments.endpoint {
                paths.runtime = endpoint
                    .parent()
                    .context("IPC endpoint has no parent directory")?
                    .to_path_buf();
                paths.endpoint = endpoint;
                paths.pid_file = paths.runtime.join("soleauxd.pid");
            }
            if let Some(state_db) = arguments.state_db {
                paths.state_database = state_db;
            }
            IpcServer::open(paths)?.run().await
        }
        Command::Index(arguments) => {
            let value = index(arguments.repo, arguments.profile.into()).await?;
            println!("{}", serde_json::to_string_pretty(&value)?);
            Ok(())
        }
        Command::Doctor(arguments) => {
            let value = doctor(arguments.repo, arguments.profile.into())
                .await
                .context("running Soleaux native doctor")?;
            println!("{}", serde_json::to_string_pretty(&value)?);
            Ok(())
        }
    }
}
