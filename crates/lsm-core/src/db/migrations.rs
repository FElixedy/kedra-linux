//! Schema migrations.
//!
//! Migrations are append-only: add a new entry to [`MIGRATIONS`], never edit an
//! existing one. Each runs inside a transaction together with the version bump,
//! so a failed migration leaves the database untouched.

use rusqlite::Connection;

use crate::error::Result;

pub struct Migration {
    pub version: i64,
    pub name: &'static str,
    pub sql: &'static str,
}

/// All migrations, in order.
pub static MIGRATIONS: &[Migration] = &[Migration {
    version: 1,
    name: "initial_schema",
    sql: super::schema::V1_INITIAL,
}];

/// The version a fully-migrated database reports.
pub const LATEST_VERSION: i64 = 1;

/// Apply any migrations the database has not seen yet.
pub fn run(conn: &mut Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );",
    )?;

    let current = current_version(conn)?;
    for m in MIGRATIONS.iter().filter(|m| m.version > current) {
        let tx = conn.transaction()?;
        tx.execute_batch(m.sql)?;
        tx.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?1, ?2)",
            rusqlite::params![m.version, m.name],
        )?;
        tx.commit()?;
        tracing::info!(version = m.version, name = m.name, "applied migration");
    }
    Ok(())
}

pub fn current_version(conn: &Connection) -> Result<i64> {
    let v: Option<i64> = conn
        .query_row("SELECT MAX(version) FROM schema_migrations", [], |r| {
            r.get(0)
        })
        .unwrap_or(None);
    Ok(v.unwrap_or(0))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fresh() -> Connection {
        let mut c = Connection::open_in_memory().unwrap();
        run(&mut c).unwrap();
        c
    }

    #[test]
    fn migration_versions_are_unique_sequential_and_match_latest() {
        let versions: Vec<i64> = MIGRATIONS.iter().map(|m| m.version).collect();
        let mut sorted = versions.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(versions, sorted, "migrations must be ordered and unique");
        assert_eq!(
            *versions.last().unwrap(),
            LATEST_VERSION,
            "LATEST_VERSION must track the last migration"
        );
        assert_eq!(versions[0], 1, "versions start at 1");
    }

    #[test]
    fn running_twice_changes_nothing() {
        let mut c = fresh();
        let v1 = current_version(&c).unwrap();
        run(&mut c).unwrap();
        assert_eq!(current_version(&c).unwrap(), v1);

        let applied: i64 = c
            .query_row("SELECT COUNT(*) FROM schema_migrations", [], |r| r.get(0))
            .unwrap();
        assert_eq!(applied, MIGRATIONS.len() as i64);
    }

    #[test]
    fn every_expected_table_exists_after_migrating() {
        let c = fresh();
        let mut stmt = c
            .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            .unwrap();
        let tables: Vec<String> = stmt
            .query_map([], |r| r.get::<_, String>(0))
            .unwrap()
            .map(|r| r.unwrap())
            .collect();

        for expected in [
            "boot_records",
            "boot_unit_times",
            "change_event_entities",
            "change_event_sources",
            "change_event_subsystems",
            "change_events",
            "collector_state",
            "config_state",
            "operation_log",
            "operations",
            "schema_migrations",
            "snapshot_config",
            "snapshot_dkms",
            "snapshot_facts",
            "snapshot_packages",
            "snapshot_services",
            "snapshots",
        ] {
            assert!(
                tables.contains(&expected.to_string()),
                "missing table `{expected}`"
            );
        }
    }

    #[test]
    fn change_events_are_indexed_for_the_queries_the_ui_actually_makes() {
        let c = fresh();
        let mut stmt = c
            .prepare("SELECT name FROM sqlite_master WHERE type='index'")
            .unwrap();
        let indexes: Vec<String> = stmt
            .query_map([], |r| r.get::<_, String>(0))
            .unwrap()
            .map(|r| r.unwrap())
            .collect();

        // Time-range scans are the hot path for What Changed?.
        assert!(indexes
            .iter()
            .any(|i| i.contains("change_events_timestamp")));
        assert!(indexes.iter().any(|i| i.contains("change_events_dedup")));
    }

    #[test]
    fn dedup_key_is_unique_so_the_same_change_cannot_be_stored_twice() {
        let c = fresh();
        let insert = "INSERT INTO change_events
            (timestamp, source, category, relevance, title, metadata, dedup_key)
            VALUES ('2026-08-14T12:00:00Z', 'pacman', 'package', 'info', 'mesa updated', '{}', 'pkg:mesa:1->2')";
        c.execute(insert, []).unwrap();
        let second = c.execute(insert, []);
        assert!(
            second.is_err(),
            "a duplicate dedup_key must be rejected by the schema"
        );
    }

    #[test]
    fn deleting_an_event_cascades_to_its_child_rows() {
        let c = fresh();
        c.pragma_update(None, "foreign_keys", "ON").unwrap();
        c.execute(
            "INSERT INTO change_events (id, timestamp, source, category, relevance, title, metadata, dedup_key)
             VALUES (1, '2026-08-14T12:00:00Z', 'pacman', 'package', 'info', 't', '{}', 'k')",
            [],
        )
        .unwrap();
        c.execute(
            "INSERT INTO change_event_entities (event_id, entity_kind, entity_id)
             VALUES (1, 'package', 'mesa')",
            [],
        )
        .unwrap();

        c.execute("DELETE FROM change_events WHERE id = 1", [])
            .unwrap();
        let remaining: i64 = c
            .query_row("SELECT COUNT(*) FROM change_event_entities", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(remaining, 0, "orphaned entity rows must not survive");
    }
}
