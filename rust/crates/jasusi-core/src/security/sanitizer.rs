use regex::Regex;
use std::sync::OnceLock;

static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();

fn get_patterns() -> &'static Vec<Regex> {
    PATTERNS.get_or_init(|| {
        vec![
            Regex::new(r"sk-ant-[A-Za-z0-9\-_]{20,}").unwrap(),
            Regex::new(r"AIza[A-Za-z0-9\-_]{35}").unwrap(),
            Regex::new(r"gsk_[A-Za-z0-9]{50,}").unwrap(),
            Regex::new(
                r"(?i)(GROQ|ANTHROPIC|OPENAI|GEMINI|DEEPSEEK)_[A-Z_]*KEY[=:]\s*[^\s]{8,}",
            )
            .unwrap(),
            Regex::new(r"eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+").unwrap(),
            Regex::new(r"(?i)bearer\s+[A-Za-z0-9\-_\.]{20,}").unwrap(),
            Regex::new(r"(?i)token[=:]\s*[a-f0-9]{40,}").unwrap(),
        ]
    })
}

pub fn sanitize(input: &str) -> String {
    let mut output = input.to_string();
    for pattern in get_patterns() {
        output = pattern.replace_all(&output, "[REDACTED]").to_string();
    }
    output
}

pub fn contains_secret(input: &str) -> bool {
    get_patterns().iter().any(|p| p.is_match(input))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sanitize_anthropic_key() {
        let input = "Using key sk-ant-api03-supersecretkey12345678901234567";
        let result = sanitize(input);
        assert!(!result.contains("sk-ant"), "Anthropic key must be redacted");
        assert!(result.contains("[REDACTED]"));
    }

    #[test]
    fn test_sanitize_google_key() {
        let input = "AIzaSyD-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE";
        let result = sanitize(input);
        assert!(!result.contains("AIzaSy"), "Google key must be redacted");
    }

    #[test]
    fn test_sanitize_jwt() {
        let input = "Authorization: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abc123";
        let result = sanitize(input);
        assert!(!result.contains("eyJhbGci"), "JWT must be redacted");
    }

    #[test]
    fn test_sanitize_clean_input_unchanged() {
        let input = "This is a normal log message with no secrets.";
        let result = sanitize(input);
        assert_eq!(result, input);
    }

    #[test]
    fn test_contains_secret_true() {
        assert!(contains_secret(
            "sk-ant-api03-supersecretkey12345678901234567"
        ));
    }

    #[test]
    fn test_contains_secret_false() {
        assert!(!contains_secret("normal text without secrets"));
    }

    #[test]
    fn test_sanitize_multiple_secrets() {
        let input = "key1=sk-ant-api03-supersecretkey12345678901234567 key2=AIzaSyD-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE";
        let result = sanitize(input);
        assert_eq!(result.matches("[REDACTED]").count(), 2);
    }
}
