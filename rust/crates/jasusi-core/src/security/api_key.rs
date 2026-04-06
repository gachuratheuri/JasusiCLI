use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ApiKey(String);

impl ApiKey {
    pub fn new(raw: impl Into<String>) -> Self {
        Self(raw.into())
    }

    pub fn expose(&self) -> &str {
        &self.0
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl std::fmt::Display for ApiKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "***")
    }
}

impl std::fmt::Debug for ApiKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "ApiKey(***)")
    }
}

impl PartialEq for ApiKey {
    fn eq(&self, other: &Self) -> bool {
        self.0 == other.0
    }
}

impl Eq for ApiKey {}

#[derive(Debug, Clone)]
pub enum AuthSource {
    None,
    ApiKey(ApiKey),
    BearerToken(ApiKey),
    ApiKeyAndBearer {
        api_key: ApiKey,
        bearer_token: ApiKey,
    },
}

impl AuthSource {
    pub fn from_env(prefix: &str) -> Self {
        let key_var = format!("{}_API_KEY", prefix.to_uppercase());
        let token_var = format!("{}_AUTH_TOKEN", prefix.to_uppercase());

        let key = std::env::var(&key_var).ok().map(ApiKey::new);
        let token = std::env::var(&token_var).ok().map(ApiKey::new);

        match (key, token) {
            (Some(k), Some(t)) => Self::ApiKeyAndBearer {
                api_key: k,
                bearer_token: t,
            },
            (Some(k), None) => Self::ApiKey(k),
            (None, Some(t)) => Self::BearerToken(t),
            (None, None) => Self::None,
        }
    }

    pub fn is_configured(&self) -> bool {
        !matches!(self, Self::None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_api_key_display_redacted() {
        let key = ApiKey::new("sk-ant-supersecret123");
        assert_eq!(format!("{key}"), "***");
        assert_eq!(format!("{key:?}"), "ApiKey(***)");
    }

    #[test]
    fn test_api_key_expose_returns_real_value() {
        let key = ApiKey::new("sk-ant-supersecret123");
        assert_eq!(key.expose(), "sk-ant-supersecret123");
    }

    #[test]
    fn test_api_key_not_leaked_in_struct_debug() {
        let auth = AuthSource::ApiKey(ApiKey::new("sk-real-key"));
        let debug = format!("{auth:?}");
        assert!(
            !debug.contains("sk-real-key"),
            "Real key must not appear in Debug output"
        );
        assert!(debug.contains("***"));
    }

    #[test]
    fn test_api_key_equality_on_real_value() {
        let a = ApiKey::new("same");
        let b = ApiKey::new("same");
        let c = ApiKey::new("different");
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    #[test]
    fn test_auth_source_none_not_configured() {
        let auth = AuthSource::None;
        assert!(!auth.is_configured());
    }

    #[test]
    fn test_auth_source_api_key_is_configured() {
        let auth = AuthSource::ApiKey(ApiKey::new("key"));
        assert!(auth.is_configured());
    }
}
