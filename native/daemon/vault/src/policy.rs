use anyhow::{Result, bail};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[serde(rename_all = "snake_case")]
pub enum Capability {
    ReadContext,
    ReadArtifact,
    WriteArtifact,
    DeleteArtifact,
    ExportArtifact,
    ManageVaultKeys,
    ReadMemory,
    WriteMemory,
    ReadSession,
    WriteSession,
    CreateHandoff,
    AcceptHandoff,
    Materialize,
    InvokeTool,
    RunCommand,
    ManageService,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[serde(rename_all = "snake_case")]
pub enum RiskLevel {
    ReadOnly,
    LocalWrite,
    Process,
    Network,
    Privileged,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[serde(rename_all = "snake_case")]
pub enum SensitivityLevel {
    Public,
    Internal,
    Confidential,
    Secret,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct CapabilityGrant {
    pub id: Uuid,
    pub subject: String,
    #[serde(default)]
    pub workspace_id: Option<Uuid>,
    pub capabilities: BTreeSet<Capability>,
    #[serde(default)]
    pub resource_prefixes: Vec<String>,
    pub max_risk: RiskLevel,
    pub max_sensitivity: SensitivityLevel,
    #[serde(default)]
    pub expires_at_unix_ms: Option<i64>,
    pub requires_approval: bool,
    pub delegable: bool,
    #[serde(default)]
    pub parent_grant_id: Option<Uuid>,
    #[serde(default)]
    pub labels: BTreeSet<String>,
}

impl CapabilityGrant {
    pub fn validate(&self) -> Result<()> {
        if self.subject.trim().is_empty() {
            bail!("capability subject must be non-empty");
        }
        if self.capabilities.is_empty() {
            bail!("capability grant must contain at least one capability");
        }
        for prefix in &self.resource_prefixes {
            validate_resource_prefix(prefix)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct CapabilityRequest {
    pub subject: String,
    pub workspace_id: Uuid,
    pub capability: Capability,
    pub resource: String,
    pub risk: RiskLevel,
    pub sensitivity: SensitivityLevel,
    pub now_unix_ms: i64,
    #[serde(default)]
    pub approval: Option<ApprovalEvidence>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ApprovalEvidence {
    pub id: Uuid,
    pub grant_id: Uuid,
    pub subject: String,
    pub workspace_id: Uuid,
    pub capability: Capability,
    pub resource: String,
    pub approved_at_unix_ms: i64,
    pub expires_at_unix_ms: i64,
    pub approver: String,
    pub request_hash: String,
}

impl ApprovalEvidence {
    pub fn for_request(
        grant_id: Uuid,
        request: &CapabilityRequest,
        approver: impl Into<String>,
        approved_at_unix_ms: i64,
        expires_at_unix_ms: i64,
    ) -> Result<Self> {
        let approver = approver.into();
        if approver.trim().is_empty() {
            bail!("approval approver must be non-empty");
        }
        if expires_at_unix_ms <= approved_at_unix_ms {
            bail!("approval expiration must follow approval time");
        }
        Ok(Self {
            id: Uuid::now_v7(),
            grant_id,
            subject: request.subject.clone(),
            workspace_id: request.workspace_id,
            capability: request.capability,
            resource: request.resource.clone(),
            approved_at_unix_ms,
            expires_at_unix_ms,
            approver,
            request_hash: request_hash(request),
        })
    }

    fn validates(&self, grant: &CapabilityGrant, request: &CapabilityRequest) -> bool {
        self.grant_id == grant.id
            && self.subject == request.subject
            && self.workspace_id == request.workspace_id
            && self.capability == request.capability
            && self.resource == request.resource
            && self.approved_at_unix_ms <= request.now_unix_ms
            && self.expires_at_unix_ms > request.now_unix_ms
            && self.request_hash == request_hash(request)
            && !self.approver.trim().is_empty()
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PolicyEffect {
    Allow,
    Deny,
    ApprovalRequired,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PolicyDecision {
    pub effect: PolicyEffect,
    pub matching_grant_ids: Vec<Uuid>,
    pub reasons: Vec<String>,
    #[serde(default)]
    pub approval_id: Option<Uuid>,
}

impl PolicyDecision {
    pub fn allowed(&self) -> bool {
        self.effect == PolicyEffect::Allow
    }
}

#[derive(Debug, Clone, Default)]
pub struct PolicyEngine {
    grants: Vec<CapabilityGrant>,
}

impl PolicyEngine {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn grants(&self) -> &[CapabilityGrant] {
        &self.grants
    }

    pub fn add_grant(&mut self, grant: CapabilityGrant) -> Result<()> {
        grant.validate()?;
        if self.grants.iter().any(|existing| existing.id == grant.id) {
            bail!("capability grant id already exists");
        }
        if let Some(parent_id) = grant.parent_grant_id {
            let parent = self
                .grants
                .iter()
                .find(|candidate| candidate.id == parent_id)
                .ok_or_else(|| anyhow::anyhow!("parent capability grant does not exist"))?;
            validate_attenuation(parent, &grant)?;
        }
        self.grants.push(grant);
        self.grants.sort_by_key(|candidate| candidate.id);
        Ok(())
    }

    pub fn revoke_grant(&mut self, grant_id: Uuid) -> bool {
        let before = self.grants.len();
        self.grants.retain(|grant| {
            grant.id != grant_id && grant.parent_grant_id != Some(grant_id)
        });
        self.grants.len() != before
    }

    pub fn evaluate(&self, request: &CapabilityRequest) -> PolicyDecision {
        if request.subject.trim().is_empty() || validate_resource(&request.resource).is_err() {
            return PolicyDecision {
                effect: PolicyEffect::Deny,
                matching_grant_ids: Vec::new(),
                reasons: vec!["invalid capability request".to_string()],
                approval_id: None,
            };
        }

        let mut matching = Vec::new();
        let mut approval_candidates = Vec::new();
        let mut reasons = Vec::new();
        for grant in &self.grants {
            if grant.subject != request.subject {
                continue;
            }
            if grant
                .workspace_id
                .is_some_and(|workspace| workspace != request.workspace_id)
            {
                continue;
            }
            if !grant.capabilities.contains(&request.capability) {
                continue;
            }
            if grant
                .expires_at_unix_ms
                .is_some_and(|expires| expires <= request.now_unix_ms)
            {
                reasons.push(format!("grant {} is expired", grant.id));
                continue;
            }
            if request.risk > grant.max_risk {
                reasons.push(format!("grant {} does not permit the requested risk", grant.id));
                continue;
            }
            if request.sensitivity > grant.max_sensitivity {
                reasons.push(format!(
                    "grant {} does not permit the requested sensitivity",
                    grant.id
                ));
                continue;
            }
            if !resource_matches(&grant.resource_prefixes, &request.resource) {
                reasons.push(format!("grant {} does not cover the resource", grant.id));
                continue;
            }
            matching.push(grant.id);
            if !grant.requires_approval {
                return PolicyDecision {
                    effect: PolicyEffect::Allow,
                    matching_grant_ids: matching,
                    reasons: vec!["an explicit capability grant permits the request".to_string()],
                    approval_id: None,
                };
            }
            if let Some(approval) = request
                .approval
                .as_ref()
                .filter(|approval| approval.validates(grant, request))
            {
                return PolicyDecision {
                    effect: PolicyEffect::Allow,
                    matching_grant_ids: matching,
                    reasons: vec!["an explicit grant and matching approval permit the request".to_string()],
                    approval_id: Some(approval.id),
                };
            }
            approval_candidates.push(grant.id);
        }

        if !approval_candidates.is_empty() {
            return PolicyDecision {
                effect: PolicyEffect::ApprovalRequired,
                matching_grant_ids: approval_candidates,
                reasons: vec!["a matching grant requires a bounded approval".to_string()],
                approval_id: None,
            };
        }
        if reasons.is_empty() {
            reasons.push("no explicit capability grant permits the request".to_string());
        }
        PolicyDecision {
            effect: PolicyEffect::Deny,
            matching_grant_ids: matching,
            reasons,
            approval_id: None,
        }
    }
}

fn validate_attenuation(parent: &CapabilityGrant, child: &CapabilityGrant) -> Result<()> {
    if !parent.delegable {
        bail!("parent capability grant is not delegable");
    }
    if parent.subject != child.subject {
        bail!("delegated capability subject must match its parent");
    }
    if parent.workspace_id.is_some() && child.workspace_id != parent.workspace_id {
        bail!("delegated workspace scope must not broaden its parent");
    }
    if !child.capabilities.is_subset(&parent.capabilities) {
        bail!("delegated capabilities must be a subset of their parent");
    }
    if child.max_risk > parent.max_risk || child.max_sensitivity > parent.max_sensitivity {
        bail!("delegated risk and sensitivity must not broaden their parent");
    }
    if parent.requires_approval && !child.requires_approval {
        bail!("delegation cannot remove a parent approval requirement");
    }
    if parent
        .expires_at_unix_ms
        .is_some_and(|parent_expiry| child.expires_at_unix_ms.is_none_or(|child_expiry| child_expiry > parent_expiry))
    {
        bail!("delegated expiration must not exceed its parent");
    }
    if !prefixes_are_attenuated(&parent.resource_prefixes, &child.resource_prefixes) {
        bail!("delegated resource prefixes must not broaden their parent");
    }
    Ok(())
}

fn prefixes_are_attenuated(parent: &[String], child: &[String]) -> bool {
    if parent.is_empty() {
        return true;
    }
    if child.is_empty() {
        return false;
    }
    child.iter().all(|candidate| {
        parent
            .iter()
            .any(|prefix| resource_has_prefix(candidate, prefix))
    })
}

fn resource_matches(prefixes: &[String], resource: &str) -> bool {
    prefixes.is_empty()
        || prefixes
            .iter()
            .any(|prefix| resource_has_prefix(resource, prefix))
}

fn resource_has_prefix(resource: &str, prefix: &str) -> bool {
    resource == prefix
        || resource
            .strip_prefix(prefix)
            .is_some_and(|suffix| suffix.starts_with('/') || suffix.starts_with(':'))
}

fn validate_resource_prefix(value: &str) -> Result<()> {
    validate_resource(value)?;
    if value.ends_with('/') || value.ends_with(':') {
        bail!("capability resource prefix must not end with a separator");
    }
    Ok(())
}

fn validate_resource(value: &str) -> Result<()> {
    if value.trim().is_empty() || value.len() > 4096 {
        bail!("capability resource is invalid");
    }
    if value.contains("..") || value.contains('\0') || value.contains('\\') {
        bail!("capability resource contains a forbidden traversal sequence");
    }
    Ok(())
}

fn request_hash(request: &CapabilityRequest) -> String {
    let value = serde_json::json!({
        "schemaVersion":"soleaux.capability-request/v1",
        "subject":request.subject,
        "workspaceId":request.workspace_id,
        "capability":request.capability,
        "resource":request.resource,
        "risk":request.risk,
        "sensitivity":request.sensitivity,
    });
    blake3::hash(
        &serde_json::to_vec(&value).expect("capability request serialization is infallible"),
    )
    .to_hex()
    .to_string()
}
