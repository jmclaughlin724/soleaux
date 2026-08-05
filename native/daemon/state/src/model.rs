use anyhow::{Result, bail};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::Value;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash, Ord, PartialOrd)]
#[serde(rename_all = "snake_case")]
pub enum EntityKind {
    PlatformAccount,
    NativeMapping,
    Session,
    Turn,
    Message,
    ContentPart,
    MemoryClaim,
    Rule,
    Skill,
    Agent,
    Handoff,
    Run,
    Subagent,
    Approval,
    Conflict,
    Materialization,
    Artifact,
}

impl EntityKind {
    pub const ALL: [Self; 17] = [
        Self::PlatformAccount,
        Self::NativeMapping,
        Self::Session,
        Self::Turn,
        Self::Message,
        Self::ContentPart,
        Self::MemoryClaim,
        Self::Rule,
        Self::Skill,
        Self::Agent,
        Self::Handoff,
        Self::Run,
        Self::Subagent,
        Self::Approval,
        Self::Conflict,
        Self::Materialization,
        Self::Artifact,
    ];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::PlatformAccount => "platform_account",
            Self::NativeMapping => "native_mapping",
            Self::Session => "session",
            Self::Turn => "turn",
            Self::Message => "message",
            Self::ContentPart => "content_part",
            Self::MemoryClaim => "memory_claim",
            Self::Rule => "rule",
            Self::Skill => "skill",
            Self::Agent => "agent",
            Self::Handoff => "handoff",
            Self::Run => "run",
            Self::Subagent => "subagent",
            Self::Approval => "approval",
            Self::Conflict => "conflict",
            Self::Materialization => "materialization",
            Self::Artifact => "artifact",
        }
    }

    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "platform_account" => Ok(Self::PlatformAccount),
            "native_mapping" => Ok(Self::NativeMapping),
            "session" => Ok(Self::Session),
            "turn" => Ok(Self::Turn),
            "message" => Ok(Self::Message),
            "content_part" => Ok(Self::ContentPart),
            "memory_claim" => Ok(Self::MemoryClaim),
            "rule" => Ok(Self::Rule),
            "skill" => Ok(Self::Skill),
            "agent" => Ok(Self::Agent),
            "handoff" => Ok(Self::Handoff),
            "run" => Ok(Self::Run),
            "subagent" => Ok(Self::Subagent),
            "approval" => Ok(Self::Approval),
            "conflict" => Ok(Self::Conflict),
            "materialization" => Ok(Self::Materialization),
            "artifact" => Ok(Self::Artifact),
            other => bail!("unsupported canonical entity kind: {other}"),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash, Default)]
#[serde(rename_all = "snake_case")]
pub enum Sensitivity {
    Public,
    #[default]
    Internal,
    Confidential,
    Secret,
}

impl Sensitivity {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Public => "public",
            Self::Internal => "internal",
            Self::Confidential => "confidential",
            Self::Secret => "secret",
        }
    }

    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "public" => Ok(Self::Public),
            "internal" => Ok(Self::Internal),
            "confidential" => Ok(Self::Confidential),
            "secret" => Ok(Self::Secret),
            other => bail!("unsupported sensitivity: {other}"),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum RelationshipKind {
    Parent,
    Contains,
    Lineage,
    Supersedes,
    Evidence,
    NativeMapping,
    Artifact,
    DependsOn,
    Materializes,
    ConflictsWith,
    ApprovedBy,
    Spawned,
}

impl RelationshipKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Parent => "parent",
            Self::Contains => "contains",
            Self::Lineage => "lineage",
            Self::Supersedes => "supersedes",
            Self::Evidence => "evidence",
            Self::NativeMapping => "native_mapping",
            Self::Artifact => "artifact",
            Self::DependsOn => "depends_on",
            Self::Materializes => "materializes",
            Self::ConflictsWith => "conflicts_with",
            Self::ApprovedBy => "approved_by",
            Self::Spawned => "spawned",
        }
    }

    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "parent" => Ok(Self::Parent),
            "contains" => Ok(Self::Contains),
            "lineage" => Ok(Self::Lineage),
            "supersedes" => Ok(Self::Supersedes),
            "evidence" => Ok(Self::Evidence),
            "native_mapping" => Ok(Self::NativeMapping),
            "artifact" => Ok(Self::Artifact),
            "depends_on" => Ok(Self::DependsOn),
            "materializes" => Ok(Self::Materializes),
            "conflicts_with" => Ok(Self::ConflictsWith),
            "approved_by" => Ok(Self::ApprovedBy),
            "spawned" => Ok(Self::Spawned),
            other => bail!("unsupported canonical relationship: {other}"),
        }
    }
}

pub trait CanonicalPayload:
    Serialize + DeserializeOwned + Clone + Send + Sync + std::fmt::Debug + PartialEq + 'static
{
    const KIND: EntityKind;

    fn validate(&self) -> Result<()> {
        Ok(())
    }
}

fn require(value: &str, label: &str) -> Result<()> {
    if value.trim().is_empty() {
        bail!("{label} must be non-empty");
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PlatformAccountPayload {
    pub platform: String,
    pub native_account_id: String,
    pub display_name: String,
    #[serde(default)]
    pub capabilities: Value,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for PlatformAccountPayload {
    const KIND: EntityKind = EntityKind::PlatformAccount;

    fn validate(&self) -> Result<()> {
        require(&self.platform, "platform")?;
        require(&self.native_account_id, "native account id")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct NativeMappingPayload {
    pub platform: String,
    pub native_kind: String,
    pub native_id: String,
    pub canonical_kind: EntityKind,
    pub canonical_id: Uuid,
    pub adapter_version: String,
    #[serde(default)]
    pub cursor: Option<String>,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for NativeMappingPayload {
    const KIND: EntityKind = EntityKind::NativeMapping;

    fn validate(&self) -> Result<()> {
        require(&self.platform, "platform")?;
        require(&self.native_kind, "native kind")?;
        require(&self.native_id, "native id")?;
        require(&self.adapter_version, "adapter version")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SessionPayload {
    pub platform: String,
    #[serde(default)]
    pub native_session_id: Option<String>,
    pub title: String,
    #[serde(default)]
    pub parent_session_id: Option<Uuid>,
    pub lineage_root_id: Uuid,
    pub session_state: String,
    #[serde(default)]
    pub repository_ref: Value,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for SessionPayload {
    const KIND: EntityKind = EntityKind::Session;

    fn validate(&self) -> Result<()> {
        require(&self.platform, "platform")?;
        require(&self.session_state, "session state")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurnPayload {
    pub session_id: Uuid,
    pub ordinal: u64,
    pub actor: String,
    #[serde(default)]
    pub native_turn_id: Option<String>,
    pub turn_state: String,
    #[serde(default)]
    pub usage: Value,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for TurnPayload {
    const KIND: EntityKind = EntityKind::Turn;

    fn validate(&self) -> Result<()> {
        require(&self.actor, "turn actor")?;
        require(&self.turn_state, "turn state")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct MessagePayload {
    pub session_id: Uuid,
    pub turn_id: Uuid,
    pub role: String,
    #[serde(default)]
    pub native_message_id: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
    pub message_state: String,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for MessagePayload {
    const KIND: EntityKind = EntityKind::Message;

    fn validate(&self) -> Result<()> {
        require(&self.role, "message role")?;
        require(&self.message_state, "message state")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ContentPartPayload {
    pub message_id: Uuid,
    pub ordinal: u64,
    pub media_type: String,
    #[serde(default)]
    pub text: Option<String>,
    #[serde(default)]
    pub artifact_id: Option<Uuid>,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for ContentPartPayload {
    const KIND: EntityKind = EntityKind::ContentPart;

    fn validate(&self) -> Result<()> {
        require(&self.media_type, "content media type")?;
        if self.text.is_none() && self.artifact_id.is_none() {
            bail!("content part requires text or artifact id");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct MemoryClaimPayload {
    pub claim_type: String,
    pub subject: String,
    pub content: String,
    pub memory_state: String,
    pub confidence: f64,
    #[serde(default)]
    pub evidence_uris: Vec<String>,
    #[serde(default)]
    pub supersedes_id: Option<Uuid>,
    #[serde(default)]
    pub source_session_id: Option<Uuid>,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for MemoryClaimPayload {
    const KIND: EntityKind = EntityKind::MemoryClaim;

    fn validate(&self) -> Result<()> {
        require(&self.claim_type, "claim type")?;
        require(&self.subject, "memory subject")?;
        require(&self.content, "memory content")?;
        require(&self.memory_state, "memory state")?;
        if !(0.0..=1.0).contains(&self.confidence) {
            bail!("memory confidence must be between zero and one");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct RulePayload {
    pub name: String,
    pub scope: String,
    pub guidance: String,
    pub enforcement: String,
    pub object_revision: String,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for RulePayload {
    const KIND: EntityKind = EntityKind::Rule;

    fn validate(&self) -> Result<()> {
        require(&self.name, "rule name")?;
        require(&self.scope, "rule scope")?;
        require(&self.guidance, "rule guidance")?;
        require(&self.object_revision, "rule revision")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SkillPayload {
    pub name: String,
    pub description: String,
    pub instructions: String,
    pub object_revision: String,
    #[serde(default)]
    pub compatibility: Value,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for SkillPayload {
    const KIND: EntityKind = EntityKind::Skill;

    fn validate(&self) -> Result<()> {
        require(&self.name, "skill name")?;
        require(&self.instructions, "skill instructions")?;
        require(&self.object_revision, "skill revision")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AgentPayload {
    pub name: String,
    pub description: String,
    pub instructions: String,
    pub object_revision: String,
    #[serde(default)]
    pub model_hint: Option<String>,
    #[serde(default)]
    pub allowed_tools: Vec<String>,
    #[serde(default)]
    pub compatibility: Value,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for AgentPayload {
    const KIND: EntityKind = EntityKind::Agent;

    fn validate(&self) -> Result<()> {
        require(&self.name, "agent name")?;
        require(&self.instructions, "agent instructions")?;
        require(&self.object_revision, "agent revision")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct HandoffPayload {
    pub source_session_id: Uuid,
    pub destination_platform: String,
    #[serde(default)]
    pub destination_session_id: Option<Uuid>,
    pub handoff_state: String,
    pub payload_hash: String,
    pub signature: String,
    #[serde(default)]
    pub git_state: Value,
    #[serde(default)]
    pub code_state: Value,
    #[serde(default)]
    pub artifact_ids: Vec<Uuid>,
    #[serde(default)]
    pub permissions: Value,
    #[serde(default)]
    pub exclusions: Value,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for HandoffPayload {
    const KIND: EntityKind = EntityKind::Handoff;

    fn validate(&self) -> Result<()> {
        require(&self.destination_platform, "destination platform")?;
        require(&self.handoff_state, "handoff state")?;
        require(&self.payload_hash, "handoff payload hash")?;
        require(&self.signature, "handoff signature")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct RunPayload {
    #[serde(default)]
    pub session_id: Option<Uuid>,
    pub operation_key: String,
    pub run_type: String,
    pub run_state: String,
    #[serde(default)]
    pub budget: Value,
    #[serde(default)]
    pub result: Option<Value>,
    #[serde(default)]
    pub error: Option<Value>,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for RunPayload {
    const KIND: EntityKind = EntityKind::Run;

    fn validate(&self) -> Result<()> {
        require(&self.operation_key, "run operation key")?;
        require(&self.run_type, "run type")?;
        require(&self.run_state, "run state")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SubagentPayload {
    pub run_id: Uuid,
    #[serde(default)]
    pub parent_subagent_id: Option<Uuid>,
    #[serde(default)]
    pub native_subagent_id: Option<String>,
    pub role: String,
    pub subagent_state: String,
    #[serde(default)]
    pub result: Option<Value>,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for SubagentPayload {
    const KIND: EntityKind = EntityKind::Subagent;

    fn validate(&self) -> Result<()> {
        require(&self.role, "subagent role")?;
        require(&self.subagent_state, "subagent state")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ApprovalPayload {
    pub run_id: Uuid,
    #[serde(default)]
    pub command_id: Option<Uuid>,
    pub risk: String,
    pub approval_state: String,
    pub requested_action: Value,
    #[serde(default)]
    pub decision: Option<Value>,
    #[serde(default)]
    pub decided_by: Option<String>,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for ApprovalPayload {
    const KIND: EntityKind = EntityKind::Approval;

    fn validate(&self) -> Result<()> {
        require(&self.risk, "approval risk")?;
        require(&self.approval_state, "approval state")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ConflictPayload {
    pub left_entity_id: Uuid,
    pub right_entity_id: Uuid,
    pub conflict_type: String,
    pub conflict_state: String,
    #[serde(default)]
    pub resolution: Option<Value>,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for ConflictPayload {
    const KIND: EntityKind = EntityKind::Conflict;

    fn validate(&self) -> Result<()> {
        if self.left_entity_id == self.right_entity_id {
            bail!("conflict entities must differ");
        }
        require(&self.conflict_type, "conflict type")?;
        require(&self.conflict_state, "conflict state")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct MaterializationPayload {
    pub object_id: Uuid,
    pub target_platform: String,
    pub target_path: String,
    pub object_revision: String,
    pub origin: String,
    pub idempotency_key: String,
    pub materialization_state: String,
    #[serde(default)]
    pub report: Value,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for MaterializationPayload {
    const KIND: EntityKind = EntityKind::Materialization;

    fn validate(&self) -> Result<()> {
        require(&self.target_platform, "target platform")?;
        require(&self.target_path, "target path")?;
        require(&self.object_revision, "object revision")?;
        require(&self.origin, "materialization origin")?;
        require(&self.idempotency_key, "materialization idempotency key")?;
        require(&self.materialization_state, "materialization state")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ArtifactPayload {
    pub content_hash: String,
    pub media_type: String,
    pub byte_length: u64,
    pub encrypted: bool,
    #[serde(default)]
    pub vault_key_id: Option<String>,
    #[serde(default)]
    pub storage_uri: Option<String>,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for ArtifactPayload {
    const KIND: EntityKind = EntityKind::Artifact;

    fn validate(&self) -> Result<()> {
        require(&self.content_hash, "artifact content hash")?;
        require(&self.media_type, "artifact media type")?;
        if self.encrypted && self.vault_key_id.is_none() {
            bail!("encrypted artifact requires a vault key id");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct CanonicalEntityInput<T> {
    #[serde(default)]
    pub id: Option<Uuid>,
    #[serde(default)]
    pub workspace_id: Option<Uuid>,
    #[serde(default)]
    pub parent_id: Option<Uuid>,
    #[serde(default)]
    pub origin_platform: Option<String>,
    #[serde(default)]
    pub native_id: Option<String>,
    pub state: String,
    #[serde(default)]
    pub sensitivity: Sensitivity,
    #[serde(default)]
    pub idempotency_key: Option<String>,
    #[serde(default)]
    pub expected_revision: Option<u64>,
    #[serde(default)]
    pub expires_at_unix_ms: Option<i64>,
    pub payload: T,
}

impl<T> CanonicalEntityInput<T> {
    pub fn active(payload: T) -> Self {
        Self {
            id: None,
            workspace_id: None,
            parent_id: None,
            origin_platform: None,
            native_id: None,
            state: "active".to_string(),
            sensitivity: Sensitivity::Internal,
            idempotency_key: None,
            expected_revision: None,
            expires_at_unix_ms: None,
            payload,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct CanonicalRecord<T> {
    pub id: Uuid,
    pub kind: EntityKind,
    pub workspace_id: Option<Uuid>,
    pub parent_id: Option<Uuid>,
    pub origin_platform: Option<String>,
    pub native_id: Option<String>,
    pub state: String,
    pub sensitivity: Sensitivity,
    pub revision: u64,
    pub payload_hash: String,
    pub idempotency_key: Option<String>,
    pub expires_at_unix_ms: Option<i64>,
    pub created_at_unix_ms: i64,
    pub updated_at_unix_ms: i64,
    pub tombstoned_at_unix_ms: Option<i64>,
    pub payload: T,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct EntityLinkInput {
    pub source_id: Uuid,
    pub relationship: RelationshipKind,
    pub target_id: Uuid,
    #[serde(default)]
    pub metadata: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct EntityLinkRecord {
    pub source_id: Uuid,
    pub relationship: RelationshipKind,
    pub target_id: Uuid,
    pub metadata: Value,
    pub created_at_unix_ms: i64,
    pub updated_at_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AdapterCursorInput {
    pub adapter: String,
    pub scope: String,
    pub cursor: String,
    #[serde(default)]
    pub etag: Option<String>,
    #[serde(default)]
    pub watermark: Option<String>,
    #[serde(default)]
    pub expected_revision: Option<u64>,
    #[serde(default)]
    pub metadata: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AdapterCursorRecord {
    pub adapter: String,
    pub scope: String,
    pub cursor: String,
    pub etag: Option<String>,
    pub watermark: Option<String>,
    pub revision: u64,
    pub metadata: Value,
    pub updated_at_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct RetentionPolicyInput {
    #[serde(default)]
    pub id: Option<Uuid>,
    #[serde(default)]
    pub workspace_id: Option<Uuid>,
    #[serde(default)]
    pub entity_kind: Option<EntityKind>,
    pub retain_for_ms: u64,
    pub tombstone_grace_ms: u64,
    pub enabled: bool,
    #[serde(default)]
    pub expected_revision: Option<u64>,
    #[serde(default)]
    pub metadata: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct RetentionPolicyRecord {
    pub id: Uuid,
    pub workspace_id: Option<Uuid>,
    pub entity_kind: Option<EntityKind>,
    pub retain_for_ms: u64,
    pub tombstone_grace_ms: u64,
    pub enabled: bool,
    pub revision: u64,
    pub metadata: Value,
    pub created_at_unix_ms: i64,
    pub updated_at_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TombstoneRecord {
    pub entity_id: Uuid,
    pub entity_kind: EntityKind,
    pub workspace_id: Option<Uuid>,
    pub payload_hash: String,
    pub reason: String,
    pub actor: String,
    pub tombstoned_at_unix_ms: i64,
    pub purged_at_unix_ms: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct OperationLease {
    pub operation_key: String,
    pub request_hash: String,
    pub operation_kind: String,
    pub workspace_id: Option<Uuid>,
    pub state: String,
    pub lease_id: Option<Uuid>,
    pub owner_id: Option<String>,
    pub attempt: u64,
    pub lease_expires_at_unix_ms: Option<i64>,
    pub result: Option<Value>,
    pub error: Option<Value>,
    pub created_at_unix_ms: i64,
    pub updated_at_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub enum OperationLeaseOutcome {
    Acquired(OperationLease),
    InFlight(OperationLease),
    Replayed(Value),
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AuditEntry {
    pub sequence: i64,
    pub event_id: Uuid,
    pub event_type: String,
    pub workspace_id: Option<Uuid>,
    pub entity_id: Option<Uuid>,
    pub payload: Value,
    pub payload_hash: String,
    pub previous_event_hash: Option<String>,
    pub event_hash: String,
    pub created_at_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct BackupManifest {
    pub schema_version: i64,
    pub path: String,
    pub byte_length: u64,
    pub blake3: String,
    pub created_at_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct IntegrityReport {
    pub schema_version: i64,
    pub integrity: String,
    pub foreign_key_violations: u64,
    pub audit_chain_valid: bool,
    pub entity_count: u64,
    pub link_count: u64,
    pub operation_count: u64,
    pub tombstone_count: u64,
    pub database_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SerializedEntityRecord {
    pub id: Uuid,
    pub kind: EntityKind,
    pub workspace_id: Option<Uuid>,
    pub parent_id: Option<Uuid>,
    pub origin_platform: Option<String>,
    pub native_id: Option<String>,
    pub state: String,
    pub sensitivity: Sensitivity,
    pub revision: u64,
    pub payload: Value,
    pub payload_hash: String,
    pub idempotency_key: Option<String>,
    pub expires_at_unix_ms: Option<i64>,
    pub created_at_unix_ms: i64,
    pub updated_at_unix_ms: i64,
    pub tombstoned_at_unix_ms: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct StateSnapshot {
    pub schema_version: i64,
    pub entities: Vec<SerializedEntityRecord>,
    pub links: Vec<EntityLinkRecord>,
    pub adapter_cursors: Vec<AdapterCursorRecord>,
    pub retention_policies: Vec<RetentionPolicyRecord>,
    pub tombstones: Vec<TombstoneRecord>,
    pub operations: Vec<OperationLease>,
    pub audit: Vec<AuditEntry>,
}
