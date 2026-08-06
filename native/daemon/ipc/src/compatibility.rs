use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use soleaux_state::{ClientCompatibilityState, ClientKind, PUBLIC_TOOL_CEILING};
use std::collections::{BTreeMap, BTreeSet};

pub const CLIENT_CAPABILITY_MATRIX_SCHEMA_VERSION: &str = "soleaux.client-capability-matrix/v1";
pub const CLIENT_CAPABILITY_PROBE_SCHEMA_VERSION: &str = "soleaux.client-capability-probe/v1";
pub const CLIENT_CAPABILITY_MATRIX_JSON: &str =
    include_str!("../../../contracts/client-capability-matrix-v1.json");

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CapabilityMatrix {
    schema_version: String,
    as_of_date: String,
    client_protocol_version: String,
    probe_schema_version: String,
    public_tool_ceiling: u16,
    production_claim_allowed: bool,
    platforms: Vec<PlatformMatrix>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct PlatformMatrix {
    id: String,
    task: String,
    display_name: String,
    client_kind: ClientKind,
    probe_mode: String,
    version_policy: String,
    versions: Vec<VersionMatrix>,
    capabilities: Value,
    sources: Vec<Value>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct VersionMatrix {
    version: String,
    release_channel: String,
    mutation_eligible: bool,
    #[serde(default)]
    required_binary_signals: Vec<String>,
    #[serde(default)]
    binary_commands: BTreeMap<String, Vec<String>>,
    #[serde(default)]
    linux_x64_asset: Option<Value>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ProbeEvidence {
    schema_version: String,
    platform: String,
    client_version: String,
    matrix_sha256: String,
    status: String,
    mutation_eligible: bool,
    passed_signals: Vec<String>,
    evidence_sha256: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct CompatibilityDecision {
    pub state: ClientCompatibilityState,
    pub write_capable: bool,
    pub platform: Option<String>,
    pub matrix_sha256: String,
    pub matrix_mutation_eligible: bool,
    pub version_policy: Option<String>,
    pub required_signals: Vec<String>,
    pub passed_signals: Vec<String>,
    pub reason: String,
}

pub fn client_capability_matrix_sha256() -> String {
    let digest = Sha256::digest(CLIENT_CAPABILITY_MATRIX_JSON.as_bytes());
    format!("{digest:x}")
}

pub fn validate_client_capability_matrix() -> Result<()> {
    let matrix = load_matrix()?;
    validate_matrix(&matrix)
}

pub fn client_capability_matrix_summary() -> Result<Value> {
    let matrix = load_matrix()?;
    validate_matrix(&matrix)?;
    let mut write_eligible = Vec::new();
    for platform in &matrix.platforms {
        for version in &platform.versions {
            if version.mutation_eligible {
                write_eligible.push(json!({
                    "platform":platform.id,
                    "version":version.version,
                }));
            }
        }
    }
    Ok(json!({
        "schemaVersion":matrix.schema_version,
        "asOfDate":matrix.as_of_date,
        "sha256":client_capability_matrix_sha256(),
        "platforms":matrix
            .platforms
            .iter()
            .map(|platform| platform.id.as_str())
            .collect::<Vec<_>>(),
        "writeEligible":write_eligible,
        "publicToolCeiling":matrix.public_tool_ceiling,
        "productionClaimAllowed":matrix.production_claim_allowed,
    }))
}

pub(crate) fn evaluate_client_compatibility(
    client_kind: ClientKind,
    client_version: &str,
    protocol_version: &str,
    expected_protocol_version: &str,
    capabilities: &Value,
    metadata: &Value,
) -> Result<CompatibilityDecision> {
    let matrix = load_matrix()?;
    validate_matrix(&matrix)?;
    let matrix_sha256 = client_capability_matrix_sha256();

    if client_kind == ClientKind::Cli
        && client_version == env!("CARGO_PKG_VERSION")
        && protocol_version == expected_protocol_version
    {
        return Ok(CompatibilityDecision {
            state: ClientCompatibilityState::Verified,
            write_capable: true,
            platform: Some("soleaux_cli".to_string()),
            matrix_sha256,
            matrix_mutation_eligible: true,
            version_policy: Some("exact_internal_version".to_string()),
            required_signals: Vec::new(),
            passed_signals: Vec::new(),
            reason: "exact Soleaux CLI and client protocol version".to_string(),
        });
    }

    if protocol_version != expected_protocol_version
        || matrix.client_protocol_version != expected_protocol_version
    {
        return Ok(read_only_decision(
            ClientCompatibilityState::Unsupported,
            None,
            matrix_sha256,
            false,
            None,
            Vec::new(),
            Vec::new(),
            "unsupported client protocol version",
        ));
    }

    let platform_id = metadata
        .get("platform")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty());
    let Some(platform_id) = platform_id else {
        return Ok(read_only_decision(
            ClientCompatibilityState::Unprobed,
            None,
            matrix_sha256,
            false,
            None,
            Vec::new(),
            Vec::new(),
            "client platform metadata is absent",
        ));
    };
    let Some(platform) = matrix
        .platforms
        .iter()
        .find(|candidate| candidate.id == platform_id)
    else {
        return Ok(read_only_decision(
            ClientCompatibilityState::Unprobed,
            Some(platform_id.to_string()),
            matrix_sha256,
            false,
            None,
            Vec::new(),
            Vec::new(),
            "client platform is not present in the capability matrix",
        ));
    };
    if platform.client_kind != client_kind {
        return Ok(read_only_decision(
            ClientCompatibilityState::Unsupported,
            Some(platform.id.clone()),
            matrix_sha256,
            false,
            Some(platform.version_policy.clone()),
            Vec::new(),
            Vec::new(),
            "client kind does not match the capability matrix",
        ));
    }
    let Some(version) = platform
        .versions
        .iter()
        .find(|candidate| candidate.version == client_version)
    else {
        return Ok(read_only_decision(
            ClientCompatibilityState::Unprobed,
            Some(platform.id.clone()),
            matrix_sha256,
            false,
            Some(platform.version_policy.clone()),
            Vec::new(),
            Vec::new(),
            "client version is not an exact matrix entry",
        ));
    };
    if !version.mutation_eligible {
        return Ok(read_only_decision(
            ClientCompatibilityState::Unprobed,
            Some(platform.id.clone()),
            matrix_sha256,
            false,
            Some(platform.version_policy.clone()),
            version.required_binary_signals.clone(),
            Vec::new(),
            "matrix entry is intentionally read-only",
        ));
    }

    let Some(probe_value) = capabilities.get("soleauxProbe") else {
        return Ok(read_only_decision(
            ClientCompatibilityState::Unprobed,
            Some(platform.id.clone()),
            matrix_sha256,
            true,
            Some(platform.version_policy.clone()),
            version.required_binary_signals.clone(),
            Vec::new(),
            "matrix-bound probe evidence is absent",
        ));
    };
    let probe: ProbeEvidence = match serde_json::from_value(probe_value.clone()) {
        Ok(probe) => probe,
        Err(error) => {
            return Ok(read_only_decision(
                ClientCompatibilityState::Unsupported,
                Some(platform.id.clone()),
                matrix_sha256,
                true,
                Some(platform.version_policy.clone()),
                version.required_binary_signals.clone(),
                Vec::new(),
                &format!("probe evidence is malformed: {error}"),
            ));
        }
    };
    let passed_signals = unique_sorted(&probe.passed_signals);
    let required_signals = unique_sorted(&version.required_binary_signals);
    let passed: BTreeSet<&str> = passed_signals.iter().map(String::as_str).collect();
    let missing = required_signals
        .iter()
        .filter(|signal| !passed.contains(signal.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    let valid = probe.schema_version == CLIENT_CAPABILITY_PROBE_SCHEMA_VERSION
        && probe.platform == platform.id
        && probe.client_version == client_version
        && probe.matrix_sha256 == matrix_sha256
        && probe.status == "pass"
        && probe.mutation_eligible
        && is_lower_hex_digest(&probe.evidence_sha256)
        && missing.is_empty();
    if !valid {
        return Ok(read_only_decision(
            ClientCompatibilityState::Unsupported,
            Some(platform.id.clone()),
            matrix_sha256,
            true,
            Some(platform.version_policy.clone()),
            required_signals,
            passed_signals,
            "probe evidence did not satisfy the exact matrix contract",
        ));
    }

    Ok(CompatibilityDecision {
        state: ClientCompatibilityState::Verified,
        write_capable: true,
        platform: Some(platform.id.clone()),
        matrix_sha256,
        matrix_mutation_eligible: true,
        version_policy: Some(platform.version_policy.clone()),
        required_signals,
        passed_signals,
        reason: "exact matrix-bound capability probe passed".to_string(),
    })
}

#[allow(clippy::too_many_arguments)]
fn read_only_decision(
    state: ClientCompatibilityState,
    platform: Option<String>,
    matrix_sha256: String,
    matrix_mutation_eligible: bool,
    version_policy: Option<String>,
    required_signals: Vec<String>,
    passed_signals: Vec<String>,
    reason: &str,
) -> CompatibilityDecision {
    CompatibilityDecision {
        state,
        write_capable: false,
        platform,
        matrix_sha256,
        matrix_mutation_eligible,
        version_policy,
        required_signals,
        passed_signals,
        reason: reason.to_string(),
    }
}

fn load_matrix() -> Result<CapabilityMatrix> {
    serde_json::from_str(CLIENT_CAPABILITY_MATRIX_JSON)
        .context("parsing the embedded client capability matrix")
}

fn validate_matrix(matrix: &CapabilityMatrix) -> Result<()> {
    if matrix.schema_version != CLIENT_CAPABILITY_MATRIX_SCHEMA_VERSION {
        bail!("unsupported client capability matrix schema");
    }
    if matrix.probe_schema_version != CLIENT_CAPABILITY_PROBE_SCHEMA_VERSION {
        bail!("unsupported client capability probe schema");
    }
    if matrix.client_protocol_version.trim().is_empty() {
        bail!("client capability matrix protocol version is empty");
    }
    if matrix.public_tool_ceiling != PUBLIC_TOOL_CEILING {
        bail!("client capability matrix changed the public tool ceiling");
    }
    if matrix.production_claim_allowed {
        bail!("client capability matrix cannot enable a production claim");
    }
    if matrix.platforms.is_empty() {
        bail!("client capability matrix has no platforms");
    }
    let mut platform_ids = BTreeSet::new();
    let mut write_eligible = 0usize;
    for platform in &matrix.platforms {
        if platform.id.trim().is_empty()
            || platform.task.trim().is_empty()
            || platform.display_name.trim().is_empty()
            || platform.probe_mode.trim().is_empty()
            || platform.version_policy.trim().is_empty()
        {
            bail!("client capability matrix platform metadata is incomplete");
        }
        if !platform_ids.insert(platform.id.as_str()) {
            bail!(
                "duplicate client capability matrix platform: {}",
                platform.id
            );
        }
        if !platform.capabilities.is_object() || platform.sources.is_empty() {
            bail!("client capability matrix platform lacks capabilities or sources");
        }
        if platform.versions.is_empty() {
            bail!("client capability matrix platform has no versions");
        }
        let mut versions = BTreeSet::new();
        for version in &platform.versions {
            if version.version.trim().is_empty() || version.release_channel.trim().is_empty() {
                bail!("client capability matrix version metadata is incomplete");
            }
            if !versions.insert(version.version.as_str()) {
                bail!(
                    "duplicate version {} for client platform {}",
                    version.version,
                    platform.id
                );
            }
            let required = unique_sorted(&version.required_binary_signals);
            if required.len() != version.required_binary_signals.len() {
                bail!("duplicate required probe signal for {}", platform.id);
            }
            for signal in &required {
                if !version.binary_commands.contains_key(signal)
                    && platform.probe_mode != "native_binary_conformance"
                {
                    bail!("missing binary command for {} signal {signal}", platform.id);
                }
            }
            if version.linux_x64_asset.is_some() && platform.id != "opencode" {
                bail!("only the OpenCode matrix entry may pin a Linux release asset");
            }
            if version.mutation_eligible {
                write_eligible += 1;
                if platform.id != "generic_mcp_host" || platform.version_policy != "exact" {
                    bail!("only the exact generic MCP host probe may be mutation eligible");
                }
            }
        }
    }
    if write_eligible != 1 {
        bail!("exactly one capability matrix entry must be mutation eligible");
    }
    Ok(())
}

fn unique_sorted(values: &[String]) -> Vec<String> {
    values
        .iter()
        .cloned()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn is_lower_hex_digest(value: &str) -> bool {
    value.len() == 64
        && value.bytes().all(|byte| byte.is_ascii_hexdigit())
        && value.bytes().all(|byte| !byte.is_ascii_uppercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    const PROTOCOL: &str = "soleaux.client/v1";

    fn valid_generic_probe() -> (Value, Value) {
        let required = vec![
            "initialize",
            "tools_list",
            "context_compile",
            "registry_registration",
            "read_write_binding",
            "tool_ceiling",
        ];
        (
            json!({
                "soleauxProbe":{
                    "schemaVersion":CLIENT_CAPABILITY_PROBE_SCHEMA_VERSION,
                    "platform":"generic_mcp_host",
                    "clientVersion":"mcp-2025-11-25",
                    "matrixSha256":client_capability_matrix_sha256(),
                    "status":"pass",
                    "mutationEligible":true,
                    "passedSignals":required,
                    "evidenceSha256":"a".repeat(64),
                }
            }),
            json!({"platform":"generic_mcp_host"}),
        )
    }

    #[test]
    fn embedded_matrix_is_valid_and_digest_is_stable_shape() {
        validate_client_capability_matrix().expect("valid matrix");
        assert!(is_lower_hex_digest(&client_capability_matrix_sha256()));
        let summary = client_capability_matrix_summary().expect("summary");
        assert_eq!(summary["publicToolCeiling"], 12);
        assert_eq!(summary["productionClaimAllowed"], false);
        assert_eq!(summary["platforms"].as_array().map(Vec::len), Some(6));
    }

    #[test]
    fn generic_mcp_host_requires_exact_matrix_bound_probe() {
        let (capabilities, metadata) = valid_generic_probe();
        let decision = evaluate_client_compatibility(
            ClientKind::Adapter,
            "mcp-2025-11-25",
            PROTOCOL,
            PROTOCOL,
            &capabilities,
            &metadata,
        )
        .expect("decision");
        assert_eq!(decision.state, ClientCompatibilityState::Verified);
        assert!(decision.write_capable);

        let mut invalid = capabilities;
        invalid["soleauxProbe"]["matrixSha256"] = Value::String("b".repeat(64));
        let decision = evaluate_client_compatibility(
            ClientKind::Adapter,
            "mcp-2025-11-25",
            PROTOCOL,
            PROTOCOL,
            &invalid,
            &metadata,
        )
        .expect("invalid decision");
        assert_eq!(decision.state, ClientCompatibilityState::Unsupported);
        assert!(!decision.write_capable);
    }

    #[test]
    fn vendor_clients_and_unknown_versions_remain_read_only() {
        let forged = json!({
            "soleauxProbe":{
                "schemaVersion":CLIENT_CAPABILITY_PROBE_SCHEMA_VERSION,
                "platform":"claude_code",
                "clientVersion":"2.1.223",
                "matrixSha256":client_capability_matrix_sha256(),
                "status":"pass",
                "mutationEligible":true,
                "passedSignals":["version","help","mcp"],
                "evidenceSha256":"c".repeat(64),
            }
        });
        let metadata = json!({"platform":"claude_code"});
        let decision = evaluate_client_compatibility(
            ClientKind::Adapter,
            "2.1.223",
            PROTOCOL,
            PROTOCOL,
            &forged,
            &metadata,
        )
        .expect("Claude decision");
        assert_eq!(decision.state, ClientCompatibilityState::Unprobed);
        assert!(!decision.write_capable);

        let unknown = evaluate_client_compatibility(
            ClientKind::Adapter,
            "999.0.0",
            PROTOCOL,
            PROTOCOL,
            &json!({}),
            &metadata,
        )
        .expect("unknown decision");
        assert_eq!(unknown.state, ClientCompatibilityState::Unprobed);
        assert!(!unknown.write_capable);
    }

    #[test]
    fn platform_kind_mismatch_is_unsupported() {
        let decision = evaluate_client_compatibility(
            ClientKind::Desktop,
            "2.1.223",
            PROTOCOL,
            PROTOCOL,
            &json!({}),
            &json!({"platform":"claude_code"}),
        )
        .expect("decision");
        assert_eq!(decision.state, ClientCompatibilityState::Unsupported);
        assert!(!decision.write_capable);
    }
}
