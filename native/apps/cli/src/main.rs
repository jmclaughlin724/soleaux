use anyhow::{Context, Result, bail};
use clap::{Args, Parser, Subcommand};
use serde_json::{Value, json};
use soleaux_mcp::{
    PublicMcpServer,
    gateway::{backend_status, clear_credential, invoke, store_credential},
    provisioning::{adopt_plan, apply_adopt, apply_attach, attach_plan, revert_last},
};
use std::{
    env,
    io::{self, Read},
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

#[derive(Debug, Parser)]
#[command(
    name = "soleaux",
    version,
    about = "Soleaux unified repository intelligence"
)]
struct Application {
    #[command(subcommand)]
    command: SoleauxCommand,
}

#[derive(Debug, Subcommand)]
enum SoleauxCommand {
    /// Turn a repository into one bounded twelve-slot MCP server.
    Serve {
        #[arg(default_value = ".")]
        repo: PathBuf,
        /// Explicit one-for-one substitution, such as restart_lsp=turborepo.packages.
        #[arg(long = "substitute")]
        substitutions: Vec<String>,
    },
    /// Diagnose the native daemon and binding MCP profile.
    Doctor(WorkspaceArguments),
    /// Refresh the persistent structural index.
    Index(WorkspaceArguments),
    /// Non-interactive native verification for repository intelligence.
    Ci(WorkspaceArguments),
    /// Manage namespaced MCP gateway backends without inflating tools/list.
    Mcp {
        #[command(subcommand)]
        command: McpCommand,
    },
    /// Inspect the native skills, agents, rules, ownership, and backend registry.
    Catalog {
        #[command(subcommand)]
        command: CatalogCommand,
    },
    /// Preview, apply, or revert native workspace adoption.
    Adopt {
        #[arg(default_value = ".")]
        repo: PathBuf,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        yes: bool,
        #[arg(long)]
        revert: bool,
    },
    /// Preview or attach a workspace to the per-user Soleaux registry.
    Attach {
        #[arg(default_value = ".")]
        repo: PathBuf,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Debug, Clone, Args)]
struct WorkspaceArguments {
    #[arg(default_value = ".")]
    repo: PathBuf,
    #[arg(long)]
    json: bool,
    #[arg(long = "substitute")]
    substitutions: Vec<String>,
}

#[derive(Debug, Subcommand)]
enum McpCommand {
    /// Show configured backend transport, namespace, availability, and auth state.
    Status {
        #[arg(default_value = ".")]
        repo: PathBuf,
    },
    /// Store a foreground CLI-provided OAuth/bearer credential outside the worktree.
    Login {
        name: String,
        #[arg(long, conflicts_with = "token_stdin")]
        token_env: Option<String>,
        #[arg(long, default_value_t = false)]
        token_stdin: bool,
    },
    /// Remove one backend's stored credential.
    Logout { name: String },
    /// Invoke a namespaced command-backed MCP tool through the native gateway.
    Call {
        name: String,
        tool: String,
        #[arg(long, default_value = "{}")]
        arguments: String,
        #[arg(default_value = ".")]
        repo: PathBuf,
    },
}

#[derive(Debug, Subcommand)]
enum CatalogCommand {
    /// List registry domains and entries.
    List {
        #[arg(default_value = ".")]
        repo: PathBuf,
        #[arg(long)]
        domain: Option<String>,
        #[arg(long, default_value_t = 100)]
        limit: usize,
    },
    /// Read registry identifiers or native tables.
    Read {
        #[arg(default_value = ".")]
        repo: PathBuf,
        #[arg(long)]
        domain: Option<String>,
        #[arg(long = "id")]
        ids: Vec<String>,
        #[arg(long = "table")]
        tables: Vec<String>,
        #[arg(long, default_value_t = 100)]
        limit: usize,
    },
}

fn daemon_executable() -> Result<PathBuf> {
    if let Some(value) = env::var_os("SOLEAUXD") {
        return Ok(PathBuf::from(value));
    }
    let current = env::current_exe().context("resolving soleaux executable")?;
    let sibling = current.with_file_name(if cfg!(windows) {
        "soleauxd.exe"
    } else {
        "soleauxd"
    });
    if sibling.is_file() {
        return Ok(sibling);
    }
    Ok(PathBuf::from("soleauxd"))
}

fn append_substitutions(command: &mut Command, substitutions: &[String]) -> Result<()> {
    for value in substitutions {
        let (replace, with) = value
            .split_once('=')
            .with_context(|| format!("invalid substitution {value}; expected replace=with"))?;
        if replace.is_empty() || with.is_empty() {
            bail!("invalid substitution {value}; replace and with must be non-empty");
        }
        command.arg("--substitute").arg(value);
    }
    Ok(())
}

fn run(mut command: Command) -> Result<()> {
    command
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    let status = command.status().context("launching soleauxd")?;
    if !status.success() {
        bail!("soleauxd exited with {status}");
    }
    Ok(())
}

fn daemon_workspace_command(
    daemon: &Path,
    operation: &str,
    arguments: &WorkspaceArguments,
) -> Result<()> {
    let mut command = Command::new(daemon);
    command.arg(operation).arg(&arguments.repo);
    append_substitutions(&mut command, &arguments.substitutions)?;
    if operation == "doctor" && !arguments.json {
        command.env("SOLEAUX_DOCTOR_HUMAN", "1");
    }
    run(command)
}

fn print_json(value: impl serde::Serialize) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}

fn read_login_token(token_env: Option<String>, token_stdin: bool) -> Result<String> {
    if let Some(name) = token_env {
        return env::var(&name)
            .with_context(|| format!("reading credential environment variable {name}"));
    }
    if !token_stdin {
        bail!("login is foreground-only; provide --token-stdin or --token-env NAME");
    }
    let mut token = String::new();
    io::stdin().read_to_string(&mut token)?;
    Ok(token)
}

async fn catalog_server(repo: &Path) -> Result<PublicMcpServer> {
    let server = PublicMcpServer::new(repo)?;
    server.prepare().await?;
    Ok(server)
}

#[tokio::main]
async fn main() -> Result<()> {
    let application = Application::parse();
    let daemon = daemon_executable()?;
    match application.command {
        SoleauxCommand::Serve {
            repo,
            substitutions,
        } => {
            let mut command = Command::new(daemon);
            command.arg("serve").arg(repo);
            append_substitutions(&mut command, &substitutions)?;
            run(command)
        }
        SoleauxCommand::Doctor(arguments) => {
            daemon_workspace_command(&daemon, "doctor", &arguments)
        }
        SoleauxCommand::Index(arguments) => daemon_workspace_command(&daemon, "index", &arguments),
        SoleauxCommand::Ci(arguments) => {
            daemon_workspace_command(&daemon, "doctor", &arguments)?;
            daemon_workspace_command(&daemon, "index", &arguments)
        }
        SoleauxCommand::Mcp { command } => match command {
            McpCommand::Status { repo } => print_json(backend_status(&repo)?),
            McpCommand::Login {
                name,
                token_env,
                token_stdin,
            } => {
                let token = read_login_token(token_env, token_stdin)?;
                let path = store_credential(&name, &token)?;
                print_json(json!({
                    "status":"stored",
                    "backend":name,
                    "credential_store":path,
                    "worktree_write":false,
                    "production_runtime":"rust",
                }))
            }
            McpCommand::Logout { name } => print_json(json!({
                "status":if clear_credential(&name)? {"removed"} else {"absent"},
                "backend":name,
            })),
            McpCommand::Call {
                name,
                tool,
                arguments,
                repo,
            } => {
                let arguments: Value = serde_json::from_str(&arguments)
                    .context("--arguments must be a JSON object")?;
                if !arguments.is_object() {
                    bail!("--arguments must be a JSON object");
                }
                print_json(invoke(&repo, &name, &tool, arguments).await?)
            }
        },
        SoleauxCommand::Catalog { command } => match command {
            CatalogCommand::List {
                repo,
                domain,
                limit,
            } => {
                let server = catalog_server(&repo).await?;
                let envelope = server
                    .call_async(
                        "registry.list",
                        &json!({"domain":domain,"limit":limit,"cursor":Value::Null}),
                    )
                    .await?;
                print_json(envelope)
            }
            CatalogCommand::Read {
                repo,
                domain,
                ids,
                tables,
                limit,
            } => {
                if tables.is_empty() && (domain.is_none() || ids.is_empty()) {
                    bail!("catalog read requires --table or --domain with at least one --id");
                }
                let server = catalog_server(&repo).await?;
                let envelope = server
                    .call_async(
                        "registry.read",
                        &json!({"domain":domain,"ids":ids,"tables":tables,"limit":limit}),
                    )
                    .await?;
                print_json(envelope)
            }
        },
        SoleauxCommand::Adopt {
            repo,
            dry_run,
            yes,
            revert,
        } => {
            if revert {
                return print_json(json!({"restored":revert_last(&repo)?}));
            }
            let plan = adopt_plan(&repo)?;
            if dry_run || !yes {
                print_json(plan)
            } else {
                print_json(apply_adopt(&repo)?)
            }
        }
        SoleauxCommand::Attach { repo, dry_run, yes } => {
            let plan = attach_plan(&repo)?;
            if dry_run || !yes {
                print_json(plan)
            } else {
                print_json(apply_attach(&repo)?)
            }
        }
    }
}
