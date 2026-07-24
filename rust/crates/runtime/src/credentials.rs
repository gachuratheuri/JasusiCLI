use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

use regex::Regex;
use std::sync::OnceLock;

static CREDENTIAL_REGEX: OnceLock<Vec<Regex>> = OnceLock::new();

fn get_credential_regexes() -> &'static [Regex] {
    CREDENTIAL_REGEX.get_or_init(|| {
        vec![
            Regex::new(r"(?i)(sk-[a-zA-Z0-9_-]{20,})").unwrap(),
            Regex::new(r"(?i)(bearer\s+[a-zA-Z0-9_.-]{20,})").unwrap(),
            Regex::new(r"(?i)(ghp_[a-zA-Z0-9]{36})").unwrap(),
            Regex::new(r"(?i)(xox[baprs]-[a-zA-Z0-9_-]{10,})").unwrap(),
            Regex::new(r"(?i)(AIzaSy[a-zA-Z0-9_-]{33})").unwrap(),
            Regex::new(r#"(?i)(api[_-]?key\s*[:=]\s*['"]?)[a-zA-Z0-9_-]{16,}['"]?"#).unwrap(),
        ]
    })
}

/// Redact credentials and sensitive API tokens from arbitrary strings (logs, traces, errors).
#[must_use]
pub fn redact_credentials(input: &str) -> String {
    let mut result = input.to_string();
    for re in get_credential_regexes() {
        result = re.replace_all(&result, "[REDACTED_CREDENTIAL]").to_string();
    }
    result
}

/// Redact sensitive environment variables (key names containing KEY, SECRET, TOKEN, PASSWORD, AUTH).
#[must_use]
pub fn redact_environment(env: &[(String, String)]) -> Vec<(String, String)> {
    env.iter()
        .map(|(key, val)| {
            let key_upper = key.to_uppercase();
            if key_upper.contains("KEY")
                || key_upper.contains("SECRET")
                || key_upper.contains("TOKEN")
                || key_upper.contains("PASSWORD")
                || key_upper.contains("AUTH")
                || key_upper.contains("CREDENTIAL")
            {
                (key.clone(), "[REDACTED_ENV_VALUE]".to_string())
            } else {
                (key.clone(), val.clone())
            }
        })
        .collect()
}

/// Write OAuth/API credentials to a file with owner-only access permissions (0600 on Unix).
pub fn write_secure_credential_file(path: &Path, content: &str) -> Result<(), std::io::Error> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }

    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)?;

    file.write_all(content.as_bytes())?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = file.metadata()?.permissions();
        perms.set_mode(0o600);
        fs::set_permissions(path, perms)?;
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_redact_credentials() {
        let input = "Error: Anthropic key sk-ant-api03-abcdef1234567890abcdef1234567890 failed!";
        let redacted = redact_credentials(input);
        assert!(!redacted.contains("sk-ant-api03"));
        assert!(redacted.contains("[REDACTED_CREDENTIAL]"));
    }

    #[test]
    fn test_redact_environment() {
        let env = vec![
            ("PATH".to_string(), "/usr/bin".to_string()),
            (
                "ANTHROPIC_API_KEY".to_string(),
                "secret_value_123".to_string(),
            ),
        ];
        let redacted = redact_environment(&env);
        assert_eq!(redacted[0].1, "/usr/bin");
        assert_eq!(redacted[1].1, "[REDACTED_ENV_VALUE]");
    }
}
