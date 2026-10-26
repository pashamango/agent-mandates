use rusqlite::{params, Connection, Result};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

#[derive(Clone, Debug)]
pub struct Job {
    pub id: i64,
    pub run_id: String,
    pub _delivery_id: String,
    pub issue_id: String,
    pub identifier: String,
    pub title: String,
    pub description: String,
    pub command_type: String,
    pub builder: String,
    pub status: String,
}

#[derive(Clone, Debug)]
pub struct OutboxItem {
    pub id: i64,
    pub issue_id: String,
    pub identifier: String,
    pub comment_body: String,
    pub marker: String,
    pub builder: String,
}

#[derive(Clone)]
pub struct Database {
    conn: Arc<Mutex<Connection>>,
}

impl Database {
    pub fn new(path: &Path) -> Result<Self> {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }

        let conn = Connection::open(path)?;
        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;
             PRAGMA busy_timeout = 30000;
             
             CREATE TABLE IF NOT EXISTS inbox (
                 delivery_id TEXT PRIMARY KEY,
                 event_type TEXT,
                 action TEXT,
                 issue_id TEXT,
                 payload_json TEXT,
                 status TEXT,
                 created_at REAL
             );

             CREATE TABLE IF NOT EXISTS jobs (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 run_id TEXT UNIQUE,
                 delivery_id TEXT,
                 issue_id TEXT,
                 identifier TEXT,
                 title TEXT,
                 description TEXT,
                 command_type TEXT,
                 builder TEXT DEFAULT 'any',
                 status TEXT,
                 exit_code INTEGER,
                 stdout TEXT,
                 stderr TEXT,
                 error_message TEXT,
                 created_at REAL,
                 started_at REAL,
                 completed_at REAL
             );

             CREATE TABLE IF NOT EXISTS outbox (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 job_id INTEGER,
                 issue_id TEXT,
                 identifier TEXT,
                 comment_body TEXT,
                 marker TEXT UNIQUE,
                 status TEXT,
                 attempts INTEGER DEFAULT 0,
                 last_error TEXT,
                 created_at REAL,
                 updated_at REAL
             );

             CREATE INDEX IF NOT EXISTS idx_inbox_delivery ON inbox(delivery_id);
             CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
             CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);",
        )?;

        // Safe migration: check if builder column exists in jobs
        let _ = conn.execute("ALTER TABLE jobs ADD COLUMN builder TEXT DEFAULT 'any'", []);
        let _ = conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_builder ON jobs(builder)", []);
        let _ = conn.execute("ALTER TABLE outbox ADD COLUMN builder TEXT DEFAULT 'agy'", []);

        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
        })
    }

    pub fn execute_raw(&self, sql: &str) -> Result<usize> {
        let conn = self.conn.lock().unwrap();
        conn.execute(sql, [])
    }

    pub fn record_inbox(
        &self,
        delivery_id: &str,
        event_type: &str,
        action: &str,
        issue_id: &str,
        payload_json: &str,
        status: &str,
    ) -> bool {
        let conn = self.conn.lock().unwrap();
        let res = conn.execute(
            "INSERT INTO inbox (delivery_id, event_type, action, issue_id, payload_json, status, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![delivery_id, event_type, action, issue_id, payload_json, status, now()],
        );
        res.is_ok()
    }

    pub fn enqueue_job(
        &self,
        delivery_id: &str,
        issue_id: &str,
        identifier: &str,
        title: &str,
        description: &str,
        command_type: &str,
        builder: &str,
    ) -> Result<String> {
        let run_id = format!("run-{}", &uuid::Uuid::new_v4().simple().to_string()[..12]);
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO jobs (run_id, delivery_id, issue_id, identifier, title, description, command_type, builder, status, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'pending', ?9)",
            params![run_id, delivery_id, issue_id, identifier, title, description, command_type, builder, now()],
        )?;
        Ok(run_id)
    }

    pub fn is_issue_already_enqueued(&self, issue_id: &str) -> bool {
        let conn = self.conn.lock().unwrap();
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM jobs WHERE issue_id = ?1 AND status IN ('pending', 'running')",
                params![issue_id],
                |r| r.get(0),
            )
            .unwrap_or(0);
        count > 0
    }

    pub fn has_active_job_for_builder(&self, builder: &str) -> bool {
        let conn = self.conn.lock().unwrap();
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM jobs WHERE status = 'running' AND builder = ?1",
                params![builder],
                |r| r.get(0),
            )
            .unwrap_or(0);
        count > 0
    }

    pub fn reconcile_interrupted_jobs(&self) -> Result<usize> {
        let conn = self.conn.lock().unwrap();
        let count = conn.execute(
            "UPDATE jobs SET status = 'interrupted', error_message = 'Daemon restarted while running job'
             WHERE status = 'running'",
            [],
        )?;
        Ok(count)
    }

    pub fn fetch_next_pending_job_for_builder(&self, target_builder: &str) -> Result<Option<Job>> {
        let conn = self.conn.lock().unwrap();

        let agy_busy: bool = if target_builder == "codex" {
            let count: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM jobs WHERE status = 'running' AND builder = 'agy'",
                    [],
                    |r| r.get(0),
                )
                .unwrap_or(0);
            count > 0
        } else {
            false
        };

        let query = match target_builder {
            "agy" => {
                "SELECT id, run_id, delivery_id, issue_id, identifier, title, description, command_type, builder, status
                 FROM jobs 
                 WHERE status = 'pending' AND (builder = 'agy' OR builder = 'any') 
                 ORDER BY id ASC LIMIT 1"
            }
            "codex" => {
                if agy_busy {
                    "SELECT id, run_id, delivery_id, issue_id, identifier, title, description, command_type, builder, status
                     FROM jobs 
                     WHERE status = 'pending' AND (builder = 'codex' OR builder = 'any') 
                     ORDER BY id ASC LIMIT 1"
                } else {
                    "SELECT id, run_id, delivery_id, issue_id, identifier, title, description, command_type, builder, status
                     FROM jobs 
                     WHERE status = 'pending' AND builder = 'codex' 
                     ORDER BY id ASC LIMIT 1"
                }
            }
            _ => {
                "SELECT id, run_id, delivery_id, issue_id, identifier, title, description, command_type, builder, status
                 FROM jobs 
                 WHERE status = 'pending' AND builder = ?1 
                 ORDER BY id ASC LIMIT 1"
            }
        };

        let mut stmt = conn.prepare(query)?;
        let job_opt = if target_builder != "agy" && target_builder != "codex" {
            stmt.query_row(params![target_builder], |row| {
                Ok(Job {
                    id: row.get(0)?,
                    run_id: row.get(1)?,
                    _delivery_id: row.get(2)?,
                    issue_id: row.get(3)?,
                    identifier: row.get(4)?,
                    title: row.get(5)?,
                    description: row.get(6)?,
                    command_type: row.get(7)?,
                    builder: row.get(8)?,
                    status: row.get(9)?,
                })
            }).ok()
        } else {
            stmt.query_row([], |row| {
                Ok(Job {
                    id: row.get(0)?,
                    run_id: row.get(1)?,
                    _delivery_id: row.get(2)?,
                    issue_id: row.get(3)?,
                    identifier: row.get(4)?,
                    title: row.get(5)?,
                    description: row.get(6)?,
                    command_type: row.get(7)?,
                    builder: row.get(8)?,
                    status: row.get(9)?,
                })
            }).ok()
        };

        if let Some(mut job) = job_opt {
            // Lock job to this specific builder once running
            conn.execute(
                "UPDATE jobs SET status = 'running', builder = ?1, started_at = ?2 WHERE id = ?3",
                params![target_builder, now(), job.id],
            )?;
            job.status = "running".to_string();
            job.builder = target_builder.to_string();
            return Ok(Some(job));
        }

        Ok(None)
    }

    pub fn complete_job(
        &self,
        job_id: i64,
        status: &str,
        exit_code: i32,
        stdout: &str,
        stderr: &str,
        error_message: &str,
    ) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE jobs SET status = ?1, exit_code = ?2, stdout = ?3, stderr = ?4, error_message = ?5, completed_at = ?6
             WHERE id = ?7",
            params![status, exit_code, stdout, stderr, error_message, now(), job_id],
        )?;
        Ok(())
    }

    pub fn enqueue_outbox(
        &self,
        job_id: i64,
        issue_id: &str,
        identifier: &str,
        comment_body: &str,
        marker: &str,
        builder: &str,
    ) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR IGNORE INTO outbox (job_id, issue_id, identifier, comment_body, marker, builder, status, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'pending', ?7, ?8)",
            params![job_id, issue_id, identifier, comment_body, marker, builder, now(), now()],
        )?;
        Ok(())
    }

    pub fn fetch_pending_outbox(&self) -> Result<Vec<OutboxItem>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, issue_id, identifier, comment_body, marker, COALESCE(builder, 'agy')
             FROM outbox WHERE status = 'pending' ORDER BY id ASC LIMIT 10",
        )?;

        let rows = stmt.query_map([], |row| {
            Ok(OutboxItem {
                id: row.get(0)?,
                issue_id: row.get(1)?,
                identifier: row.get(2)?,
                comment_body: row.get(3)?,
                marker: row.get(4)?,
                builder: row.get(5)?,
            })
        })?;

        let mut items = Vec::new();
        for r in rows {
            if let Ok(item) = r {
                items.push(item);
            }
        }
        Ok(items)
    }

    pub fn mark_outbox_delivered(&self, outbox_id: i64) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE outbox SET status = 'delivered', updated_at = ?1 WHERE id = ?2",
            params![now(), outbox_id],
        )?;
        Ok(())
    }

    pub fn mark_outbox_failed(&self, outbox_id: i64, err: &str) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE outbox SET attempts = attempts + 1, last_error = ?1, updated_at = ?2,
             status = CASE WHEN attempts + 1 >= 5 THEN 'failed' ELSE 'pending' END
             WHERE id = ?3",
            params![&err[..err.len().min(500)], now(), outbox_id],
        )?;
        Ok(())
    }

    pub fn stats(&self) -> serde_json::Value {
        let conn = self.conn.lock().unwrap();
        let inbox_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM inbox", [], |r| r.get(0))
            .unwrap_or(0);
        let pending_jobs: i64 = conn
            .query_row("SELECT COUNT(*) FROM jobs WHERE status = 'pending'", [], |r| {
                r.get(0)
            })
            .unwrap_or(0);
        let running_jobs: i64 = conn
            .query_row("SELECT COUNT(*) FROM jobs WHERE status = 'running'", [], |r| {
                r.get(0)
            })
            .unwrap_or(0);
        let running_agy: i64 = conn
            .query_row("SELECT COUNT(*) FROM jobs WHERE status = 'running' AND builder = 'agy'", [], |r| {
                r.get(0)
            })
            .unwrap_or(0);
        let running_codex: i64 = conn
            .query_row("SELECT COUNT(*) FROM jobs WHERE status = 'running' AND builder = 'codex'", [], |r| {
                r.get(0)
            })
            .unwrap_or(0);
        let completed_jobs: i64 = conn
            .query_row("SELECT COUNT(*) FROM jobs WHERE status = 'completed'", [], |r| {
                r.get(0)
            })
            .unwrap_or(0);
        let failed_jobs: i64 = conn
            .query_row("SELECT COUNT(*) FROM jobs WHERE status = 'failed'", [], |r| {
                r.get(0)
            })
            .unwrap_or(0);
        let pending_outbox: i64 = conn
            .query_row("SELECT COUNT(*) FROM outbox WHERE status = 'pending'", [], |r| {
                r.get(0)
            })
            .unwrap_or(0);

        serde_json::json!({
            "inbox_total": inbox_count,
            "jobs_pending": pending_jobs,
            "jobs_running_total": running_jobs,
            "slots": {
                "agy": { "active": running_agy, "max": 1 },
                "codex": { "active": running_codex, "max": 1 }
            },
            "jobs_completed": completed_jobs,
            "jobs_failed": failed_jobs,
            "outbox_pending": pending_outbox,
        })
    }
}
