//! SQLite storage.
//!
//! This database caches and *normalizes* history. It is never the source of
//! truth for current system state: the running kernel, installed packages and
//! unit states are always read from the system itself. What lives here is the
//! history the system does not keep — normalized change events, operation
//! records, metadata snapshots and boot records.

pub mod migrations;
pub mod schema;

use std::path::{Path, PathBuf};
use std::sync::Arc;

use rusqlite::Connection;
use tokio::sync::Mutex;

use crate::error::{Error, Result};

/// Handle to the application database.
///
/// rusqlite is synchronous, so every access runs on the blocking pool via
/// [`Db::with`]. The connection sits behind an async mutex, which keeps writes
/// serialized without ever blocking a runtime worker.
#[derive(Clone)]
pub struct Db {
    conn: Arc<Mutex<Connection>>,
    path: Option<PathBuf>,
}

impl std::fmt::Debug for Db {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Db")
            .field("path", &self.path)
            .finish_non_exhaustive()
    }
}

impl Db {
    /// Open (creating if needed) the database at `path` and run migrations.
    pub async fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent)
                .await
                .map_err(|e| Error::io(parent, e))?;
        }
        let p = path.clone();
        let conn = tokio::task::spawn_blocking(move || -> Result<Connection> {
            let conn = Connection::open(&p)?;
            configure(&conn)?;
            Ok(conn)
        })
        .await
        .map_err(|e| Error::Internal(format!("database task failed: {e}")))??;

        let db = Db {
            conn: Arc::new(Mutex::new(conn)),
            path: Some(path),
        };
        db.migrate().await?;
        Ok(db)
    }

    /// Open an in-memory database. Used by tests and by the GUI when the real
    /// database cannot be opened, so the app still runs read-only rather than
    /// refusing to start.
    pub async fn open_in_memory() -> Result<Self> {
        let conn = tokio::task::spawn_blocking(|| -> Result<Connection> {
            let conn = Connection::open_in_memory()?;
            configure(&conn)?;
            Ok(conn)
        })
        .await
        .map_err(|e| Error::Internal(format!("database task failed: {e}")))??;

        let db = Db {
            conn: Arc::new(Mutex::new(conn)),
            path: None,
        };
        db.migrate().await?;
        Ok(db)
    }

    /// The default location: `$XDG_DATA_HOME/linux-system-manager/history.db`.
    pub fn default_path() -> PathBuf {
        let base = std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share")))
            .unwrap_or_else(|| PathBuf::from("/tmp"));
        base.join("linux-system-manager").join("history.db")
    }

    pub fn path(&self) -> Option<&Path> {
        self.path.as_deref()
    }

    /// Run a closure against the connection on the blocking pool.
    pub async fn with<F, T>(&self, f: F) -> Result<T>
    where
        F: FnOnce(&mut Connection) -> Result<T> + Send + 'static,
        T: Send + 'static,
    {
        let conn = self.conn.clone();
        // Hold the lock across the blocking call so writes stay serialized.
        let guard = conn.lock_owned().await;
        tokio::task::spawn_blocking(move || {
            let mut guard = guard;
            f(&mut guard)
        })
        .await
        .map_err(|e| {
            // A panic inside a query must surface as an error, never as a crash.
            if e.is_panic() {
                Error::from_panic(e.into_panic())
            } else {
                Error::Internal(format!("database task failed: {e}"))
            }
        })?
    }

    async fn migrate(&self) -> Result<()> {
        self.with(migrations::run).await
    }

    /// Current schema version.
    pub async fn schema_version(&self) -> Result<i64> {
        self.with(|conn| migrations::current_version(conn)).await
    }
}

/// Pragmas applied to every connection.
fn configure(conn: &Connection) -> Result<()> {
    // WAL keeps readers (the UI) from blocking on the collector's writes.
    conn.pragma_update(None, "journal_mode", "WAL")?;
    // NORMAL is the right durability trade-off for a cache of history: a crash
    // may lose the last transaction, which we can re-collect.
    conn.pragma_update(None, "synchronous", "NORMAL")?;
    conn.pragma_update(None, "foreign_keys", "ON")?;
    conn.pragma_update(None, "busy_timeout", 5_000)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn opens_in_memory_and_applies_every_migration() {
        let db = Db::open_in_memory().await.unwrap();
        assert_eq!(
            db.schema_version().await.unwrap(),
            migrations::LATEST_VERSION
        );
    }

    #[tokio::test]
    async fn migrations_are_idempotent_across_reopens() {
        let dir = tempdir();
        let path = dir.join("history.db");

        let db = Db::open(&path).await.unwrap();
        assert_eq!(
            db.schema_version().await.unwrap(),
            migrations::LATEST_VERSION
        );
        drop(db);

        // Opening again must not re-run anything or fail on existing tables.
        let db2 = Db::open(&path).await.unwrap();
        assert_eq!(
            db2.schema_version().await.unwrap(),
            migrations::LATEST_VERSION
        );

        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn foreign_keys_are_enforced() {
        let db = Db::open_in_memory().await.unwrap();
        let enabled: i64 = db
            .with(|c| Ok(c.query_row("PRAGMA foreign_keys", [], |r| r.get(0))?))
            .await
            .unwrap();
        assert_eq!(enabled, 1);
    }

    #[tokio::test]
    async fn a_panic_inside_a_query_becomes_an_error_not_a_crash() {
        let db = Db::open_in_memory().await.unwrap();
        let err = db
            .with(|_| -> Result<()> { panic!("query exploded") })
            .await
            .unwrap_err();
        assert!(err.technical_detail().contains("query exploded"));
    }

    #[tokio::test]
    async fn default_path_lives_under_the_users_data_directory() {
        let p = Db::default_path();
        assert!(
            p.ends_with("linux-system-manager/history.db"),
            "got {}",
            p.display()
        );
        assert!(p.is_absolute());
    }

    pub(crate) fn tempdir() -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "lsm-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&base).unwrap();
        base
    }
}
