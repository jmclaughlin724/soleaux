//! Binding public MCP and context-packet contract.
//!
//! The profile is loaded from the closed Phase 0 manifest. Runtime code may
//! substitute one optional native tool for one canonical slot, but it may not
//! append, alias, reorder, or exceed the twelve-slot contract.

use anyhow::{Context, Result, bail};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::OnceLock,
};

pub const PRODUCT_VERSION: &str = "0.4.0-dev.5";
pub const PRODUCTION_CLAIM_ALLOWED: bool = false;
pub const HARD_CEILING: usize = 12;
pub const PROFILE_SCHEMA_VERSION: &str = "soleaux.mcp.profile/v2";
pub const RESPONSE_ENVELOPE_SCHEMA_VERSION: &str = "soleaux.mcp/v2";
pub const CONTEXT_PACKET_SCHEMA_VERSION: &str = "soleaux.context/v2";
pub const PROFILE_MANIFEST_SHA256: &str =
    "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc";
pub const CONTEXT_SCHEMA_SHA256: &str =
    "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f";

pub const CANONICAL_TOOL_NAMES: [&str; HARD_CEILING] = [
    "context.compile",
    "code.search",
    "memory.search",
    "get_symbols",
    "registry.list",
    "registry.read",
    "repo_info",
    "navigate",
    "inspect",
    "preview",
    "edit",
    "restart_lsp",
];

pub const OPTIONAL_TOOL_NAMES: [&str; 3] = [
    "parse_and_validate_postgres_sql",
    "turborepo.packages",
    "next.get_routes",
];

pub const UNIFIED_PROFILE_DOCUMENT: &str = include_str!("../../../UNIFIED-MCP-PROFILE.md");
pub const CONTEXT_PACKET_DOCUMENT: &str = include_str!("../../../CONTEXT-PACKET-V2.md");
pub const PROFILE_MANIFEST_JSON: &str =
    include_str!("../../../contracts/unified-mcp-profile-v2.json");
pub const CONTEXT_PACKET_SCHEMA_JSON: &str =
    include_str!("../../../contracts/context-packet-v2.schema.json");
pub const PHASE0_IDENTITY_JSON: &str = include_str!("../../../contracts/phase0-identity.json");

pub type SubstitutionMap = BTreeMap<String, String>;

pub fn manifest() -> &'static Value {
    static MANIFEST: OnceLock<Value> = OnceLock::new();
    MANIFEST.get_or_init(|| {
        serde_json::from_str(PROFILE_MANIFEST_JSON)
            .expect("binding unified MCP profile JSON must be valid")
    })
}

pub fn canonical_definition(name: &str) -> Result<&'static Value> {
    manifest()
        .get("tools")
        .and_then(Value::as_array)
        .and_then(|tools| {
            tools
                .iter()
                .find(|definition| definition.get("name").and_then(Value::as_str) == Some(name))
        })
        .with_context(|| format!("canonical tool definition is missing: {name}"))
}

pub fn optional_definition(name: &str) -> Result<&'static Value> {
    manifest()
        .get("optionalDefinitions")
        .and_then(Value::as_array)
        .and_then(|tools| {
            tools
                .iter()
                .find(|definition| definition.get("name").and_then(Value::as_str) == Some(name))
        })
        .with_context(|| format!("optional tool definition is missing: {name}"))
}

pub fn active_tool_names(substitutions: &SubstitutionMap) -> Result<Vec<String>> {
    validate_substitutions(substitutions)?;
    Ok(CANONICAL_TOOL_NAMES
        .iter()
        .map(|name| {
            substitutions
                .get(*name)
                .cloned()
                .unwrap_or_else(|| (*name).to_string())
        })
        .collect())
}

pub fn validate_substitutions(substitutions: &SubstitutionMap) -> Result<()> {
    if substitutions.len() > OPTIONAL_TOOL_NAMES.len() {
        bail!("public MCP profile has more optional substitutions than candidates");
    }
    let canonical = CANONICAL_TOOL_NAMES
        .iter()
        .copied()
        .collect::<BTreeSet<_>>();
    let optional = OPTIONAL_TOOL_NAMES.iter().copied().collect::<BTreeSet<_>>();
    let mut selected_optional = BTreeSet::new();
    for (replace, with) in substitutions {
        if !canonical.contains(replace.as_str()) {
            bail!("optional substitution targets an unknown canonical slot: {replace}");
        }
        if !optional.contains(with.as_str()) {
            bail!("optional substitution selects an unknown candidate: {with}");
        }
        if !selected_optional.insert(with.as_str()) {
            bail!("optional substitution candidate is selected more than once: {with}");
        }
    }
    let active = CANONICAL_TOOL_NAMES
        .iter()
        .map(|name| substitutions.get(*name).map_or(*name, String::as_str))
        .collect::<Vec<_>>();
    if active.len() != HARD_CEILING {
        bail!("public MCP profile must contain exactly {HARD_CEILING} active slots");
    }
    if active.iter().copied().collect::<BTreeSet<_>>().len() != HARD_CEILING {
        bail!("public MCP profile contains a duplicate active tool");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_profile_is_exactly_twelve_unique_ordered_slots() {
        assert_eq!(CANONICAL_TOOL_NAMES.len(), HARD_CEILING);
        assert_eq!(
            CANONICAL_TOOL_NAMES
                .iter()
                .copied()
                .collect::<BTreeSet<_>>()
                .len(),
            HARD_CEILING
        );
        assert_eq!(CANONICAL_TOOL_NAMES[0], "context.compile");
        assert_eq!(CANONICAL_TOOL_NAMES[11], "restart_lsp");
    }

    #[test]
    fn optional_candidates_are_unique_and_replace_without_expanding() {
        assert_eq!(OPTIONAL_TOOL_NAMES.len(), 3);
        let mut substitutions = SubstitutionMap::new();
        substitutions.insert("restart_lsp".to_string(), "turborepo.packages".to_string());
        let active = active_tool_names(&substitutions).expect("valid substitution");
        assert_eq!(active.len(), HARD_CEILING);
        assert_eq!(active[11], "turborepo.packages");
        assert!(!active.iter().any(|name| name == "restart_lsp"));
    }

    #[test]
    fn invalid_substitutions_fail_closed() {
        let mut duplicate = SubstitutionMap::new();
        duplicate.insert("restart_lsp".to_string(), "next.get_routes".to_string());
        duplicate.insert("edit".to_string(), "next.get_routes".to_string());
        assert!(validate_substitutions(&duplicate).is_err());

        let mut unknown = SubstitutionMap::new();
        unknown.insert("unknown".to_string(), "next.get_routes".to_string());
        assert!(validate_substitutions(&unknown).is_err());
    }

    #[test]
    fn embedded_contracts_lock_version_digests_and_native_guarantees() {
        let profile = manifest();
        let context: Value =
            serde_json::from_str(CONTEXT_PACKET_SCHEMA_JSON).expect("context schema JSON");
        let identity: Value = serde_json::from_str(PHASE0_IDENTITY_JSON).expect("identity JSON");

        assert_eq!(profile["schemaVersion"], PROFILE_SCHEMA_VERSION);
        assert_eq!(profile["productVersion"], PRODUCT_VERSION);
        assert_eq!(profile["hardCeiling"], HARD_CEILING);
        assert_eq!(profile["productionClaimAllowed"], false);
        assert_eq!(
            profile["defaultProfile"],
            serde_json::to_value(CANONICAL_TOOL_NAMES).expect("canonical names")
        );
        assert_eq!(
            profile["optionalTools"],
            serde_json::to_value(OPTIONAL_TOOL_NAMES).expect("optional names")
        );
        assert_eq!(
            context["properties"]["schema_version"]["const"],
            CONTEXT_PACKET_SCHEMA_VERSION
        );
        assert_eq!(
            context["properties"]["native"]["properties"]["selected_parsers_native"]["const"],
            true
        );
        assert_eq!(
            context["properties"]["native"]["properties"]["selected_lsps_native"]["const"],
            true
        );
        assert_eq!(identity["profileManifestSha256"], PROFILE_MANIFEST_SHA256);
        assert_eq!(identity["contextSchemaSha256"], CONTEXT_SCHEMA_SHA256);
        assert_eq!(identity["productionClaimAllowed"], PRODUCTION_CLAIM_ALLOWED);
        assert!(UNIFIED_PROFILE_DOCUMENT.contains(PROFILE_MANIFEST_SHA256));
        assert!(CONTEXT_PACKET_DOCUMENT.contains(CONTEXT_SCHEMA_SHA256));
    }
}
