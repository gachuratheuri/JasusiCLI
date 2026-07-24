use std::fs::{self, File};
use std::io::Write;

use runtime::{
    compute_sha256, rollback_transaction, validate_and_apply_patch, PatchError, PatchHunk,
    StructuredPatch,
};
use tempfile::tempdir;

#[test]
fn test_transactional_patch_apply_and_rollback() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    let file_path = root.join("src").join("lib.rs");
    fs::create_dir_all(file_path.parent().unwrap()).unwrap();

    let initial_content = "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n";
    {
        let mut f = File::create(&file_path).unwrap();
        f.write_all(initial_content.as_bytes()).unwrap();
    }

    let initial_hash = compute_sha256(initial_content);

    let patch = StructuredPatch {
        target_file: "src/lib.rs".to_string(),
        expected_sha256: Some(initial_hash.clone()),
        hunks: vec![PatchHunk {
            start_line: 1,
            end_line: 3,
            target_content: "a + b".to_string(),
            replacement_content: "a.saturating_add(b)".to_string(),
        }],
    };

    // Apply patch
    let (report, rollback) = validate_and_apply_patch(&patch, root).unwrap();
    assert!(report.is_valid);
    assert_eq!(report.pre_patch_sha256, initial_hash);

    let updated_content = fs::read_to_string(&file_path).unwrap();
    assert!(updated_content.contains("a.saturating_add(b)"));
    assert_eq!(report.post_patch_sha256, compute_sha256(&updated_content));

    // Rollback transaction
    rollback_transaction(&rollback, root).unwrap();
    let restored_content = fs::read_to_string(&file_path).unwrap();
    assert_eq!(restored_content, initial_content);
}

#[test]
fn test_patch_hash_mismatch_fails_closed() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    let file_path = root.join("test.txt");
    fs::write(&file_path, "current content").unwrap();

    let patch = StructuredPatch {
        target_file: "test.txt".to_string(),
        expected_sha256: Some("wrong_hash_1234567890".to_string()),
        hunks: vec![PatchHunk {
            start_line: 1,
            end_line: 1,
            target_content: "current content".to_string(),
            replacement_content: "new content".to_string(),
        }],
    };

    let res = validate_and_apply_patch(&patch, root);
    assert!(matches!(res, Err(PatchError::HashMismatch { .. })));

    // File content remains byte-for-byte unchanged
    assert_eq!(fs::read_to_string(&file_path).unwrap(), "current content");
}

#[test]
fn test_patch_target_content_not_found() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    let file_path = root.join("code.rs");
    fs::write(&file_path, "fn hello() {}").unwrap();

    let patch = StructuredPatch {
        target_file: "code.rs".to_string(),
        expected_sha256: None,
        hunks: vec![PatchHunk {
            start_line: 1,
            end_line: 1,
            target_content: "nonexistent code snippet".to_string(),
            replacement_content: "replacement".to_string(),
        }],
    };

    let res = validate_and_apply_patch(&patch, root);
    assert!(matches!(res, Err(PatchError::TargetContentNotFound(_))));
}

#[test]
fn test_patch_workspace_escape_rejected() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    let patch = StructuredPatch {
        target_file: "../outside.txt".to_string(),
        expected_sha256: None,
        hunks: vec![],
    };

    let res = validate_and_apply_patch(&patch, root);
    assert!(matches!(res, Err(PatchError::WorkspaceEscape(_))));
}
