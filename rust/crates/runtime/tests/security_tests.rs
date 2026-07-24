use std::fs::{self, File};

use runtime::{
    is_path_safe_in_workspace, redact_credentials, redact_environment, validate_execution_allowed,
    Capability, CapabilitySet, FilesystemIsolationMode, PermissionMode, PermissionOutcome,
    PermissionPolicy, SandboxStatus,
};
use tempfile::tempdir;

#[test]
fn test_capability_set_hierarchy_and_permissions() {
    let mut caps = CapabilitySet::read_only();
    assert!(caps.contains(Capability::Read));
    assert!(!caps.contains(Capability::Execute));

    caps.grant(Capability::Execute);
    assert!(caps.contains(Capability::Execute));

    let full_caps = CapabilitySet::all();
    assert!(caps.is_subset(&full_caps));
}

#[test]
fn test_deny_rules_strictly_precede_allow_rules() {
    use runtime::RuntimePermissionRuleConfig;

    let config = RuntimePermissionRuleConfig::new(
        vec!["bash(*)".to_string()],    // allow rule
        vec!["bash(rm:*)".to_string()], // deny rule
        vec![],
    );

    let policy = PermissionPolicy::new(PermissionMode::Allow).with_permission_rules(&config);

    // rm command matched by deny rule must be Denied despite general allow rule and Allow mode
    let outcome = policy.authorize("bash", "rm -rf /", None);
    assert!(matches!(outcome, PermissionOutcome::Deny { .. }));

    // safe command passed
    let outcome_safe = policy.authorize("bash", "ls -la", None);
    assert_eq!(outcome_safe, PermissionOutcome::Allow);
}

#[test]
fn test_path_canonicalization_prevents_workspace_escape() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    let safe_file = root.join("src").join("main.rs");
    fs::create_dir_all(safe_file.parent().unwrap()).unwrap();
    File::create(&safe_file).unwrap();

    assert!(is_path_safe_in_workspace(&safe_file, root));

    // Path traversal attempt using ..
    let escape_file = root.join("..").join("secret.txt");
    assert!(!is_path_safe_in_workspace(&escape_file, root));
}

#[test]
fn test_fail_closed_sandbox_validation() {
    let status = SandboxStatus {
        enabled: true,
        supported: false,
        active: false,
        filesystem_mode: FilesystemIsolationMode::Off,
        ..Default::default()
    };

    // When sandboxing is inactive and unsafe_local_mode is false, execution of write/shell must fail closed
    let res = validate_execution_allowed(&status, false, true);
    assert!(res.is_err());
    assert!(res.unwrap_err().contains("Security Denial"));

    // When unsafe_local_mode is explicitly granted, execution is permitted
    let res_unsafe = validate_execution_allowed(&status, true, true);
    assert!(res_unsafe.is_ok());

    // Read-only operations allowed
    let res_read = validate_execution_allowed(&status, false, false);
    assert!(res_read.is_ok());
}

#[test]
fn test_credential_redaction_in_logs_and_env() {
    let secret = "sk-ant-api03-123456789012345678901234567890123456";
    let log_msg = format!("Making request with key {secret} to provider");
    let redacted = redact_credentials(&log_msg);

    assert!(!redacted.contains(secret));
    assert!(redacted.contains("[REDACTED_CREDENTIAL]"));

    let env_pairs = vec![
        ("PATH".to_string(), "/bin".to_string()),
        (
            "AWS_SECRET_ACCESS_KEY".to_string(),
            "supersecret123".to_string(),
        ),
    ];
    let redacted_env = redact_environment(&env_pairs);
    assert_eq!(redacted_env[1].1, "[REDACTED_ENV_VALUE]");
}
