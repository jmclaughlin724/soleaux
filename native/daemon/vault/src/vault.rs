use crate::keyring::{KeyRing, KeyStore, load_or_create};
use anyhow::{Context, Result, anyhow, bail};
use ring::{
    aead::{self, Aad, LessSafeKey, Nonce, UnboundKey},
    rand::{SecureRandom, SystemRandom},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use soleaux_redaction::redact_json_value;
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Arc,
};
use uuid::Uuid;

const MAGIC: &[u8; 8] = b"SLXVAULT";
const FORMAT_VERSION: u16 = 1;
const MAX_HEADER_BYTES: usize = 1024 * 1024;
const MAX_ARTIFACT_BYTES: usize = 512 * 1024 * 1024;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactSensitivity {
    Public,
    Internal,
    Confidential,
    Secret,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ArtifactHeader {
    pub schema_version: String,
    pub workspace_id: Uuid,
    pub content_hash: String,
    pub media_type: String,
    pub byte_length: u64,
    pub sensitivity: ArtifactSensitivity,
    pub key_version: u32,
    pub nonce: [u8; 12],
    pub metadata: Value,
    pub metadata_redactions: usize,
    pub created_at_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ArtifactDescriptor {
    pub workspace_id: Uuid,
    pub content_hash: String,
    pub media_type: String,
    pub byte_length: u64,
    pub sensitivity: ArtifactSensitivity,
    pub key_version: u32,
    pub metadata: Value,
    pub metadata_redactions: usize,
    pub created_at_unix_ms: i64,
    pub storage_path: String,
    pub encrypted: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ArtifactContent {
    pub descriptor: ArtifactDescriptor,
    pub bytes: Vec<u8>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct VaultVerificationReport {
    pub workspace_id: Uuid,
    pub artifact_count: usize,
    pub plaintext_bytes: u64,
    pub encrypted_bytes: u64,
    pub key_versions: Vec<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct VaultRotationReport {
    pub workspace_id: Uuid,
    pub previous_key_version: u32,
    pub current_key_version: u32,
    pub rotated_artifacts: usize,
    pub plaintext_bytes: u64,
}

#[derive(Clone)]
pub struct ArtifactVault {
    root: Arc<PathBuf>,
    key_store: Arc<dyn KeyStore>,
}

impl ArtifactVault {
    pub fn open(root: impl AsRef<Path>, key_store: Arc<dyn KeyStore>) -> Result<Self> {
        let root = root.as_ref().to_path_buf();
        fs::create_dir_all(root.join("workspaces"))
            .with_context(|| format!("creating artifact vault {}", root.display()))?;
        let _ = load_or_create(key_store.as_ref())?;
        Ok(Self {
            root: Arc::new(root),
            key_store,
        })
    }

    pub fn root(&self) -> &Path {
        self.root.as_ref()
    }

    pub fn put(
        &self,
        workspace_id: Uuid,
        media_type: &str,
        bytes: &[u8],
        sensitivity: ArtifactSensitivity,
        metadata: Value,
    ) -> Result<ArtifactDescriptor> {
        validate_media_type(media_type)?;
        if bytes.len() > MAX_ARTIFACT_BYTES {
            bail!("artifact exceeds the 512 MiB local vault limit");
        }
        let content_hash = blake3::hash(bytes).to_hex().to_string();
        let destination = self.artifact_path(workspace_id, &content_hash)?;
        if destination.exists() {
            let existing = self.read(workspace_id, &content_hash)?;
            if existing.descriptor.media_type != media_type
                || existing.descriptor.sensitivity != sensitivity
            {
                bail!("content-addressed artifact collision: immutable metadata differs");
            }
            return Ok(existing.descriptor);
        }

        let key_ring = load_or_create(self.key_store.as_ref())?;
        let key_version = key_ring.current_version();
        let redaction = redact_json_value(metadata);
        let header = ArtifactHeader {
            schema_version: "soleaux.artifact-envelope/v1".to_string(),
            workspace_id,
            content_hash: content_hash.clone(),
            media_type: media_type.to_string(),
            byte_length: u64::try_from(bytes.len()).unwrap_or(u64::MAX),
            sensitivity,
            key_version,
            nonce: random_nonce()?,
            metadata: redaction.value,
            metadata_redactions: redaction.count,
            created_at_unix_ms: unix_ms(),
        };
        let envelope = seal(&key_ring, &header, bytes)?;
        atomic_write(&destination, &envelope)?;
        self.read(workspace_id, &content_hash)
            .map(|content| content.descriptor)
    }

    pub fn read(&self, workspace_id: Uuid, content_hash: &str) -> Result<ArtifactContent> {
        validate_hash(content_hash)?;
        let path = self.artifact_path(workspace_id, content_hash)?;
        let envelope = fs::read(&path)
            .with_context(|| format!("reading encrypted artifact {}", path.display()))?;
        let key_ring = load_or_create(self.key_store.as_ref())?;
        let (header, bytes) = open(&key_ring, &envelope)?;
        if header.workspace_id != workspace_id || header.content_hash != content_hash {
            bail!("artifact envelope identity does not match its vault path");
        }
        Ok(ArtifactContent {
            descriptor: descriptor(&path, &header),
            bytes,
        })
    }

    pub fn delete(&self, workspace_id: Uuid, content_hash: &str) -> Result<bool> {
        validate_hash(content_hash)?;
        let path = self.artifact_path(workspace_id, content_hash)?;
        if !path.exists() {
            return Ok(false);
        }
        let _ = self.read(workspace_id, content_hash)?;
        fs::remove_file(&path)
            .with_context(|| format!("deleting encrypted artifact {}", path.display()))?;
        remove_empty_parent(path.parent(), &self.workspace_root(workspace_id))?;
        Ok(true)
    }

    pub fn verify_workspace(&self, workspace_id: Uuid) -> Result<VaultVerificationReport> {
        let mut artifact_count = 0usize;
        let mut plaintext_bytes = 0u64;
        let mut encrypted_bytes = 0u64;
        let mut key_versions = Vec::new();
        for path in self.workspace_files(workspace_id)? {
            let hash = hash_from_path(&path)?;
            let content = self.read(workspace_id, &hash)?;
            artifact_count = artifact_count.saturating_add(1);
            plaintext_bytes = plaintext_bytes.saturating_add(content.descriptor.byte_length);
            encrypted_bytes = encrypted_bytes.saturating_add(fs::metadata(&path)?.len());
            if !key_versions.contains(&content.descriptor.key_version) {
                key_versions.push(content.descriptor.key_version);
            }
        }
        key_versions.sort_unstable();
        Ok(VaultVerificationReport {
            workspace_id,
            artifact_count,
            plaintext_bytes,
            encrypted_bytes,
            key_versions,
        })
    }

    pub fn rotate_workspace_key(&self, workspace_id: Uuid) -> Result<VaultRotationReport> {
        let mut key_ring = load_or_create(self.key_store.as_ref())?;
        let previous_key_version = key_ring.current_version();
        let current_key_version = key_ring.rotate()?;
        self.key_store.save(&key_ring)?;

        let paths = self.workspace_files(workspace_id)?;
        let mut rotated_artifacts = 0usize;
        let mut plaintext_bytes = 0u64;
        for path in paths {
            let envelope = fs::read(&path)?;
            let (mut header, plaintext) = open(&key_ring, &envelope)?;
            if header.workspace_id != workspace_id {
                bail!("artifact workspace identity changed during rotation");
            }
            if header.key_version == current_key_version {
                continue;
            }
            header.key_version = current_key_version;
            header.nonce = random_nonce()?;
            let replacement = seal(&key_ring, &header, &plaintext)?;
            atomic_write(&path, &replacement)?;
            let verified = self.read(workspace_id, &header.content_hash)?;
            if verified.descriptor.key_version != current_key_version {
                bail!("artifact key rotation did not persist the current key version");
            }
            rotated_artifacts = rotated_artifacts.saturating_add(1);
            plaintext_bytes = plaintext_bytes.saturating_add(header.byte_length);
        }
        Ok(VaultRotationReport {
            workspace_id,
            previous_key_version,
            current_key_version,
            rotated_artifacts,
            plaintext_bytes,
        })
    }

    fn artifact_path(&self, workspace_id: Uuid, content_hash: &str) -> Result<PathBuf> {
        validate_hash(content_hash)?;
        Ok(self
            .workspace_root(workspace_id)
            .join(&content_hash[..2])
            .join(format!("{content_hash}.sxv")))
    }

    fn workspace_root(&self, workspace_id: Uuid) -> PathBuf {
        self.root.join("workspaces").join(workspace_id.to_string())
    }

    fn workspace_files(&self, workspace_id: Uuid) -> Result<Vec<PathBuf>> {
        let root = self.workspace_root(workspace_id);
        if !root.exists() {
            return Ok(Vec::new());
        }
        let mut files = Vec::new();
        for prefix in fs::read_dir(&root)? {
            let prefix = prefix?;
            if !prefix.file_type()?.is_dir() {
                continue;
            }
            for entry in fs::read_dir(prefix.path())? {
                let entry = entry?;
                if entry.file_type()?.is_file()
                    && entry.path().extension().is_some_and(|value| value == "sxv")
                {
                    files.push(entry.path());
                }
            }
        }
        files.sort();
        Ok(files)
    }
}

fn seal(key_ring: &KeyRing, header: &ArtifactHeader, plaintext: &[u8]) -> Result<Vec<u8>> {
    let header_bytes = serde_json::to_vec(header)?;
    if header_bytes.len() > MAX_HEADER_BYTES {
        bail!("artifact envelope header exceeds one MiB");
    }
    let key = key_ring.derive_workspace_key(header.workspace_id, header.key_version)?;
    let unbound = UnboundKey::new(&aead::CHACHA20_POLY1305, &key)
        .map_err(|_| anyhow!("constructing artifact encryption key failed"))?;
    let key = LessSafeKey::new(unbound);
    let nonce = Nonce::assume_unique_for_key(header.nonce);
    let mut ciphertext = plaintext.to_vec();
    key.seal_in_place_append_tag(nonce, Aad::from(header_bytes.as_slice()), &mut ciphertext)
        .map_err(|_| anyhow!("encrypting artifact failed"))?;

    let mut envelope = Vec::with_capacity(8 + 2 + 4 + header_bytes.len() + ciphertext.len());
    envelope.extend_from_slice(MAGIC);
    envelope.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
    envelope.extend_from_slice(
        &u32::try_from(header_bytes.len())
            .context("artifact header length exceeds u32")?
            .to_le_bytes(),
    );
    envelope.extend_from_slice(&header_bytes);
    envelope.extend_from_slice(&ciphertext);
    Ok(envelope)
}

fn open(key_ring: &KeyRing, envelope: &[u8]) -> Result<(ArtifactHeader, Vec<u8>)> {
    if envelope.len() < MAGIC.len() + 2 + 4 + aead::CHACHA20_POLY1305.tag_len() {
        bail!("artifact envelope is truncated");
    }
    if &envelope[..MAGIC.len()] != MAGIC {
        bail!("artifact envelope magic is invalid");
    }
    let version = u16::from_le_bytes(
        envelope[MAGIC.len()..MAGIC.len() + 2]
            .try_into()
            .expect("fixed format version range"),
    );
    if version != FORMAT_VERSION {
        bail!("unsupported artifact envelope version {version}");
    }
    let header_start = MAGIC.len() + 2 + 4;
    let header_length = u32::from_le_bytes(
        envelope[MAGIC.len() + 2..header_start]
            .try_into()
            .expect("fixed header length range"),
    ) as usize;
    if header_length > MAX_HEADER_BYTES || header_start.saturating_add(header_length) > envelope.len()
    {
        bail!("artifact envelope header is invalid");
    }
    let header_end = header_start + header_length;
    let header_bytes = &envelope[header_start..header_end];
    let header: ArtifactHeader =
        serde_json::from_slice(header_bytes).context("decoding artifact envelope header")?;
    if header.schema_version != "soleaux.artifact-envelope/v1" {
        bail!("unsupported artifact envelope schema");
    }
    let key = key_ring.derive_workspace_key(header.workspace_id, header.key_version)?;
    let unbound = UnboundKey::new(&aead::CHACHA20_POLY1305, &key)
        .map_err(|_| anyhow!("constructing artifact decryption key failed"))?;
    let key = LessSafeKey::new(unbound);
    let nonce = Nonce::assume_unique_for_key(header.nonce);
    let mut ciphertext = envelope[header_end..].to_vec();
    let plaintext = key
        .open_in_place(nonce, Aad::from(header_bytes), &mut ciphertext)
        .map_err(|_| anyhow!("artifact authentication failed"))?
        .to_vec();
    if u64::try_from(plaintext.len()).unwrap_or(u64::MAX) != header.byte_length {
        bail!("artifact plaintext length does not match its envelope");
    }
    if blake3::hash(&plaintext).to_hex().as_str() != header.content_hash {
        bail!("artifact plaintext hash does not match its envelope");
    }
    Ok((header, plaintext))
}

fn descriptor(path: &Path, header: &ArtifactHeader) -> ArtifactDescriptor {
    ArtifactDescriptor {
        workspace_id: header.workspace_id,
        content_hash: header.content_hash.clone(),
        media_type: header.media_type.clone(),
        byte_length: header.byte_length,
        sensitivity: header.sensitivity,
        key_version: header.key_version,
        metadata: header.metadata.clone(),
        metadata_redactions: header.metadata_redactions,
        created_at_unix_ms: header.created_at_unix_ms,
        storage_path: path.to_string_lossy().to_string(),
        encrypted: true,
    }
}

fn random_nonce() -> Result<[u8; 12]> {
    let mut nonce = [0_u8; 12];
    SystemRandom::new()
        .fill(&mut nonce)
        .map_err(|_| anyhow!("operating-system randomness is unavailable"))?;
    Ok(nonce)
}

fn validate_media_type(value: &str) -> Result<()> {
    if value.trim().is_empty() || !value.contains('/') || value.len() > 255 {
        bail!("artifact media type is invalid");
    }
    Ok(())
}

fn validate_hash(value: &str) -> Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        bail!("artifact content hash must be 64 lowercase hexadecimal characters");
    }
    Ok(())
}

fn hash_from_path(path: &Path) -> Result<String> {
    let name = path
        .file_stem()
        .and_then(|value| value.to_str())
        .context("artifact path does not contain a UTF-8 hash")?;
    validate_hash(name)?;
    Ok(name.to_string())
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().context("artifact path has no parent")?;
    fs::create_dir_all(parent)
        .with_context(|| format!("creating artifact directory {}", parent.display()))?;
    let temporary = parent.join(format!(".{}.tmp", Uuid::now_v7()));
    let mut file = File::create(&temporary)
        .with_context(|| format!("creating temporary artifact {}", temporary.display()))?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    fs::rename(&temporary, path)
        .with_context(|| format!("installing encrypted artifact {}", path.display()))?;
    sync_directory(parent)?;
    Ok(())
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<()> {
    File::open(path)?.sync_all()?;
    Ok(())
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<()> {
    Ok(())
}

fn remove_empty_parent(parent: Option<&Path>, workspace_root: &Path) -> Result<()> {
    let Some(parent) = parent else {
        return Ok(());
    };
    if parent != workspace_root && parent.read_dir()?.next().is_none() {
        fs::remove_dir(parent)?;
    }
    if workspace_root.exists() && workspace_root.read_dir()?.next().is_none() {
        fs::remove_dir(workspace_root)?;
    }
    Ok(())
}

fn unix_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}
