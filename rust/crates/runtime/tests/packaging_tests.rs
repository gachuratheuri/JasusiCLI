/// Packaging and release-candidate integration tests (Phase 7, finding F14).
///
/// These tests form the authoritative machine-verifiable contract for repository
/// packaging integrity. They must pass on every platform (Windows, Linux, macOS)
/// without network access. If any assertion fails, the packaging release gate
/// (G7) must be blocked until the root cause is resolved.
use std::fs;
use std::path::{Path, PathBuf};

/// Canonical version string — the single source of truth for the entire project.
/// Update *this constant* and then run `cargo test` to verify all packaging
/// surfaces are in sync.
const CANONICAL_VERSION: &str = "3.0.0";

fn repo_root() -> PathBuf {
    // CARGO_MANIFEST_DIR is the `runtime` crate directory.
    // Three levels up reaches the repository root.
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .join("../../..")
        .canonicalize()
        .expect("Failed to canonicalize repository root path")
}

// ---------------------------------------------------------------------------
// T1 — MIT License presence and format (finding F14)
// ---------------------------------------------------------------------------

#[test]
fn test_mit_license_presence_and_format() {
    let license_path = repo_root().join("LICENSE");
    assert!(
        license_path.exists(),
        "Root MIT LICENSE file must exist at repository root: {}",
        license_path.display()
    );

    let content = fs::read_to_string(&license_path).expect("Failed to read LICENSE file");
    assert!(
        content.contains("MIT License"),
        "LICENSE must contain the 'MIT License' header"
    );
    assert!(
        content.contains("Copyright (c) 2026"),
        "LICENSE must contain a copyright notice with year 2026"
    );
    assert!(
        content.contains("Permission is hereby granted, free of charge"),
        "LICENSE must contain the standard MIT grant clause"
    );
}

// ---------------------------------------------------------------------------
// T2 — Rust workspace version matches CANONICAL_VERSION (finding F14)
// ---------------------------------------------------------------------------

#[test]
fn test_cargo_workspace_version_matches_canonical() {
    let cargo_toml_path = repo_root().join("rust").join("Cargo.toml");
    assert!(
        cargo_toml_path.exists(),
        "Rust workspace Cargo.toml must exist"
    );

    let content = fs::read_to_string(&cargo_toml_path).expect("Failed to read rust/Cargo.toml");

    // Look for the `version = "<CANONICAL_VERSION>"` assignment under [workspace.package].
    let expected = format!("version = \"{CANONICAL_VERSION}\"");
    assert!(
        content.contains(&expected),
        "rust/Cargo.toml [workspace.package] version must be \"{CANONICAL_VERSION}\"; \
         update to match CANONICAL_VERSION.\n\
         Expected substring: {expected}\n\
         Actual Cargo.toml content snippet:\n{}",
        &content[..content.len().min(512)]
    );
}

// ---------------------------------------------------------------------------
// T3 — Python pyproject.toml version matches CANONICAL_VERSION (finding F14)
// ---------------------------------------------------------------------------

#[test]
fn test_pyproject_version_matches_canonical() {
    let pyproject_path = repo_root().join("pyproject.toml");
    assert!(
        pyproject_path.exists(),
        "pyproject.toml must exist at repository root"
    );

    let content = fs::read_to_string(&pyproject_path).expect("Failed to read pyproject.toml");

    let expected = format!("version = \"{CANONICAL_VERSION}\"");
    assert!(
        content.contains(&expected),
        "pyproject.toml [project] version must be \"{CANONICAL_VERSION}\"; \
         update to match CANONICAL_VERSION.\n\
         Expected substring: {expected}\n\
         Actual pyproject.toml content snippet:\n{}",
        &content[..content.len().min(512)]
    );
}

// ---------------------------------------------------------------------------
// T4 — No unredacted credentials in tracked configuration files (F14 / secret scan)
// ---------------------------------------------------------------------------

#[test]
fn test_no_unredacted_credentials_in_tracked_configs() {
    // Relative paths from the Rust workspace root (`rust/`).
    let rust_workspace = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .join("rust");

    let candidate_configs = [
        "Cargo.toml",
        "crates/runtime/Cargo.toml",
        "crates/jasusi-core/Cargo.toml",
        "crates/tools/Cargo.toml",
        "crates/telemetry/Cargo.toml",
    ];

    // Patterns that are unambiguous indicators of real credentials.
    let banned_patterns = [
        "sk-ant-api", // Anthropic API key prefix
        "ghp_",       // GitHub personal access token
        "xoxb-",      // Slack bot token
        "AIzaSy",     // Google AI Studio key prefix
        "sk-proj-",   // OpenAI project key prefix
        "AKIA",       // AWS access key ID prefix
    ];

    for rel_path in &candidate_configs {
        let full_path = rust_workspace.join(rel_path);
        if !full_path.exists() {
            assert!(
                *rel_path != "Cargo.toml",
                "Rust workspace Cargo.toml must exist at {}",
                full_path.display()
            );
            continue;
        }

        let content = fs::read_to_string(&full_path)
            .unwrap_or_else(|e| panic!("Failed to read {}: {e}", full_path.display()));

        for pattern in &banned_patterns {
            assert!(
                !content.contains(pattern),
                "Found unredacted secret pattern '{pattern}' in {rel_path}\n\
                 This is a P0 security violation — remove the credential before committing."
            );
        }
    }
}

// ---------------------------------------------------------------------------
// T5 — Platform-conditional sandbox guard is present in sandbox.rs (finding F13)
// ---------------------------------------------------------------------------

#[test]
fn test_sandbox_rs_contains_platform_conditional_guards() {
    let sandbox_path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../rust/crates/runtime/src/sandbox.rs");

    let content = fs::read_to_string(&sandbox_path)
        .expect("Failed to read sandbox.rs for platform guard audit");

    // Assert that compile-time cfg guards are present, not merely runtime cfg! macros.
    assert!(
        content.contains("#[cfg(unix)]"),
        "sandbox.rs must use compile-time #[cfg(unix)] guards for Unix-specific paths"
    );
    assert!(
        content.contains("#[cfg(not(unix))]"),
        "sandbox.rs must provide #[cfg(not(unix))] fallback stubs for cross-platform compilation"
    );
    // Verify the runtime guard is *also* present as defense-in-depth.
    assert!(
        content.contains("cfg!(target_os = \"linux\")"),
        "sandbox.rs must retain the runtime cfg!(target_os = \"linux\") guard in resolve_sandbox_status_for_request"
    );
}
