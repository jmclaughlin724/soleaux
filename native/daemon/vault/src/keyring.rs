use anyhow::{Context, Result, anyhow, bail};
use ring::rand::{SecureRandom, SystemRandom};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::{Arc, Mutex},
};
#[cfg(target_os = "linux")]
use std::{io::Write, process::Stdio};
use uuid::Uuid;

pub type MasterKey = [u8; 32];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KeyRing {
    current_version: u32,
    keys: BTreeMap<u32, MasterKey>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoredKeyRing {
    schema_version: String,
    current_version: u32,
    keys: BTreeMap<String, String>,
}

impl KeyRing {
    pub fn generate() -> Result<Self> {
        let mut keys = BTreeMap::new();
        keys.insert(1, random_key()?);
        Ok(Self {
            current_version: 1,
            keys,
        })
    }

    pub fn current_version(&self) -> u32 {
        self.current_version
    }

    pub fn versions(&self) -> impl Iterator<Item = u32> + '_ {
        self.keys.keys().copied()
    }

    pub fn rotate(&mut self) -> Result<u32> {
        let version = self
            .current_version
            .checked_add(1)
            .context("vault key version overflow")?;
        self.keys.insert(version, random_key()?);
        self.current_version = version;
        Ok(version)
    }

    pub fn derive_workspace_key(&self, workspace_id: Uuid, version: u32) -> Result<MasterKey> {
        let master = self
            .keys
            .get(&version)
            .with_context(|| format!("vault key version {version} is unavailable"))?;
        let mut context = Vec::with_capacity(64);
        context.extend_from_slice(b"soleaux.artifact-vault.workspace-key/v1\0");
        context.extend_from_slice(workspace_id.as_bytes());
        context.extend_from_slice(&version.to_le_bytes());
        Ok(*blake3::keyed_hash(master, &context).as_bytes())
    }

    pub fn remove_versions_before(&mut self, minimum_version: u32) -> Result<()> {
        if minimum_version > self.current_version {
            bail!("cannot remove the current vault key version");
        }
        self.keys.retain(|version, _| *version >= minimum_version);
        if !self.keys.contains_key(&self.current_version) {
            bail!("vault key pruning removed the current key");
        }
        Ok(())
    }

    fn encode(&self) -> Result<String> {
        let stored = StoredKeyRing {
            schema_version: "soleaux.vault-keyring/v1".to_string(),
            current_version: self.current_version,
            keys: self
                .keys
                .iter()
                .map(|(version, key)| (version.to_string(), hex_encode(key)))
                .collect(),
        };
        Ok(serde_json::to_string(&stored)?)
    }

    fn decode(encoded: &str) -> Result<Self> {
        let stored: StoredKeyRing =
            serde_json::from_str(encoded).context("decoding Soleaux vault key ring")?;
        if stored.schema_version != "soleaux.vault-keyring/v1" {
            bail!("unsupported Soleaux vault key-ring schema");
        }
        let mut keys = BTreeMap::new();
        for (version, encoded_key) in stored.keys {
            let version = version
                .parse::<u32>()
                .context("decoding vault key version")?;
            let bytes = hex_decode(&encoded_key)?;
            let key: MasterKey = bytes
                .try_into()
                .map_err(|_| anyhow!("vault master key must contain exactly 32 bytes"))?;
            keys.insert(version, key);
        }
        if stored.current_version == 0 || !keys.contains_key(&stored.current_version) {
            bail!("vault key ring does not contain its current version");
        }
        Ok(Self {
            current_version: stored.current_version,
            keys,
        })
    }
}

pub trait KeyStore: Send + Sync {
    fn load(&self) -> Result<Option<KeyRing>>;
    fn save(&self, key_ring: &KeyRing) -> Result<()>;
}

pub fn load_or_create(store: &dyn KeyStore) -> Result<KeyRing> {
    if let Some(key_ring) = store.load()? {
        return Ok(key_ring);
    }
    let key_ring = KeyRing::generate()?;
    store.save(&key_ring)?;
    Ok(key_ring)
}

#[derive(Debug, Clone, Default)]
pub struct MemoryKeyStore {
    value: Arc<Mutex<Option<KeyRing>>>,
}

impl MemoryKeyStore {
    pub fn with_key_ring(key_ring: KeyRing) -> Self {
        Self {
            value: Arc::new(Mutex::new(Some(key_ring))),
        }
    }
}

impl KeyStore for MemoryKeyStore {
    fn load(&self) -> Result<Option<KeyRing>> {
        Ok(self
            .value
            .lock()
            .map_err(|_| anyhow!("vault memory key store was poisoned"))?
            .clone())
    }

    fn save(&self, key_ring: &KeyRing) -> Result<()> {
        *self
            .value
            .lock()
            .map_err(|_| anyhow!("vault memory key store was poisoned"))? = Some(key_ring.clone());
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct FileKeyStore {
    path: PathBuf,
}

impl FileKeyStore {
    /// Explicit development/test key store. Production callers should use [`OsKeyStore`].
    pub fn new(path: impl AsRef<Path>) -> Self {
        Self {
            path: path.as_ref().to_path_buf(),
        }
    }
}

impl KeyStore for FileKeyStore {
    fn load(&self) -> Result<Option<KeyRing>> {
        if !self.path.exists() {
            return Ok(None);
        }
        let encoded = fs::read_to_string(&self.path)
            .with_context(|| format!("reading vault key file {}", self.path.display()))?;
        KeyRing::decode(encoded.trim()).map(Some)
    }

    fn save(&self, key_ring: &KeyRing) -> Result<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("creating vault key directory {}", parent.display()))?;
            restrict_directory(parent)?;
        }
        let encoded = key_ring.encode()?;
        atomic_private_write(&self.path, encoded.as_bytes())
    }
}

#[derive(Debug, Clone)]
pub struct OsKeyStore {
    service: String,
    account: String,
    #[cfg(target_os = "windows")]
    dpapi_path: PathBuf,
}

impl OsKeyStore {
    pub fn new(service: impl Into<String>, account: impl Into<String>) -> Result<Self> {
        let service = service.into();
        let account = account.into();
        if service.trim().is_empty() || account.trim().is_empty() {
            bail!("vault keychain service and account must be non-empty");
        }
        #[cfg(target_os = "windows")]
        let dpapi_path = {
            let app_data = std::env::var_os("APPDATA")
                .map(PathBuf::from)
                .context("APPDATA is unavailable for the Soleaux DPAPI key store")?;
            app_data.join("Soleaux").join(format!(
                "{}-{}.dpapi",
                safe_component(&service),
                safe_component(&account)
            ))
        };
        Ok(Self {
            service,
            account,
            #[cfg(target_os = "windows")]
            dpapi_path,
        })
    }
}

impl KeyStore for OsKeyStore {
    fn load(&self) -> Result<Option<KeyRing>> {
        let encoded = load_os_secret(self)?;
        encoded.as_deref().map(KeyRing::decode).transpose()
    }

    fn save(&self, key_ring: &KeyRing) -> Result<()> {
        save_os_secret(self, &key_ring.encode()?)
    }
}

fn random_key() -> Result<MasterKey> {
    let mut key = [0_u8; 32];
    SystemRandom::new()
        .fill(&mut key)
        .map_err(|_| anyhow!("operating-system randomness is unavailable"))?;
    Ok(key)
}

#[cfg(target_os = "macos")]
fn load_os_secret(store: &OsKeyStore) -> Result<Option<String>> {
    let output = Command::new("/usr/bin/security")
        .args([
            "find-generic-password",
            "-s",
            &store.service,
            "-a",
            &store.account,
            "-w",
        ])
        .output()
        .context("executing macOS Keychain lookup")?;
    if !output.status.success() {
        return Ok(None);
    }
    let value = String::from_utf8(output.stdout).context("decoding macOS Keychain value")?;
    Ok(Some(value.trim().to_string()))
}

#[cfg(target_os = "macos")]
fn save_os_secret(store: &OsKeyStore, encoded: &str) -> Result<()> {
    let status = Command::new("/usr/bin/security")
        .args([
            "add-generic-password",
            "-U",
            "-s",
            &store.service,
            "-a",
            &store.account,
            "-w",
            encoded,
        ])
        .status()
        .context("executing macOS Keychain update")?;
    if !status.success() {
        bail!("macOS Keychain rejected the Soleaux vault key ring");
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn load_os_secret(store: &OsKeyStore) -> Result<Option<String>> {
    let output = Command::new("secret-tool")
        .args([
            "lookup",
            "service",
            &store.service,
            "account",
            &store.account,
        ])
        .output()
        .context("executing Secret Service lookup with secret-tool")?;
    if !output.status.success() || output.stdout.is_empty() {
        return Ok(None);
    }
    let value = String::from_utf8(output.stdout).context("decoding Secret Service value")?;
    Ok(Some(value.trim().to_string()))
}

#[cfg(target_os = "linux")]
fn save_os_secret(store: &OsKeyStore, encoded: &str) -> Result<()> {
    let mut child = Command::new("secret-tool")
        .args([
            "store",
            "--label=Soleaux encrypted artifact vault",
            "service",
            &store.service,
            "account",
            &store.account,
        ])
        .stdin(Stdio::piped())
        .spawn()
        .context("executing Secret Service update with secret-tool")?;
    child
        .stdin
        .as_mut()
        .context("secret-tool stdin was unavailable")?
        .write_all(encoded.as_bytes())?;
    let status = child.wait()?;
    if !status.success() {
        bail!("Secret Service rejected the Soleaux vault key ring");
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn load_os_secret(store: &OsKeyStore) -> Result<Option<String>> {
    if !store.dpapi_path.exists() {
        return Ok(None);
    }
    let path = powershell_quote(&store.dpapi_path.to_string_lossy());
    let script = format!(
        "$b=[IO.File]::ReadAllBytes({path});$p=[Security.Cryptography.ProtectedData]::Unprotect($b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);[Text.Encoding]::UTF8.GetString($p)"
    );
    let output = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .output()
        .context("executing Windows DPAPI key lookup")?;
    if !output.status.success() {
        bail!("Windows DPAPI could not decrypt the Soleaux vault key ring");
    }
    let value = String::from_utf8(output.stdout).context("decoding Windows DPAPI value")?;
    Ok(Some(value.trim().to_string()))
}

#[cfg(target_os = "windows")]
fn save_os_secret(store: &OsKeyStore, encoded: &str) -> Result<()> {
    if let Some(parent) = store.dpapi_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let path = powershell_quote(&store.dpapi_path.to_string_lossy());
    let value = powershell_quote(encoded);
    let script = format!(
        "$p=[Text.Encoding]::UTF8.GetBytes({value});$b=[Security.Cryptography.ProtectedData]::Protect($p,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);[IO.File]::WriteAllBytes({path},$b)"
    );
    let status = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .status()
        .context("executing Windows DPAPI key update")?;
    if !status.success() {
        bail!("Windows DPAPI could not protect the Soleaux vault key ring");
    }
    Ok(())
}

#[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
fn load_os_secret(_store: &OsKeyStore) -> Result<Option<String>> {
    bail!("Soleaux vault key protection is unsupported on this operating system")
}

#[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
fn save_os_secret(_store: &OsKeyStore, _encoded: &str) -> Result<()> {
    bail!("Soleaux vault key protection is unsupported on this operating system")
}

fn atomic_private_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let temporary = path.with_extension(format!("tmp-{}", Uuid::now_v7()));
    fs::write(&temporary, bytes)
        .with_context(|| format!("writing temporary vault key {}", temporary.display()))?;
    restrict_file(&temporary)?;
    fs::rename(&temporary, path)
        .with_context(|| format!("installing vault key file {}", path.display()))?;
    restrict_file(path)
}

#[cfg(unix)]
fn restrict_directory(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .with_context(|| format!("restricting vault key directory {}", path.display()))
}

#[cfg(not(unix))]
fn restrict_directory(_path: &Path) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
fn restrict_file(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .with_context(|| format!("restricting vault key file {}", path.display()))
}

#[cfg(not(unix))]
fn restrict_file(_path: &Path) -> Result<()> {
    Ok(())
}

#[cfg(target_os = "windows")]
fn powershell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

#[cfg(target_os = "windows")]
fn safe_component(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .collect()
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

fn hex_decode(value: &str) -> Result<Vec<u8>> {
    if !value.len().is_multiple_of(2) {
        bail!("hex-encoded vault key has an odd length");
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let high = hex_value(pair[0])?;
            let low = hex_value(pair[1])?;
            Ok((high << 4) | low)
        })
        .collect()
}

fn hex_value(value: u8) -> Result<u8> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        b'A'..=b'F' => Ok(value - b'A' + 10),
        _ => bail!("invalid hex character in vault key"),
    }
}
