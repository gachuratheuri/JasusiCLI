use sha2::{Digest, Sha256};
use std::collections::HashSet;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FirewallVerdict {
    Allow,
    Deny { reason: String },
    Quarantine { threat_class: String, fragment: String },
}

pub struct SemanticFirewall {
    blocked_commands: HashSet<&'static str>,
    blocked_patterns: Vec<&'static str>,
}

impl SemanticFirewall {
    pub fn new() -> Self {
        Self {
            blocked_commands: HashSet::from([
                "rm -rf /",
                "dd if=/dev/zero",
                "mkfs",
                ":(){ :|:& };:",
                "chmod 777 /",
                "chown -R root /",
                "curl | bash",
                "wget | bash",
                "curl | sh",
                "wget | sh",
            ]),
            blocked_patterns: vec![
                "DROP TABLE",
                "DROP DATABASE",
                "--no-preserve-root",
                "/dev/sda",
                "/dev/nvme",
                "base64 -d | bash",
                "base64 -d | sh",
                "eval $(",
                "exec 3<>/dev/tcp",
            ],
        }
    }

    pub fn inspect(&self, tool_name: &str, input_json: &[u8]) -> FirewallVerdict {
        let input_str = String::from_utf8_lossy(input_json);

        for blocked in &self.blocked_commands {
            if input_str.contains(blocked) {
                return FirewallVerdict::Quarantine {
                    threat_class: "DESTRUCTIVE_COMMAND".to_string(),
                    fragment: (*blocked).to_string(),
                };
            }
        }

        for pattern in &self.blocked_patterns {
            if input_str.to_uppercase().contains(&pattern.to_uppercase()) {
                return FirewallVerdict::Quarantine {
                    threat_class: "DANGEROUS_PATTERN".to_string(),
                    fragment: (*pattern).to_string(),
                };
            }
        }

        if serde_json::from_slice::<serde_json::Value>(input_json).is_err() {
            return FirewallVerdict::Deny {
                reason: format!("Malformed JSON input for tool {tool_name}"),
            };
        }

        FirewallVerdict::Allow
    }

    pub fn audit_hash(input_json: &[u8]) -> String {
        let mut hasher = Sha256::new();
        hasher.update(input_json);
        hex::encode(hasher.finalize())
    }
}

impl Default for SemanticFirewall {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fw() -> SemanticFirewall {
        SemanticFirewall::new()
    }

    #[test]
    fn test_allow_safe_command() {
        let input = br#"{"command": "cargo build"}"#;
        assert_eq!(fw().inspect("bash", input), FirewallVerdict::Allow);
    }

    #[test]
    fn test_quarantine_rm_rf() {
        let input = br#"{"command": "rm -rf /"}"#;
        let verdict = fw().inspect("bash", input);
        assert!(matches!(verdict, FirewallVerdict::Quarantine { .. }));
    }

    #[test]
    fn test_quarantine_fork_bomb() {
        let input = br#"{"command": ":(){ :|:& };:"}"#;
        let verdict = fw().inspect("bash", input);
        assert!(matches!(verdict, FirewallVerdict::Quarantine { .. }));
    }

    #[test]
    fn test_deny_malformed_json() {
        let input = b"not valid json at all {{{{";
        let verdict = fw().inspect("bash", input);
        assert!(matches!(verdict, FirewallVerdict::Deny { .. }));
    }

    #[test]
    fn test_quarantine_drop_table() {
        let input = br#"{"query": "DROP TABLE users"}"#;
        let verdict = fw().inspect("file_write", input);
        assert!(matches!(verdict, FirewallVerdict::Quarantine { .. }));
    }

    #[test]
    fn test_audit_hash_is_sha256_hex() {
        let input = b"cargo build";
        let hash = SemanticFirewall::audit_hash(input);
        assert_eq!(hash.len(), 64);
        assert!(hash.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn test_audit_hash_deterministic() {
        let input = b"same input";
        assert_eq!(
            SemanticFirewall::audit_hash(input),
            SemanticFirewall::audit_hash(input)
        );
    }

    #[test]
    fn test_audit_hash_not_raw_input() {
        let input = b"sk-ant-supersecret";
        let hash = SemanticFirewall::audit_hash(input);
        assert!(!hash.contains("sk-ant"));
    }
}
