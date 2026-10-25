//! Durable Lease Manager (Sections 3.2, 5 & 6)
//! Provides cross-process atomic lease locking backed by active_leases.json and a lockfile.
//! Corrupted lease file fails closed, preventing silent double claims.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LeaseRecord {
    pub task_id: String,
    pub slot: String,
    pub created_at: f64,
    pub expires_at: f64,
    pub commit_sha: Option<String>,
}

impl LeaseRecord {
    pub fn is_expired(&self, now: f64) -> bool {
        now > self.expires_at
    }
}

pub struct DurableLeaseManager {
    lease_file: PathBuf,
    lock_file: PathBuf,
}

impl DurableLeaseManager {
    pub fn new<P: AsRef<Path>>(lease_file: P) -> Self {
        let p = lease_file.as_ref().to_path_buf();
        if let Some(parent) = p.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let lock_file = p.with_extension("lock");
        Self {
            lease_file: p,
            lock_file,
        }
    }

    fn now_secs() -> f64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0)
    }

    fn acquire_file_lock(&self) -> Result<std::fs::File, String> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&self.lock_file)
            .map_err(|e| format!("Failed to open lockfile {:?}: {}", self.lock_file, e))?;

        #[cfg(unix)]
        {
            use std::os::unix::io::AsRawFd;
            let fd = file.as_raw_fd();
            let ret = unsafe { libc::flock(fd, libc::LOCK_EX) };
            if ret != 0 {
                return Err(format!("flock failed on lockfile: errno {}", ret));
            }
        }

        Ok(file)
    }

    fn release_file_lock(&self, file: std::fs::File) {
        #[cfg(unix)]
        {
            use std::os::unix::io::AsRawFd;
            let fd = file.as_raw_fd();
            let _ = unsafe { libc::flock(fd, libc::LOCK_UN) };
        }
        drop(file);
    }

    fn read_leases_locked(&self) -> Result<HashMap<String, LeaseRecord>, String> {
        if !self.lease_file.exists() {
            return Ok(HashMap::new());
        }

        let mut content = String::new();
        let mut f = fs::File::open(&self.lease_file)
            .map_err(|e| format!("Failed to open lease file {:?}: {}", self.lease_file, e))?;
        f.read_to_string(&mut content)
            .map_err(|e| format!("Failed to read lease file {:?}: {}", self.lease_file, e))?;

        let trimmed = content.trim();
        if trimmed.is_empty() {
            return Ok(HashMap::new());
        }

        let map: HashMap<String, LeaseRecord> = serde_json::from_str(trimmed)
            .map_err(|e| format!("FATAL: Corrupted lease storage at {:?}: fail-closed: {}", self.lease_file, e))?;

        Ok(map)
    }

    fn write_leases_locked(&self, leases: &HashMap<String, LeaseRecord>) -> Result<(), String> {
        let now = Self::now_secs();
        let active: HashMap<&String, &LeaseRecord> = leases.iter().filter(|(_, v)| !v.is_expired(now)).collect();

        let json = serde_json::to_string_pretty(&active)
            .map_err(|e| format!("Failed to serialize leases: {}", e))?;

        let tmp_file = self.lease_file.with_extension("tmp");
        {
            let mut f = fs::File::create(&tmp_file)
                .map_err(|e| format!("Failed to create tmp lease file {:?}: {}", tmp_file, e))?;
            f.write_all(json.as_bytes())
                .map_err(|e| format!("Failed to write tmp lease file: {}", e))?;
            f.sync_all().map_err(|e| format!("Failed to sync tmp lease file: {}", e))?;
        }

        fs::rename(&tmp_file, &self.lease_file)
            .map_err(|e| format!("Failed to rename tmp lease file to {:?}: {}", self.lease_file, e))?;

        Ok(())
    }

    pub fn acquire_lease_atomic(
        &self,
        task_id: &str,
        slot: &str,
        ttl_secs: u64,
        commit_sha: Option<&str>,
        max_fallback_cap: Option<usize>,
    ) -> Result<Result<(), String>, String> {
        let lock = self.acquire_file_lock()?;
        let res = (|| {
            let mut leases = self.read_leases_locked()?;
            let now = Self::now_secs();

            if let Some(existing) = leases.get(task_id) {
                if !existing.is_expired(now) {
                    return Ok(Err("DOUBLE_CLAIM_REJECTED".to_string()));
                }
            }

            if slot == "codex-local" {
                if let Some(cap) = max_fallback_cap {
                    let active_fallback = leases
                        .values()
                        .filter(|r| r.slot == "codex-local" && !r.is_expired(now) && r.task_id != task_id)
                        .count();
                    if active_fallback >= cap {
                        return Ok(Err("FALLBACK_CAP_EXCEEDED".to_string()));
                    }
                }
            }

            leases.insert(
                task_id.to_string(),
                LeaseRecord {
                    task_id: task_id.to_string(),
                    slot: slot.to_string(),
                    created_at: now,
                    expires_at: now + ttl_secs as f64,
                    commit_sha: commit_sha.map(|s| s.to_string()),
                },
            );

            self.write_leases_locked(&leases)?;
            Ok(Ok(()))
        })();
        self.release_file_lock(lock);
        res
    }

    pub fn acquire_lease(
        &self,
        task_id: &str,
        slot: &str,
        ttl_secs: u64,
        commit_sha: Option<&str>,
    ) -> Result<bool, String> {
        match self.acquire_lease_atomic(task_id, slot, ttl_secs, commit_sha, None)? {
            Ok(()) => Ok(true),
            Err(_) => Ok(false),
        }
    }

    pub fn release_lease(&self, task_id: &str) -> Result<(), String> {
        let lock = self.acquire_file_lock()?;
        let res = (|| {
            let mut leases = self.read_leases_locked()?;
            if leases.remove(task_id).is_some() {
                self.write_leases_locked(&leases)?;
            }
            Ok(())
        })();
        self.release_file_lock(lock);
        res
    }

    pub fn get_lease(&self, task_id: &str) -> Result<Option<LeaseRecord>, String> {
        let lock = self.acquire_file_lock()?;
        let res = (|| {
            let leases = self.read_leases_locked()?;
            let now = Self::now_secs();
            if let Some(r) = leases.get(task_id) {
                if !r.is_expired(now) {
                    return Ok(Some(r.clone()));
                }
            }
            Ok(None)
        })();
        self.release_file_lock(lock);
        res
    }

    pub fn count_active_fallback(&self) -> Result<usize, String> {
        let lock = self.acquire_file_lock()?;
        let res = (|| {
            let leases = self.read_leases_locked()?;
            let now = Self::now_secs();
            let count = leases
                .values()
                .filter(|r| r.slot == "codex-local" && !r.is_expired(now))
                .count();
            Ok(count)
        })();
        self.release_file_lock(lock);
        res
    }
}
