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
    Workspace,
    ClientRegistration,
    ClientWorkspaceBinding,
}

impl EntityKind {
    pub const ALL: [Self; 20] = [
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
        Self::Workspace,
        Self::ClientRegistration,
        Self::ClientWorkspaceBinding,
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
            Self::Workspace => "workspace",
            Self::ClientRegistration => "client_registration",
            Self::ClientWorkspaceBinding => "client_workspace_binding",
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
            "workspace" => Ok(Self::Workspace),
            "client_registration" => Ok(Self::ClientRegistration),
            "client_workspace_binding" => Ok(Self::ClientWorkspaceBinding),
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
        validate_session_state(&self.session_state)
    }
}

pub const SESSION_STATE_ACTIVE: &str = "active";
pub const SESSION_STATE_ARCHIVED: &str = "archived";

pub fn validate_session_state(value: &str) -> Result<()> {
    if value == SESSION_STATE_ACTIVE || value == SESSION_STATE_ARCHIVED {
        return Ok(());
    }
    bail!("unsupported session state: {value}")
}

/// Same-platform lifecycle only: archive suspends an active session and
/// resume reactivates an archived one. Cross-platform continuation is a
/// signed handoff, never a session-state transition.
pub fn validate_session_transition(from: &str, to: &str) -> Result<()> {
    validate_session_state(from)?;
    validate_session_state(to)?;
    match (from, to) {
        (SESSION_STATE_ACTIVE, SESSION_STATE_ARCHIVED)
        | (SESSION_STATE_ARCHIVED, SESSION_STATE_ACTIVE) => Ok(()),
        _ => bail!("unsupported session transition: {from} -> {to}"),
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
    pub scope: String,
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
        validate_memory_scope(&self.scope)?;
        validate_memory_state(&self.memory_state)?;
        if !(0.0..=1.0).contains(&self.confidence) {
            bail!("memory confidence must be between zero and one");
        }
        Ok(())
    }
}

pub const MEMORY_STATE_PROPOSED: &str = "proposed";
pub const MEMORY_STATE_VALIDATED: &str = "validated";
pub const MEMORY_STATE_ACTIVE: &str = "active";
pub const MEMORY_STATE_SUPERSEDED: &str = "superseded";
pub const MEMORY_STATE_TOMBSTONED: &str = "tombstoned";
pub const MEMORY_STATE_REJECTED: &str = "rejected";

/// Memory claims live inside the three locked `memory.search` scopes; the
/// public scope enum cannot grow without a reviewed contract change.
pub const MEMORY_SCOPES: [&str; 3] = ["compiled_context", "session", "team"];

pub fn validate_memory_scope(value: &str) -> Result<()> {
    if MEMORY_SCOPES.contains(&value) {
        return Ok(());
    }
    bail!("unsupported memory scope: {value}")
}

pub fn validate_memory_state(value: &str) -> Result<()> {
    if [
        MEMORY_STATE_PROPOSED,
        MEMORY_STATE_VALIDATED,
        MEMORY_STATE_ACTIVE,
        MEMORY_STATE_SUPERSEDED,
        MEMORY_STATE_TOMBSTONED,
        MEMORY_STATE_REJECTED,
    ]
    .contains(&value)
    {
        return Ok(());
    }
    bail!("unsupported memory state: {value}")
}

/// Proposed→Validated→Active is the only forward path; rejection ends a claim
/// before activation, and an active claim ends only as superseded or
/// tombstoned. Superseded, tombstoned, and rejected are terminal.
pub fn validate_memory_transition(from: &str, to: &str) -> Result<()> {
    validate_memory_state(from)?;
    validate_memory_state(to)?;
    match (from, to) {
        (MEMORY_STATE_PROPOSED, MEMORY_STATE_VALIDATED)
        | (MEMORY_STATE_PROPOSED, MEMORY_STATE_REJECTED)
        | (MEMORY_STATE_VALIDATED, MEMORY_STATE_ACTIVE)
        | (MEMORY_STATE_VALIDATED, MEMORY_STATE_REJECTED)
        | (MEMORY_STATE_ACTIVE, MEMORY_STATE_SUPERSEDED)
        | (MEMORY_STATE_ACTIVE, MEMORY_STATE_TOMBSTONED) => Ok(()),
        _ => bail!("unsupported memory transition: {from} -> {to}"),
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

pub const LOCKED_PROFILE_SHA256: &str =
    "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc";
pub const LOCKED_CONTEXT_PACKET_SHA256: &str =
    "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f";
pub const PUBLIC_TOOL_CEILING: u16 = 12;
pub const REGISTRY_PAGE_LIMIT_DEFAULT: usize = 24;
pub const REGISTRY_PAGE_LIMIT_MAX: usize = 32;
pub const REGISTRY_JSON_FIELD_MAX_BYTES: usize = 2 * 1024;
pub const REGISTRY_TEXT_FIELD_MAX_BYTES: usize = 4 * 1024;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum WorkspaceTrustState {
    Untrusted,
    ReadOnly,
    Trusted,
}

impl WorkspaceTrustState {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Untrusted => "untrusted",
            Self::ReadOnly => "read_only",
            Self::Trusted => "trusted",
        }
    }

    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "untrusted" => Ok(Self::Untrusted),
            "read_only" => Ok(Self::ReadOnly),
            "trusted" => Ok(Self::Trusted),
            other => bail!("unsupported workspace trust state: {other}"),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum ClientKind {
    Cli,
    Desktop,
    Editor,
    Adapter,
}

impl ClientKind {
    pub const ALL: [Self; 4] = [Self::Cli, Self::Desktop, Self::Editor, Self::Adapter];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Cli => "cli",
            Self::Desktop => "desktop",
            Self::Editor => "editor",
            Self::Adapter => "adapter",
        }
    }

    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "cli" => Ok(Self::Cli),
            "desktop" => Ok(Self::Desktop),
            "editor" => Ok(Self::Editor),
            "adapter" => Ok(Self::Adapter),
            other => bail!("unsupported client kind: {other}"),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash, Default)]
#[serde(rename_all = "snake_case")]
pub enum ClientCompatibilityState {
    Verified,
    #[default]
    Unprobed,
    Unsupported,
}

impl ClientCompatibilityState {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Verified => "verified",
            Self::Unprobed => "unprobed",
            Self::Unsupported => "unsupported",
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum ClientAccessMode {
    ReadOnly,
    ReadWrite,
}

impl ClientAccessMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ReadOnly => "read_only",
            Self::ReadWrite => "read_write",
        }
    }

    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "read_only" => Ok(Self::ReadOnly),
            "read_write" => Ok(Self::ReadWrite),
            other => bail!("unsupported client access mode: {other}"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct WorkspacePayload {
    pub canonical_path: String,
    pub path_hash: String,
    pub display_name: String,
    pub trust_state: WorkspaceTrustState,
    pub profile_digest: String,
    pub context_digest: String,
    pub public_tool_ceiling: u16,
    pub production_claim_allowed: bool,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for WorkspacePayload {
    const KIND: EntityKind = EntityKind::Workspace;

    fn validate(&self) -> Result<()> {
        require(&self.canonical_path, "workspace canonical path")?;
        require(&self.path_hash, "workspace path hash")?;
        require(&self.display_name, "workspace display name")?;
        validate_hex_digest(&self.path_hash, "workspace path hash")?;
        if self.profile_digest != LOCKED_PROFILE_SHA256 {
            bail!("workspace profile digest does not match the locked contract");
        }
        if self.context_digest != LOCKED_CONTEXT_PACKET_SHA256 {
            bail!("workspace context digest does not match the locked contract");
        }
        if self.public_tool_ceiling != PUBLIC_TOOL_CEILING {
            bail!("workspace public tool ceiling must remain {PUBLIC_TOOL_CEILING}");
        }
        if self.production_claim_allowed {
            bail!("workspace registration cannot enable a production claim");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ClientRegistrationPayload {
    pub client_kind: ClientKind,
    pub instance_id: String,
    pub display_name: String,
    pub client_version: String,
    pub protocol_version: String,
    pub connection_state: String,
    #[serde(default)]
    pub compatibility_state: ClientCompatibilityState,
    #[serde(default)]
    pub write_capable: bool,
    pub last_seen_at_unix_ms: i64,
    #[serde(default)]
    pub capabilities: Value,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for ClientRegistrationPayload {
    const KIND: EntityKind = EntityKind::ClientRegistration;

    fn validate(&self) -> Result<()> {
        require(&self.instance_id, "client instance id")?;
        require(&self.display_name, "client display name")?;
        require(&self.client_version, "client version")?;
        require(&self.protocol_version, "client protocol version")?;
        require(&self.connection_state, "client connection state")?;
        if self.write_capable != (self.compatibility_state == ClientCompatibilityState::Verified) {
            bail!("client write capability must match verified compatibility state");
        }
        if self.last_seen_at_unix_ms < 0 {
            bail!("client last-seen time must be non-negative");
        }
        Ok(())
    }
}

/// Daemon-recorded evidence that a read-write binding was admitted through a
/// verified admission receipt rather than a verified internal client. Only the
/// daemon constructs this after MAC verification; it never crosses the IPC
/// boundary as caller input. The elevation lasts exactly until
/// `expires_at_unix_ms`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ClientBindingAdmission {
    pub receipt_matrix_sha256: String,
    pub probe_evidence_sha256: String,
    pub issued_at_unix_ms: i64,
    pub expires_at_unix_ms: i64,
    pub key_version: u32,
}

impl ClientBindingAdmission {
    pub fn admits_write_at(&self, now_unix_ms: i64) -> bool {
        self.issued_at_unix_ms <= now_unix_ms && self.expires_at_unix_ms > now_unix_ms
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ClientWorkspaceBindingPayload {
    pub client_id: Uuid,
    pub workspace_id: Uuid,
    pub access_mode: ClientAccessMode,
    pub binding_state: String,
    pub attached_at_unix_ms: i64,
    pub last_seen_at_unix_ms: i64,
    #[serde(default)]
    pub admission: Option<ClientBindingAdmission>,
    #[serde(default)]
    pub capabilities: Value,
    #[serde(default)]
    pub metadata: Value,
}

impl CanonicalPayload for ClientWorkspaceBindingPayload {
    const KIND: EntityKind = EntityKind::ClientWorkspaceBinding;

    fn validate(&self) -> Result<()> {
        require(&self.binding_state, "client workspace binding state")?;
        if self.attached_at_unix_ms < 0 || self.last_seen_at_unix_ms < 0 {
            bail!("client workspace binding times must be non-negative");
        }
        if let Some(admission) = &self.admission {
            validate_hex_digest(&admission.receipt_matrix_sha256, "binding admission matrix")?;
            validate_hex_digest(
                &admission.probe_evidence_sha256,
                "binding admission probe evidence",
            )?;
            if admission.issued_at_unix_ms < 0
                || admission.expires_at_unix_ms <= admission.issued_at_unix_ms
            {
                bail!("binding admission expiry must follow its issuance time");
            }
        }
        Ok(())
    }
}

fn validate_hex_digest(value: &str, label: &str) -> Result<()> {
    if value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        bail!("{label} must be a lowercase 64-character hexadecimal digest");
    }
    if value.bytes().any(|byte| byte.is_ascii_uppercase()) {
        bail!("{label} must use lowercase hexadecimal characters");
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceRegistryPage {
    pub items: Vec<CanonicalRecord<WorkspacePayload>>,
    pub next_cursor: Option<Uuid>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ClientRegistryPage {
    pub items: Vec<CanonicalRecord<ClientRegistrationPayload>>,
    pub next_cursor: Option<Uuid>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ClientWorkspaceBindingRegistryPage {
    pub items: Vec<CanonicalRecord<ClientWorkspaceBindingPayload>>,
    pub next_cursor: Option<Uuid>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SessionRegistryPage {
    pub items: Vec<CanonicalRecord<SessionPayload>>,
    pub next_cursor: Option<Uuid>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct MemoryClaimPage {
    pub items: Vec<CanonicalRecord<MemoryClaimPayload>>,
    pub next_cursor: Option<Uuid>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurnPage {
    pub items: Vec<CanonicalRecord<TurnPayload>>,
    pub next_ordinal: Option<u64>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct RegistrySnapshot {
    pub workspaces: WorkspaceRegistryPage,
    pub clients: ClientRegistryPage,
    pub bindings: ClientWorkspaceBindingRegistryPage,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceRegistrationResult {
    pub workspace: CanonicalRecord<WorkspacePayload>,
    pub downgraded_bindings: Vec<CanonicalRecord<ClientWorkspaceBindingPayload>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ClientRegistrationResult {
    pub client: CanonicalRecord<ClientRegistrationPayload>,
    pub bindings: Vec<CanonicalRecord<ClientWorkspaceBindingPayload>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct RegistryCascadeResult {
    pub entity_id: Uuid,
    pub binding_ids: Vec<Uuid>,
    pub tombstone: TombstoneRecord,
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
