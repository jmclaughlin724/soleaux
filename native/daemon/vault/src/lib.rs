//! Encrypted content-addressed artifact storage and capability policy for Soleaux.
//!
//! Production key material is protected by the operating-system credential service. Artifact
//! encryption is workspace-separated and authenticated, while policy evaluation is explicit,
//! deny-by-default, risk-bounded, sensitivity-bounded, and approval-aware. Decryption and policy
//! decisions fail closed when workspace identity, key version, integrity, or approval evidence
//! cannot be verified.

mod keyring;
mod policy;
mod vault;

pub use keyring::{
    FileKeyStore, KeyRing, KeyStore, MasterKey, MemoryKeyStore, OsKeyStore, load_or_create,
};
pub use policy::{
    ApprovalEvidence, Capability, CapabilityGrant, CapabilityRequest, PolicyDecision, PolicyEffect,
    PolicyEngine, RiskLevel, SensitivityLevel,
};
pub use vault::{
    ArtifactContent, ArtifactDescriptor, ArtifactHeader, ArtifactSensitivity, ArtifactVault,
    VaultRotationReport, VaultVerificationReport,
};

#[cfg(test)]
mod tests;
