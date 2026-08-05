from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} drifted: expected 1 occurrence, observed {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


workspace = Path("native/Cargo.toml")
text = workspace.read_text(encoding="utf-8")
old = '  "daemon/state",\n  "apps/cli",'
new = '  "daemon/state",\n  "daemon/ipc",\n  "apps/cli",'
count = text.count(old)
if count != 2:
    raise SystemExit(f"IPC workspace member targets drifted: expected 2, observed {count}")
workspace.write_text(text.replace(old, new), encoding="utf-8")

server = Path("native/daemon/ipc/src/server.rs")
replace_once(
    server,
    "use anyhow::{Context, Result, anyhow, bail};",
    "use anyhow::{Context, Result, anyhow};",
    "server imports",
)
replace_once(
    server,
    '        bail!("Soleaux local IPC is not yet available on this operating system")',
    '        anyhow::bail!("Soleaux local IPC is not yet available on this operating system")',
    "non-Unix server failure",
)

tests = Path("native/daemon/ipc/src/tests.rs")
replace_once(
    tests,
    '        assert!(manifest.contains("systemd").not());',
    '        assert!(!manifest.contains("systemd"));',
    "service manifest assertion",
)
replace_once(
    tests,
    '''
trait BoolNot {
    fn not(self) -> bool;
}

impl BoolNot for bool {
    fn not(self) -> bool {
        !self
    }
}
''',
    "\n",
    "obsolete BoolNot helper",
)

main = Path("native/apps/cli/src/main.rs")
replace_once(
    main,
    "use anyhow::{Context, Result, bail};\n",
    "mod operations;\n\nuse anyhow::{Context, Result, bail};\n",
    "operations module",
)
replace_once(
    main,
    '''    Attach {
        #[arg(default_value = ".")]
        repo: PathBuf,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        yes: bool,
    },
}
''',
    '''    Attach {
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
    /// Create a signed canonical handoff record.
    Handoff {
        #[command(subcommand)]
        command: HandoffCommand,
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
''',
    "stable CLI command surface",
)

catalog_end = '''enum CatalogCommand {
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
'''
new_enums = catalog_end + '''
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
'''
replace_once(main, catalog_end, new_enums, "operational subcommands")

attach_arm = '''        SoleauxCommand::Attach { repo, dry_run, yes } => {
            let plan = attach_plan(&repo)?;
            if dry_run || !yes {
                print_json(plan)
            } else {
                print_json(apply_attach(&repo)?)
            }
        }
'''
new_arms = attach_arm + '''        SoleauxCommand::Install {
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
                    print_json(json!({"restored":revert_last(&repo)?}))
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
                    print_json(apply_attach(&repo)?)
                }
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
                let git_state: Value = serde_json::from_str(&git_state)
                    .context("--git-state must be valid JSON")?;
                let code_state: Value = serde_json::from_str(&code_state)
                    .context("--code-state must be valid JSON")?;
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
        SoleauxCommand::Backup { destination } => {
            print_json(operations::backup(destination).await?)
        }
        SoleauxCommand::Restore { source } => {
            print_json(operations::restore(source).await?)
        }
        SoleauxCommand::Export { destination } => {
            print_json(operations::export_state(destination).await?)
        }
        SoleauxCommand::Repair => print_json(operations::repair().await?),
        SoleauxCommand::Uninstall {
            preserve_state,
            restore_native,
        } => print_json(
            operations::uninstall_product(preserve_state, restore_native).await?,
        ),
'''
replace_once(main, attach_arm, new_arms, "operational command dispatch")
