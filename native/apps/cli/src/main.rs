mod operations;

use anyhow::{Context, Result, bail};
use clap::{Args, Parser, Subcommand};
use serde_json::{Value, json};
use soleaux_ipc::IpcMethod;
use soleaux_mcp::{
    PublicMcpServer,
    gateway::{backend_status, clear_credential, invoke, store_credential},
    nextjs_devtools,
    provisioning::{adopt_plan, apply_adopt, attach_plan},
};
use soleaux_state::{
    ClientAccessMode, ClientKind, REGISTRY_PAGE_LIMIT_DEFAULT, Sensitivity, WorkspaceTrustState,
};
use std::{
    env, fs,
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
    /// Install the Soleaux CLI, daemon, and per-user service manifest.
    Install {
        #[arg(long)]
        cli: Option<PathBuf>,
        #[arg(long)]
        daemon: Option<PathBuf>,
        #[arg(long)]
        no_start: bool,
    },
    /// Install, start, stop, restart, or inspect the per-user daemon.
    Service {
        #[command(subcommand)]
        command: ServiceCommand,
    },
    /// Inspect or clear Soleaux's disposable cache.
    Cache {
        #[command(subcommand)]
        command: CacheCommand,
    },
    /// Stable integration alias for adopt and attach workflows.
    Integrate {
        #[command(subcommand)]
        command: IntegrateCommand,
    },
    /// Inspect and mutate the daemon-owned workspace and client registry.
    Registry {
        #[command(subcommand)]
        command: RegistryCommand,
    },
    /// Create a signed canonical handoff record.
    Handoff {
        #[command(subcommand)]
        command: HandoffCommand,
    },
    /// Capability-gated canonical memory claim lifecycle operations.
    Memory {
        #[command(subcommand)]
        command: MemoryCommand,
    },
    /// Back up daemon-owned canonical state.
    Backup { destination: PathBuf },
    /// Restore daemon-owned canonical state while the service is stopped.
    Restore { source: PathBuf },
    /// Export canonical state as bounded JSON.
    Export { destination: PathBuf },
    /// Verify and repair canonical SQLite indexes and WAL state.
    Repair,
    /// Remove the installed service and binaries without guessing vendor-native state.
    Uninstall {
        #[arg(long, default_value_t = true)]
        preserve_state: bool,
        #[arg(long)]
        restore_native: bool,
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
    /// Capability-probe the registered next-devtools backend and, when
    /// capable, run init → nextjs_index and attach runtime evidence.
    NextRuntime {
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

#[derive(Debug, Subcommand)]
enum ServiceCommand {
    Install {
        #[arg(long)]
        daemon: Option<PathBuf>,
    },
    Start,
    Stop,
    Restart,
    Status,
}

#[derive(Debug, Subcommand)]
enum CacheCommand {
    Status,
    Clear,
}

#[derive(Debug, Subcommand)]
enum IntegrateCommand {
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
    Attach {
        #[arg(default_value = ".")]
        repo: PathBuf,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Debug, Subcommand)]
enum RegistryCommand {
    /// Show one bounded page of the converged workspace, client, and binding registry.
    Status {
        #[arg(long)]
        include_stale: bool,
        #[arg(long, default_value_t = REGISTRY_PAGE_LIMIT_DEFAULT)]
        limit: usize,
        #[arg(long)]
        workspace_cursor: Option<uuid::Uuid>,
        #[arg(long)]
        client_cursor: Option<uuid::Uuid>,
        #[arg(long)]
        binding_cursor: Option<uuid::Uuid>,
    },
    /// Manage canonical workspace registrations.
    Workspace {
        #[command(subcommand)]
        command: WorkspaceRegistryCommand,
    },
    /// Manage connected CLI, desktop, editor, and adapter clients.
    Client {
        #[command(subcommand)]
        command: ClientRegistryCommand,
    },
    /// List one bounded page of client/workspace bindings.
    Bindings {
        #[arg(long)]
        include_stale: bool,
        #[arg(long)]
        cursor: Option<uuid::Uuid>,
        #[arg(long, default_value_t = REGISTRY_PAGE_LIMIT_DEFAULT)]
        limit: usize,
    },
    /// Bind a registered client to a registered workspace.
    Bind {
        client_id: uuid::Uuid,
        workspace_id: uuid::Uuid,
        #[arg(long, default_value = "read_only", value_parser = parse_access_mode)]
        access_mode: ClientAccessMode,
        #[arg(long, default_value = "{}")]
        capabilities: String,
        #[arg(long, default_value = "{}")]
        metadata: String,
    },
    /// Remove a client/workspace binding.
    Unbind {
        binding_id: uuid::Uuid,
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Debug, Subcommand)]
enum WorkspaceRegistryCommand {
    /// Register or refresh one canonical local workspace.
    Register {
        #[arg(default_value = ".")]
        repo: PathBuf,
        #[arg(long)]
        display_name: Option<String>,
        #[arg(long, default_value = "read_only", value_parser = parse_trust_state)]
        trust_state: WorkspaceTrustState,
        #[arg(long, default_value = "{}")]
        metadata: String,
    },
    /// List one bounded page of registered workspaces.
    List {
        #[arg(long)]
        cursor: Option<uuid::Uuid>,
        #[arg(long, default_value_t = REGISTRY_PAGE_LIMIT_DEFAULT)]
        limit: usize,
    },
    /// Forget a workspace and tombstone all client bindings to it.
    Forget {
        workspace_id: uuid::Uuid,
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Debug, Subcommand)]
enum ClientRegistryCommand {
    /// Register or refresh a concurrent client instance.
    Register {
        #[arg(long, value_parser = parse_client_kind)]
        kind: ClientKind,
        #[arg(long)]
        instance_id: String,
        #[arg(long)]
        display_name: String,
        #[arg(long)]
        client_version: String,
        #[arg(long, default_value = "soleaux.client/v1")]
        protocol_version: String,
        #[arg(long, default_value_t = 300_000)]
        ttl_ms: u64,
        #[arg(long, default_value = "{}")]
        capabilities: String,
        #[arg(long, default_value = "{}")]
        metadata: String,
    },
    /// Refresh the lease and capabilities of a registered client.
    Heartbeat {
        client_id: uuid::Uuid,
        #[arg(long, default_value_t = 300_000)]
        ttl_ms: u64,
        #[arg(long)]
        capabilities: Option<String>,
    },
    /// List one bounded page of clients, optionally including stale registrations.
    List {
        #[arg(long)]
        include_stale: bool,
        #[arg(long)]
        cursor: Option<uuid::Uuid>,
        #[arg(long, default_value_t = REGISTRY_PAGE_LIMIT_DEFAULT)]
        limit: usize,
    },
    /// Disconnect a client and tombstone all workspace bindings.
    Disconnect {
        client_id: uuid::Uuid,
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Debug, Subcommand)]
enum MemoryCommand {
    /// Propose a memory claim; it enters the lifecycle as `proposed`.
    Propose {
        #[arg(long)]
        workspace_id: uuid::Uuid,
        #[arg(long)]
        actor: String,
        #[arg(long)]
        scope: String,
        #[arg(long)]
        claim_type: String,
        #[arg(long)]
        subject: String,
        #[arg(long)]
        content: String,
        #[arg(long, default_value_t = 0.5)]
        confidence: f64,
        #[arg(long = "evidence-uri")]
        evidence_uris: Vec<String>,
        #[arg(long)]
        supersedes_id: Option<uuid::Uuid>,
        #[arg(long)]
        source_session_id: Option<uuid::Uuid>,
        #[arg(long, default_value = "internal", value_parser = parse_sensitivity)]
        sensitivity: Sensitivity,
        #[arg(long)]
        expires_at_unix_ms: Option<i64>,
        #[arg(long, default_value = "{}")]
        metadata: String,
    },
    /// List one bounded page of memory claims.
    List {
        #[arg(long)]
        workspace_id: Option<uuid::Uuid>,
        #[arg(long)]
        scope: Option<String>,
        #[arg(long = "state")]
        memory_state: Option<String>,
        #[arg(long)]
        cursor: Option<uuid::Uuid>,
        #[arg(long, default_value_t = REGISTRY_PAGE_LIMIT_DEFAULT)]
        limit: usize,
    },
    /// Advance a claim to validated or active, or reject it.
    Validate {
        claim_id: uuid::Uuid,
        #[arg(long)]
        actor: String,
        #[arg(long)]
        disposition: String,
    },
    /// Correct a non-terminal claim's content, confidence, or evidence.
    Correct {
        claim_id: uuid::Uuid,
        #[arg(long)]
        actor: String,
        #[arg(long)]
        content: Option<String>,
        #[arg(long)]
        confidence: Option<f64>,
        #[arg(long = "evidence-uri")]
        evidence_uris: Vec<String>,
        #[arg(long)]
        metadata: Option<String>,
    },
    /// Mark an active claim superseded by a validated or active replacement.
    Supersede {
        claim_id: uuid::Uuid,
        #[arg(long)]
        actor: String,
        #[arg(long)]
        replacement_id: uuid::Uuid,
    },
    /// Tombstone an active claim; retention purges it later.
    Tombstone {
        claim_id: uuid::Uuid,
        #[arg(long)]
        actor: String,
        #[arg(long)]
        reason: String,
        #[arg(long)]
        yes: bool,
    },
    /// Export one bounded page of claims as a portable document.
    Export {
        #[arg(long)]
        workspace_id: uuid::Uuid,
        #[arg(long)]
        scope: Option<String>,
        #[arg(long)]
        cursor: Option<uuid::Uuid>,
        #[arg(long, default_value_t = REGISTRY_PAGE_LIMIT_DEFAULT)]
        limit: usize,
    },
    /// Import a previously exported claim document into a workspace.
    Import {
        #[arg(long)]
        workspace_id: uuid::Uuid,
        #[arg(long)]
        actor: String,
        /// Path to a soleaux.memory-export/v1 JSON document.
        document: PathBuf,
    },
}

#[derive(Debug, Subcommand)]
enum HandoffCommand {
    Create {
        #[arg(long)]
        source_session_id: uuid::Uuid,
        #[arg(long)]
        destination_platform: String,
        #[arg(long)]
        destination_session_id: Option<uuid::Uuid>,
        #[arg(long)]
        payload_hash: String,
        #[arg(long)]
        signature: String,
        #[arg(long)]
        workspace_id: Option<uuid::Uuid>,
        #[arg(long, default_value = "{}")]
        git_state: String,
        #[arg(long, default_value = "{}")]
        code_state: String,
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

fn canonical_path_to_utf8(path: &Path) -> Result<String> {
    path.to_str()
        .context("workspace path is not valid UTF-8")
        .map(ToOwned::to_owned)
}

fn canonical_utf8_path(path: &Path) -> Result<String> {
    let canonical = fs::canonicalize(path)
        .with_context(|| format!("resolving workspace path {}", path.display()))?;
    canonical_path_to_utf8(&canonical)
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

fn parse_client_kind(value: &str) -> std::result::Result<ClientKind, String> {
    ClientKind::parse(value).map_err(|error| error.to_string())
}

fn parse_access_mode(value: &str) -> std::result::Result<ClientAccessMode, String> {
    ClientAccessMode::parse(value).map_err(|error| error.to_string())
}

fn parse_trust_state(value: &str) -> std::result::Result<WorkspaceTrustState, String> {
    WorkspaceTrustState::parse(value).map_err(|error| error.to_string())
}

fn parse_sensitivity(value: &str) -> std::result::Result<Sensitivity, String> {
    Sensitivity::parse(value).map_err(|error| error.to_string())
}

fn parse_json_argument(value: &str, label: &str) -> Result<Value> {
    serde_json::from_str(value).with_context(|| format!("{label} must be valid JSON"))
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
            McpCommand::NextRuntime { repo } => {
                let (index, devtools) = nextjs_devtools::runtime_report(&repo).await?;
                print_json(json!({"index":index,"devtools":devtools}))
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
                return print_json(operations::revert_adoption(&repo).await?);
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
                print_json(operations::apply_and_register_attach(&repo).await?)
            }
        }
        SoleauxCommand::Install {
            cli,
            daemon,
            no_start,
        } => print_json(operations::install_product(cli, daemon, !no_start).await?),
        SoleauxCommand::Service { command } => match command {
            ServiceCommand::Install { daemon } => {
                print_json(operations::service_install(daemon).await?)
            }
            ServiceCommand::Start => print_json(operations::service_start().await?),
            ServiceCommand::Stop => print_json(operations::service_stop().await?),
            ServiceCommand::Restart => print_json(operations::service_restart().await?),
            ServiceCommand::Status => print_json(operations::service_status_value().await?),
        },
        SoleauxCommand::Cache { command } => match command {
            CacheCommand::Status => print_json(operations::cache_status()?),
            CacheCommand::Clear => print_json(operations::cache_clear()?),
        },
        SoleauxCommand::Integrate { command } => match command {
            IntegrateCommand::Adopt {
                repo,
                dry_run,
                yes,
                revert,
            } => {
                if revert {
                    print_json(operations::revert_adoption(&repo).await?)
                } else {
                    let plan = adopt_plan(&repo)?;
                    if dry_run || !yes {
                        print_json(plan)
                    } else {
                        print_json(apply_adopt(&repo)?)
                    }
                }
            }
            IntegrateCommand::Attach { repo, dry_run, yes } => {
                let plan = attach_plan(&repo)?;
                if dry_run || !yes {
                    print_json(plan)
                } else {
                    print_json(operations::apply_and_register_attach(&repo).await?)
                }
            }
        },
        SoleauxCommand::Registry { command } => match command {
            RegistryCommand::Status {
                include_stale,
                limit,
                workspace_cursor,
                client_cursor,
                binding_cursor,
            } => print_json(
                operations::registry_call(IpcMethod::RegistryStatus {
                    include_stale,
                    limit,
                    workspace_cursor,
                    client_cursor,
                    binding_cursor,
                })
                .await?,
            ),
            RegistryCommand::Workspace { command } => match command {
                WorkspaceRegistryCommand::Register {
                    repo,
                    display_name,
                    trust_state,
                    metadata,
                } => print_json(
                    operations::registry_call(IpcMethod::WorkspaceRegister {
                        path: canonical_utf8_path(&repo)?,
                        display_name,
                        trust_state,
                        metadata: parse_json_argument(&metadata, "--metadata")?,
                    })
                    .await?,
                ),
                WorkspaceRegistryCommand::List { cursor, limit } => print_json(
                    operations::registry_call(IpcMethod::WorkspaceList { cursor, limit }).await?,
                ),
                WorkspaceRegistryCommand::Forget { workspace_id, yes } => {
                    if !yes {
                        bail!("workspace forget requires --yes");
                    }
                    print_json(
                        operations::registry_call(IpcMethod::WorkspaceForget { workspace_id })
                            .await?,
                    )
                }
            },
            RegistryCommand::Client { command } => match command {
                ClientRegistryCommand::Register {
                    kind,
                    instance_id,
                    display_name,
                    client_version,
                    protocol_version,
                    ttl_ms,
                    capabilities,
                    metadata,
                } => print_json(
                    operations::registry_call(IpcMethod::ClientRegister {
                        client_kind: kind,
                        instance_id,
                        display_name,
                        client_version,
                        protocol_version,
                        ttl_ms,
                        capabilities: parse_json_argument(&capabilities, "--capabilities")?,
                        metadata: parse_json_argument(&metadata, "--metadata")?,
                    })
                    .await?,
                ),
                ClientRegistryCommand::Heartbeat {
                    client_id,
                    ttl_ms,
                    capabilities,
                } => print_json(
                    operations::registry_call(IpcMethod::ClientHeartbeat {
                        client_id,
                        ttl_ms,
                        capabilities: capabilities
                            .as_deref()
                            .map(|value| parse_json_argument(value, "--capabilities"))
                            .transpose()?,
                    })
                    .await?,
                ),
                ClientRegistryCommand::List {
                    include_stale,
                    cursor,
                    limit,
                } => print_json(
                    operations::registry_call(IpcMethod::ClientList {
                        include_stale,
                        cursor,
                        limit,
                    })
                    .await?,
                ),
                ClientRegistryCommand::Disconnect { client_id, yes } => {
                    if !yes {
                        bail!("client disconnect requires --yes");
                    }
                    print_json(
                        operations::registry_call(IpcMethod::ClientDisconnect { client_id })
                            .await?,
                    )
                }
            },
            RegistryCommand::Bindings {
                include_stale,
                cursor,
                limit,
            } => print_json(
                operations::registry_call(IpcMethod::ClientBindingList {
                    include_stale,
                    cursor,
                    limit,
                })
                .await?,
            ),
            RegistryCommand::Bind {
                client_id,
                workspace_id,
                access_mode,
                capabilities,
                metadata,
            } => print_json(
                operations::registry_call(IpcMethod::ClientBindWorkspace {
                    client_id,
                    workspace_id,
                    access_mode,
                    // Receipt-admitted external binds arrive through adapters
                    // (P5-014+); the CLI itself binds on the internal path.
                    admission_receipt: None,
                    capabilities: parse_json_argument(&capabilities, "--capabilities")?,
                    metadata: parse_json_argument(&metadata, "--metadata")?,
                })
                .await?,
            ),
            RegistryCommand::Unbind { binding_id, yes } => {
                if !yes {
                    bail!("registry unbind requires --yes");
                }
                print_json(
                    operations::registry_call(IpcMethod::ClientUnbindWorkspace { binding_id })
                        .await?,
                )
            }
        },
        SoleauxCommand::Handoff { command } => match command {
            HandoffCommand::Create {
                source_session_id,
                destination_platform,
                destination_session_id,
                payload_hash,
                signature,
                workspace_id,
                git_state,
                code_state,
            } => {
                let git_state: Value =
                    serde_json::from_str(&git_state).context("--git-state must be valid JSON")?;
                let code_state: Value =
                    serde_json::from_str(&code_state).context("--code-state must be valid JSON")?;
                print_json(operations::create_handoff(
                    source_session_id,
                    destination_platform,
                    destination_session_id,
                    payload_hash,
                    signature,
                    workspace_id,
                    git_state,
                    code_state,
                )?)
            }
        },
        SoleauxCommand::Memory { command } => match command {
            MemoryCommand::Propose {
                workspace_id,
                actor,
                scope,
                claim_type,
                subject,
                content,
                confidence,
                evidence_uris,
                supersedes_id,
                source_session_id,
                sensitivity,
                expires_at_unix_ms,
                metadata,
            } => print_json(
                operations::daemon_call(IpcMethod::MemoryPropose {
                    workspace_id,
                    actor,
                    scope,
                    claim_type,
                    subject,
                    content,
                    confidence,
                    evidence_uris,
                    supersedes_id,
                    source_session_id,
                    sensitivity,
                    expires_at_unix_ms,
                    metadata: parse_json_argument(&metadata, "--metadata")?,
                })
                .await?,
            ),
            MemoryCommand::List {
                workspace_id,
                scope,
                memory_state,
                cursor,
                limit,
            } => print_json(
                operations::daemon_call(IpcMethod::MemoryList {
                    workspace_id,
                    scope,
                    memory_state,
                    cursor,
                    limit,
                })
                .await?,
            ),
            MemoryCommand::Validate {
                claim_id,
                actor,
                disposition,
            } => print_json(
                operations::daemon_call(IpcMethod::MemoryValidate {
                    claim_id,
                    actor,
                    disposition,
                })
                .await?,
            ),
            MemoryCommand::Correct {
                claim_id,
                actor,
                content,
                confidence,
                evidence_uris,
                metadata,
            } => print_json(
                operations::daemon_call(IpcMethod::MemoryCorrect {
                    claim_id,
                    actor,
                    content,
                    confidence,
                    evidence_uris: if evidence_uris.is_empty() {
                        None
                    } else {
                        Some(evidence_uris)
                    },
                    metadata: metadata
                        .as_deref()
                        .map(|value| parse_json_argument(value, "--metadata"))
                        .transpose()?,
                })
                .await?,
            ),
            MemoryCommand::Supersede {
                claim_id,
                actor,
                replacement_id,
            } => print_json(
                operations::daemon_call(IpcMethod::MemorySupersede {
                    claim_id,
                    actor,
                    replacement_id,
                })
                .await?,
            ),
            MemoryCommand::Tombstone {
                claim_id,
                actor,
                reason,
                yes,
            } => {
                if !yes {
                    bail!("memory tombstone requires --yes");
                }
                print_json(
                    operations::daemon_call(IpcMethod::MemoryTombstone {
                        claim_id,
                        actor,
                        reason,
                    })
                    .await?,
                )
            }
            MemoryCommand::Export {
                workspace_id,
                scope,
                cursor,
                limit,
            } => print_json(
                operations::daemon_call(IpcMethod::MemoryExport {
                    workspace_id,
                    scope,
                    cursor,
                    limit,
                })
                .await?,
            ),
            MemoryCommand::Import {
                workspace_id,
                actor,
                document,
            } => {
                let raw = fs::read_to_string(&document).with_context(|| {
                    format!("reading memory import document {}", document.display())
                })?;
                let document: Value =
                    serde_json::from_str(&raw).context("memory import document must be JSON")?;
                print_json(
                    operations::daemon_call(IpcMethod::MemoryImport {
                        workspace_id,
                        actor,
                        document,
                    })
                    .await?,
                )
            }
        },
        SoleauxCommand::Backup { destination } => {
            print_json(operations::backup(destination).await?)
        }
        SoleauxCommand::Restore { source } => print_json(operations::restore(source).await?),
        SoleauxCommand::Export { destination } => {
            print_json(operations::export_state(destination).await?)
        }
        SoleauxCommand::Repair => print_json(operations::repair().await?),
        SoleauxCommand::Uninstall {
            preserve_state,
            restore_native,
        } => print_json(operations::uninstall_product(preserve_state, restore_native).await?),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn canonical_workspace_path_encoding_rejects_non_utf8_names() {
        use std::{ffi::OsString, os::unix::ffi::OsStringExt};

        let invalid = PathBuf::from(OsString::from_vec(vec![b'w', 0xff]));
        let error = canonical_path_to_utf8(&invalid).expect_err("non-UTF8 path must fail closed");
        assert!(error.to_string().contains("valid UTF-8"));
    }
}
