use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::permissions::is_path_safe_in_workspace;

/// Error types for transactional patch operations.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PatchError {
    WorkspaceEscape(String),
    FileNotFound(String),
    HashMismatch { expected: String, actual: String },
    TargetContentNotFound(String),
    ConcurrentModification(String),
    InvalidPatchFormat(String),
    ValidationFailed(String),
    IoError(String),
}

impl std::fmt::Display for PatchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::WorkspaceEscape(p) => write!(
                f,
                "Security Error: patch target '{p}' escapes workspace boundary"
            ),
            Self::FileNotFound(p) => write!(f, "Patch Error: target file '{p}' not found"),
            Self::HashMismatch { expected, actual } => write!(
                f,
                "Patch Error: file hash mismatch (expected {expected}, actual {actual})"
            ),
            Self::TargetContentNotFound(c) => {
                write!(f, "Patch Error: target content not found in file: '{c}'")
            }
            Self::ConcurrentModification(p) => {
                write!(f, "Patch Error: file '{p}' modified concurrently")
            }
            Self::InvalidPatchFormat(m) => write!(f, "Patch Error: invalid patch format: {m}"),
            Self::ValidationFailed(m) => write!(f, "Patch Error: validation failed: {m}"),
            Self::IoError(e) => write!(f, "Patch Error: IO failure: {e}"),
        }
    }
}

impl std::error::Error for PatchError {}

/// Typed patch hunk specification referencing target content and replacement.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PatchHunk {
    pub start_line: usize,
    pub end_line: usize,
    pub target_content: String,
    pub replacement_content: String,
}

/// Structured patch payload referencing expected file hash.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StructuredPatch {
    pub target_file: String,
    pub expected_sha256: Option<String>,
    pub hunks: Vec<PatchHunk>,
}

/// Record of an applied patch transaction for instant rollback.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RollbackRecord {
    pub target_file: String,
    pub pre_patch_content: String,
    pub pre_patch_sha256: String,
    pub timestamp: String,
}

/// Validation report generated prior to workspace mutation.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PatchValidationReport {
    pub target_file: String,
    pub is_valid: bool,
    pub pre_patch_sha256: String,
    pub post_patch_sha256: String,
    pub hunk_count: usize,
    pub validation_messages: Vec<String>,
}

/// Compute SHA-256 hash of text content.
#[must_use]
pub fn compute_sha256(content: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(content.as_bytes());
    format!("{:x}", hasher.finalize())
}

/// Validate and apply structured patch transactionally with atomic replacement.
pub fn validate_and_apply_patch(
    patch: &StructuredPatch,
    workspace_root: &Path,
) -> Result<(PatchValidationReport, RollbackRecord), PatchError> {
    let target_path = PathBuf::from(&patch.target_file);
    let resolved_path = if target_path.is_absolute() {
        target_path
    } else {
        workspace_root.join(&target_path)
    };

    if !is_path_safe_in_workspace(&resolved_path, workspace_root) {
        return Err(PatchError::WorkspaceEscape(patch.target_file.clone()));
    }

    if !resolved_path.exists() {
        return Err(PatchError::FileNotFound(patch.target_file.clone()));
    }

    let original_content =
        fs::read_to_string(&resolved_path).map_err(|e| PatchError::IoError(e.to_string()))?;
    let current_sha256 = compute_sha256(&original_content);

    if let Some(expected_hash) = &patch.expected_sha256 {
        if !expected_hash.is_empty() && expected_hash != &current_sha256 {
            return Err(PatchError::HashMismatch {
                expected: expected_hash.clone(),
                actual: current_sha256.clone(),
            });
        }
    }

    let mut modified_content = original_content.clone();
    for hunk in &patch.hunks {
        if !modified_content.contains(&hunk.target_content) {
            return Err(PatchError::TargetContentNotFound(
                hunk.target_content.clone(),
            ));
        }
        modified_content =
            modified_content.replacen(&hunk.target_content, &hunk.replacement_content, 1);
    }

    let post_patch_sha256 = compute_sha256(&modified_content);

    // Atomic write via staging file in same directory
    let temp_staging_path = resolved_path.with_extension(format!("tmp_{}", std::process::id()));
    {
        let mut staging_file =
            File::create(&temp_staging_path).map_err(|e| PatchError::IoError(e.to_string()))?;
        staging_file
            .write_all(modified_content.as_bytes())
            .map_err(|e| PatchError::IoError(e.to_string()))?;
        staging_file
            .sync_all()
            .map_err(|e| PatchError::IoError(e.to_string()))?;
    }

    // Atomic replacement
    fs::rename(&temp_staging_path, &resolved_path)
        .map_err(|e| PatchError::IoError(e.to_string()))?;

    let report = PatchValidationReport {
        target_file: patch.target_file.clone(),
        is_valid: true,
        pre_patch_sha256: current_sha256.clone(),
        post_patch_sha256,
        hunk_count: patch.hunks.len(),
        validation_messages: vec!["Patch validated and applied atomically".to_string()],
    };

    let rollback = RollbackRecord {
        target_file: patch.target_file.clone(),
        pre_patch_content: original_content,
        pre_patch_sha256: current_sha256,
        timestamp: "2026-07-24T00:00:00Z".to_string(),
    };

    Ok((report, rollback))
}

/// Rollback a committed patch transaction using its `RollbackRecord`.
pub fn rollback_transaction(
    rollback: &RollbackRecord,
    workspace_root: &Path,
) -> Result<(), PatchError> {
    let target_path = PathBuf::from(&rollback.target_file);
    let resolved_path = if target_path.is_absolute() {
        target_path
    } else {
        workspace_root.join(&target_path)
    };

    if !is_path_safe_in_workspace(&resolved_path, workspace_root) {
        return Err(PatchError::WorkspaceEscape(rollback.target_file.clone()));
    }

    // Atomic restoration
    let temp_staging_path = resolved_path.with_extension(format!("tmp_rb_{}", std::process::id()));
    {
        let mut staging_file =
            File::create(&temp_staging_path).map_err(|e| PatchError::IoError(e.to_string()))?;
        staging_file
            .write_all(rollback.pre_patch_content.as_bytes())
            .map_err(|e| PatchError::IoError(e.to_string()))?;
        staging_file
            .sync_all()
            .map_err(|e| PatchError::IoError(e.to_string()))?;
    }

    fs::rename(&temp_staging_path, &resolved_path)
        .map_err(|e| PatchError::IoError(e.to_string()))?;

    Ok(())
}
