use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::Command;

#[derive(Clone, Debug)]
pub struct Config {
    pub listen_addr: SocketAddr,
    pub linear_api_url: String,
    pub linear_api_key: String,
    pub linear_webhook_secret: Option<String>,
    pub db_path: PathBuf,
    pub pavel_user_id: String,
    pub bot_user_id: String,
    pub allowed_teams: Vec<String>,
    pub agy_bin: PathBuf,
    pub codex_bin: PathBuf,
    pub node_name: String,
    pub vacuum_enabled: bool,
    pub vacuum_interval_secs: u64,
    pub agy_min_interval_secs: u64,
}

impl Config {
    pub fn load() -> Self {
        let env_file_vars = Self::load_env_file();

        let mut api_key = std::env::var("LINEAR_API_KEY")
            .ok()
            .or_else(|| env_file_vars.get("LINEAR_API_KEY").cloned())
            .unwrap_or_default();

        if api_key.is_empty() {
            api_key = Self::get_token_from_keychain("mindrock");
        }

        let webhook_secret = std::env::var("LINEAR_WEBHOOK_SECRET")
            .ok()
            .or_else(|| env_file_vars.get("LINEAR_WEBHOOK_SECRET").cloned());

        let port: u16 = std::env::var("PORT")
            .ok()
            .or_else(|| env_file_vars.get("PORT").cloned())
            .and_then(|p| p.parse().ok())
            .unwrap_or(18792);

        let host = std::env::var("LISTEN_HOST")
            .ok()
            .or_else(|| env_file_vars.get("LISTEN_HOST").cloned())
            .unwrap_or_else(|| "127.0.0.1".to_string());

        let listen_addr: SocketAddr = format!("{}:{}", host, port)
            .parse()
            .unwrap_or_else(|_| "127.0.0.1:18792".parse().unwrap());

        let db_path = std::env::var("DISPATCHER_DB_PATH")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/tmp/fleetd.db"));

        let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/pavelcherkashin".to_string());

        let agy_bin = std::env::var("AGY_BIN")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                let p1 = PathBuf::from(format!("{}/.local/bin/agy", home));
                if p1.exists() {
                    p1
                } else {
                    PathBuf::from(format!("{}/.npm-global/bin/gemini", home))
                }
            });

        let codex_bin = std::env::var("CODEX_BIN")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                let p1 = PathBuf::from("/opt/homebrew/bin/codex");
                if p1.exists() {
                    p1
                } else {
                    PathBuf::from(format!("{}/.npm-global/bin/codex", home))
                }
            });

        let node_name = std::env::var("NODE_NAME")
            .ok()
            .or_else(|| env_file_vars.get("NODE_NAME").cloned())
            .unwrap_or_else(|| {
                let h = Command::new("hostname").output().ok();
                let s = h.map(|o| String::from_utf8_lossy(&o.stdout).trim().to_lowercase()).unwrap_or_default();
                if s.contains("darwin") {
                    "darwin".to_string()
                } else {
                    "wolfram".to_string()
                }
            });

        let vacuum_enabled = std::env::var("VACUUM_ENABLED")
            .ok()
            .or_else(|| env_file_vars.get("VACUUM_ENABLED").cloned())
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
            .unwrap_or(true);

        let vacuum_interval_secs: u64 = std::env::var("VACUUM_INTERVAL_SECS")
            .ok()
            .or_else(|| env_file_vars.get("VACUUM_INTERVAL_SECS").cloned())
            .and_then(|v| v.parse().ok())
            .unwrap_or(120);

        let agy_min_interval_secs: u64 = std::env::var("AGY_MIN_INTERVAL_SECS")
            .ok()
            .or_else(|| env_file_vars.get("AGY_MIN_INTERVAL_SECS").cloned())
            .and_then(|v| v.parse().ok())
            .unwrap_or(300);

        Self {
            listen_addr,
            linear_api_url: "https://api.linear.app/graphql".to_string(),
            linear_api_key: api_key,
            linear_webhook_secret: webhook_secret,
            db_path,
            pavel_user_id: "9812c512-26d5-4d6e-ac81-67d15d714ce4".to_string(),
            bot_user_id: "7f186470-b6ed-407c-9491-74ec4e30ed1c".to_string(),
            allowed_teams: vec!["MIN".to_string(), "PRD".to_string()],
            agy_bin,
            codex_bin,
            node_name,
            vacuum_enabled,
            vacuum_interval_secs,
            agy_min_interval_secs,
        }
    }

    fn load_env_file() -> std::collections::HashMap<String, String> {
        let mut map = std::collections::HashMap::new();
        let paths = [
            PathBuf::from("dispatcher.env"),
            PathBuf::from("../agy-dispatcher/dispatcher.env"),
            PathBuf::from("/Users/pavelcherkashin/ChitAkasha-Wolfram/bin/agy-dispatcher/dispatcher.env"),
        ];

        for path in &paths {
            if let Ok(content) = std::fs::read_to_string(path) {
                for line in content.lines() {
                    let trimmed = line.trim();
                    if trimmed.is_empty() || trimmed.starts_with('#') {
                        continue;
                    }
                    if let Some((k, v)) = trimmed.split_once('=') {
                        let val = v.trim().trim_matches(|c| c == '\'' || c == '"');
                        map.insert(k.trim().to_string(), val.to_string());
                    }
                }
                break;
            }
        }
        map
    }

    fn get_token_from_keychain(service: &str) -> String {
        let output = Command::new("security")
            .args(["find-generic-password", "-s", service, "-w"])
            .output();

        if let Ok(out) = output {
            if out.status.success() {
                return String::from_utf8_lossy(&out.stdout).trim().to_string();
            }
        }
        String::new()
    }
}
