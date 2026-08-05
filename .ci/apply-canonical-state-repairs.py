from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} drifted: expected 1 occurrence, observed {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


store = Path("native/daemon/state/src/store.rs")
replace_once(
    store,
    "#[derive(Clone)]\npub struct StateStore {",
    "#[derive(Clone, Debug)]\npub struct StateStore {",
    "StateStore Debug derive",
)
replace_once(
    store,
    '''        let (sender, receiver) = mpsc::channel();
        thread::Builder::new()
            .name("soleaux-canonical-state-writer".to_string())
            .spawn(move || writer_loop(connection, receiver))
            .context("starting canonical state writer")?;
''',
    '''        let (sender, receiver) = mpsc::channel();
        let writer_path = path.clone();
        thread::Builder::new()
            .name("soleaux-canonical-state-writer".to_string())
            .spawn(move || writer_loop(connection, receiver, writer_path))
            .context("starting canonical state writer")?;
''',
    "writer path capture",
)
replace_once(
    store,
    '''fn writer_loop(
    mut connection: rusqlite::Connection,
    receiver: mpsc::Receiver<WriteCommand>,
) {
''',
    '''fn writer_loop(
    mut connection: rusqlite::Connection,
    receiver: mpsc::Receiver<WriteCommand>,
    path: PathBuf,
) {
''',
    "writer loop path",
)
replace_once(
    store,
    'respond(reply, database::repair(&mut connection, &PathBuf::from("")));',
    'respond(reply, database::repair(&mut connection, &path));',
    "repair database path",
)

database = Path("native/daemon/state/src/database.rs")
replace_once(
    database,
    '''        if let Some(existing) = existing {
            if entity_replay_matches(&existing, input) {
                transaction.commit()?;
                return Ok(existing);
            }
            bail!("canonical idempotency collision: immutable request differs");
        }
''',
    '''        if let Some(existing) = existing {
            if entity_replay_matches(&existing, input) {
                transaction.commit()?;
                return Ok(existing);
            }
            if input.id != Some(existing.id) {
                bail!("canonical idempotency collision: immutable request differs");
            }
        }
''',
    "idempotent update routing",
)
replace_once(
    database,
    '''workspace_id
            .as_ref()
            .map(Uuid::as_bytes)
            .unwrap_or(&[]),''',
    '''workspace_id
            .as_ref()
            .map(|value| value.as_bytes().as_slice())
            .unwrap_or(&[]),''',
    "workspace UUID audit bytes",
)
replace_once(
    database,
    'entity_id.as_ref().map(Uuid::as_bytes).unwrap_or(&[]),',
    'entity_id\n            .as_ref()\n            .map(|value| value.as_bytes().as_slice())\n            .unwrap_or(&[]),',
    "entity UUID audit bytes",
)
replace_once(
    database,
    '''fn parse_kind(index: usize, value: &str) -> rusqlite::Result<EntityKind> {
    EntityKind::parse(value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(index, Type::Text, Box::new(error))
    })
}

fn parse_sensitivity(index: usize, value: &str) -> rusqlite::Result<Sensitivity> {
    Sensitivity::parse(value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(index, Type::Text, Box::new(error))
    })
}

fn parse_relationship(index: usize, value: &str) -> rusqlite::Result<RelationshipKind> {
    RelationshipKind::parse(value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(index, Type::Text, Box::new(error))
    })
}
''',
    '''fn invalid_text_value(index: usize, error: anyhow::Error) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(
        index,
        Type::Text,
        Box::new(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            error.to_string(),
        )),
    )
}

fn parse_kind(index: usize, value: &str) -> rusqlite::Result<EntityKind> {
    EntityKind::parse(value).map_err(|error| invalid_text_value(index, error))
}

fn parse_sensitivity(index: usize, value: &str) -> rusqlite::Result<Sensitivity> {
    Sensitivity::parse(value).map_err(|error| invalid_text_value(index, error))
}

fn parse_relationship(index: usize, value: &str) -> rusqlite::Result<RelationshipKind> {
    RelationshipKind::parse(value).map_err(|error| invalid_text_value(index, error))
}
''',
    "canonical SQLite text conversion errors",
)

workspace = Path("native/Cargo.toml")
workspace_text = workspace.read_text(encoding="utf-8")
old = '  "daemon/storage",\n  "apps/cli",'
new = '  "daemon/storage",\n  "daemon/state",\n  "apps/cli",'
count = workspace_text.count(old)
if count != 2:
    raise SystemExit(f"workspace state member targets drifted: expected 2, observed {count}")
workspace.write_text(workspace_text.replace(old, new), encoding="utf-8")
