//! Phase C2 persistence: comparison entities, baselines, profiles,
//! evaluations and stored diffs.
//!
//! All functions run on the single storage thread (see `service.rs`), so
//! they take a plain `Connection` and never worry about concurrency.

use crate::service::StorageError;
use crate::types::{ProfileRevisionRow, ProfileRow};
use chrono::{DateTime, Utc};
use doctor_domain::baseline::{Baseline, BaselineEntity, BaselineSource};
use doctor_domain::comparison::{ComparisonEntity, EntityKey, NamespaceAvailability};
use doctor_domain::evaluation::{EvaluationRun, ExpectationResult};
use doctor_domain::profile::{DeviceProfileAssignment, Profile, ProfileStatus};
use doctor_domain::{BaselineDiff, BaselineId, DeviceId, ProfileId, RunId, RunProjection};
use rusqlite::{params, Connection, OptionalExtension};

fn json<T: serde::Serialize>(value: &T) -> String {
    serde_json::to_string(value).expect("storage payloads serialize")
}

fn parse<T: serde::de::DeserializeOwned>(text: &str, what: &str) -> Result<T, StorageError> {
    serde_json::from_str(text).map_err(|e| StorageError::Corrupt(format!("malformed {what}: {e}")))
}

fn parse_ts(text: &str) -> Result<DateTime<Utc>, StorageError> {
    DateTime::parse_from_rfc3339(text)
        .map(|t| t.with_timezone(&Utc))
        .map_err(|e| StorageError::Corrupt(format!("bad timestamp '{text}': {e}")))
}

fn enum_str<T: serde::Serialize>(value: &T) -> String {
    match serde_json::to_value(value) {
        Ok(serde_json::Value::String(s)) => s,
        other => panic!("expected string-serializable enum, got {other:?}"),
    }
}

fn enum_parse<T: serde::de::DeserializeOwned>(s: &str) -> Result<T, StorageError> {
    serde_json::from_value(serde_json::Value::String(s.to_owned()))
        .map_err(|e| StorageError::Corrupt(format!("bad enum value '{s}': {e}")))
}

// ── run entities ───────────────────────────────────────────────────────

/// Read comparison entities filtered by `run_id` or `result_id`.
pub fn read_entities(
    conn: &Connection,
    column: &str,
    value: &str,
) -> Result<Vec<ComparisonEntity>, StorageError> {
    // `column` is a compile-time constant at every call site, never user
    // input; entity values are still bound as parameters.
    let sql = format!(
        "SELECT namespace, kind, entity_key, display_name, attributes,
                source_plugin, source_check, evidence_ids
         FROM run_entities WHERE {column} = ?1
         ORDER BY namespace, kind, entity_key"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt
        .query_map(params![value], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, String>(6)?,
                row.get::<_, String>(7)?,
            ))
        })?
        .collect::<Result<Vec<_>, _>>()?;
    rows.into_iter()
        .map(
            |(namespace, kind, key, display_name, attributes, plugin, check, evidence)| {
                Ok(ComparisonEntity {
                    key: EntityKey::new(namespace, kind, key),
                    display_name,
                    attributes: parse(&attributes, "entity attributes")?,
                    source_plugin: plugin.as_str().into(),
                    source_check: check.as_str().into(),
                    source_evidence_ids: parse(&evidence, "entity evidence ids")?,
                })
            },
        )
        .collect()
}

/// The full comparison projection of a persisted run: entities plus which
/// namespaces could actually be observed.
pub fn run_projection(conn: &Connection, run_id: &RunId) -> Result<RunProjection, StorageError> {
    let device_id: Option<String> = conn
        .query_row(
            "SELECT device_id FROM diagnostic_runs WHERE id = ?1",
            params![run_id.as_str()],
            |row| row.get(0),
        )
        .optional()?;
    let Some(device_id) = device_id else {
        return Err(StorageError::Corrupt(format!(
            "diagnostic run '{run_id}' does not exist"
        )));
    };

    let mut projection = RunProjection {
        run_id: run_id.clone(),
        device_id: DeviceId::from(device_id.as_str()),
        entities: read_entities(conn, "run_id", run_id.as_str())?,
        ..Default::default()
    };

    // Availability: any check that observed a namespace (or kind) wins;
    // otherwise a failed attempt marks it NOT_OBSERVED; a namespace or
    // kind nobody reported on stays UNSUPPORTED.
    let mut stmt = conn.prepare(
        "SELECT projection_namespace, projection_observed, projection_reason, projection_kinds
         FROM check_results
         WHERE run_id = ?1 AND projection_namespace IS NOT NULL",
    )?;
    let rows = stmt
        .query_map(params![run_id.as_str()], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<i64>>(1)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, Option<String>>(3)?,
            ))
        })?
        .collect::<Result<Vec<_>, _>>()?;
    for (namespace, observed, reason, kinds) in rows {
        let observed = observed.unwrap_or(0) != 0;
        let kinds: Vec<String> = kinds
            .as_deref()
            .and_then(|text| serde_json::from_str(text).ok())
            .unwrap_or_default();

        let entry = projection
            .namespaces
            .entry(namespace.clone())
            .or_insert(NamespaceAvailability::NotObserved);
        if observed {
            *entry = NamespaceAvailability::Observed;
            projection.namespace_reasons.remove(&namespace);
        } else if *entry != NamespaceAvailability::Observed {
            if let Some(reason) = &reason {
                projection
                    .namespace_reasons
                    .insert(namespace.clone(), reason.clone());
            }
        }

        for kind in kinds {
            let key = RunProjection::kind_key(&namespace, &kind);
            let entry = projection
                .kinds
                .entry(key.clone())
                .or_insert(NamespaceAvailability::NotObserved);
            if observed {
                *entry = NamespaceAvailability::Observed;
                projection.kind_reasons.remove(&key);
            } else if *entry != NamespaceAvailability::Observed {
                if let Some(reason) = &reason {
                    projection.kind_reasons.insert(key, reason.clone());
                }
            }
        }
    }
    Ok(projection)
}

// ── baselines ──────────────────────────────────────────────────────────

pub fn insert_baseline(conn: &mut Connection, baseline: &Baseline) -> Result<(), StorageError> {
    let tx = conn.transaction()?;
    tx.execute(
        "INSERT INTO baselines
         (id, device_id, name, description, tags, created_at, app_version,
          plugin_versions, namespaces)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            baseline.id.as_str(),
            baseline.device_id.as_str(),
            baseline.name,
            baseline.description,
            json(&baseline.tags),
            baseline.created_at.to_rfc3339(),
            baseline.app_version,
            json(&baseline.plugin_versions),
            json(&baseline.namespaces),
        ],
    )?;
    for source in &baseline.sources {
        tx.execute(
            "INSERT INTO baseline_sources
             (baseline_id, run_id, run_started_at, mode, overall_health, manually_accepted)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                baseline.id.as_str(),
                source.run_id.as_str(),
                source.run_started_at.to_rfc3339(),
                source.mode,
                source.overall_health,
                i64::from(source.manually_accepted),
            ],
        )?;
    }
    for entity in &baseline.entities {
        tx.execute(
            "INSERT INTO baseline_entities
             (baseline_id, namespace, kind, entity_key, display_name, attributes,
              presence_count, presence_ratio, stability, numeric_summaries,
              observed_values, source_plugin, source_check, evidence_ids)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
            params![
                baseline.id.as_str(),
                entity.key.namespace,
                entity.key.kind,
                entity.key.key,
                entity.display_name,
                json(&entity.attributes),
                entity.presence_count as i64,
                entity.presence_ratio,
                enum_str(&entity.stability),
                json(&entity.numeric_summaries),
                json(&entity.observed_values),
                entity.source_plugin,
                entity.source_check,
                json(&entity.source_evidence_ids),
            ],
        )?;
    }
    tx.commit()?;
    Ok(())
}

pub fn get_baseline(conn: &Connection, id: &BaselineId) -> Result<Option<Baseline>, StorageError> {
    let header = conn
        .query_row(
            "SELECT device_id, name, description, tags, created_at, app_version,
                    plugin_versions, namespaces
             FROM baselines WHERE id = ?1",
            params![id.as_str()],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                ))
            },
        )
        .optional()?;
    let Some((device_id, name, description, tags, created_at, app_version, plugins, namespaces)) =
        header
    else {
        return Ok(None);
    };

    let mut stmt = conn.prepare(
        "SELECT run_id, run_started_at, mode, overall_health, manually_accepted
         FROM baseline_sources WHERE baseline_id = ?1 ORDER BY run_started_at",
    )?;
    let sources = stmt
        .query_map(params![id.as_str()], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, Option<String>>(3)?,
                row.get::<_, i64>(4)?,
            ))
        })?
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(|(run_id, started, mode, health, accepted)| {
            Ok(BaselineSource {
                run_id: RunId::from(run_id.as_str()),
                run_started_at: parse_ts(&started)?,
                mode,
                overall_health: health,
                manually_accepted: accepted != 0,
            })
        })
        .collect::<Result<Vec<_>, StorageError>>()?;

    let mut stmt = conn.prepare(
        "SELECT namespace, kind, entity_key, display_name, attributes, presence_count,
                presence_ratio, stability, numeric_summaries, observed_values,
                source_plugin, source_check, evidence_ids
         FROM baseline_entities WHERE baseline_id = ?1
         ORDER BY namespace, kind, entity_key",
    )?;
    let entities = stmt
        .query_map(params![id.as_str()], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, i64>(5)?,
                row.get::<_, f64>(6)?,
                row.get::<_, String>(7)?,
                row.get::<_, String>(8)?,
                row.get::<_, String>(9)?,
                row.get::<_, String>(10)?,
                row.get::<_, String>(11)?,
                row.get::<_, String>(12)?,
            ))
        })?
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(|row| {
            let (
                namespace,
                kind,
                key,
                display_name,
                attributes,
                count,
                ratio,
                stability,
                numeric,
                observed,
                plugin,
                check,
                evidence,
            ) = row;
            Ok(BaselineEntity {
                key: EntityKey::new(namespace, kind, key),
                display_name,
                attributes: parse(&attributes, "baseline attributes")?,
                presence_count: count as u32,
                presence_ratio: ratio,
                stability: enum_parse(&stability)?,
                numeric_summaries: parse(&numeric, "numeric summaries")?,
                observed_values: parse(&observed, "observed values")?,
                source_plugin: plugin,
                source_check: check,
                source_evidence_ids: parse(&evidence, "evidence ids")?,
            })
        })
        .collect::<Result<Vec<_>, StorageError>>()?;

    Ok(Some(Baseline {
        id: id.clone(),
        device_id: DeviceId::from(device_id.as_str()),
        name,
        description,
        tags: parse(&tags, "baseline tags")?,
        created_at: parse_ts(&created_at)?,
        sources,
        app_version,
        plugin_versions: parse(&plugins, "plugin versions")?,
        namespaces: parse(&namespaces, "baseline namespaces")?,
        entities,
    }))
}

/// Baseline headers for a device, newest first (no entity payloads).
pub fn list_baselines(
    conn: &Connection,
    device_id: Option<&str>,
) -> Result<Vec<crate::types::BaselineSummaryRow>, StorageError> {
    let mut stmt = conn.prepare(
        "SELECT b.id, b.device_id, b.name, b.description, b.created_at,
                (SELECT COUNT(*) FROM baseline_sources s WHERE s.baseline_id = b.id),
                (SELECT COUNT(*) FROM baseline_entities e WHERE e.baseline_id = b.id)
         FROM baselines b
         WHERE (?1 IS NULL OR b.device_id = ?1)
         ORDER BY b.created_at DESC",
    )?;
    let rows = stmt.query_map(params![device_id], |row| {
        Ok(crate::types::BaselineSummaryRow {
            id: row.get(0)?,
            device_id: row.get(1)?,
            name: row.get(2)?,
            description: row.get(3)?,
            created_at: row.get(4)?,
            source_count: row.get(5)?,
            entity_count: row.get(6)?,
        })
    })?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

/// Metadata may be edited; the captured technical snapshot may not.
pub fn update_baseline_metadata(
    conn: &Connection,
    id: &BaselineId,
    name: &str,
    description: &str,
    tags: &[String],
) -> Result<bool, StorageError> {
    let changed = conn.execute(
        "UPDATE baselines SET name = ?2, description = ?3, tags = ?4 WHERE id = ?1",
        params![id.as_str(), name, description, json(&tags)],
    )?;
    Ok(changed > 0)
}

pub fn delete_baseline(conn: &Connection, id: &BaselineId) -> Result<bool, StorageError> {
    let n = conn.execute("DELETE FROM baselines WHERE id = ?1", params![id.as_str()])?;
    Ok(n > 0)
}

// ── profiles ───────────────────────────────────────────────────────────

pub fn upsert_profile_revision(
    conn: &mut Connection,
    profile: &Profile,
    yaml: &str,
) -> Result<(), StorageError> {
    let tx = conn.transaction()?;
    tx.execute(
        "INSERT INTO profiles (id, name, description, tags, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5)
         ON CONFLICT(id) DO UPDATE SET name = excluded.name,
                                       description = excluded.description,
                                       tags = excluded.tags",
        params![
            profile.id.as_str(),
            profile.name,
            profile.description,
            json(&profile.tags),
            profile.created_at.to_rfc3339(),
        ],
    )?;
    tx.execute(
        "INSERT INTO profile_revisions
         (profile_id, revision, status, document, yaml, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
         ON CONFLICT(profile_id, revision) DO UPDATE SET
             status = excluded.status,
             document = excluded.document,
             yaml = excluded.yaml,
             updated_at = excluded.updated_at",
        params![
            profile.id.as_str(),
            profile.revision as i64,
            enum_str(&profile.status),
            json(profile),
            yaml,
            profile.created_at.to_rfc3339(),
            profile.updated_at.to_rfc3339(),
        ],
    )?;
    tx.commit()?;
    Ok(())
}

pub fn get_profile_revision(
    conn: &Connection,
    profile_id: &ProfileId,
    revision: u32,
) -> Result<Option<Profile>, StorageError> {
    let document: Option<String> = conn
        .query_row(
            "SELECT document FROM profile_revisions WHERE profile_id = ?1 AND revision = ?2",
            params![profile_id.as_str(), revision as i64],
            |row| row.get(0),
        )
        .optional()?;
    document
        .map(|doc| parse(&doc, "profile document"))
        .transpose()
}

/// Highest revision number for a profile, if it exists.
pub fn latest_revision(
    conn: &Connection,
    profile_id: &ProfileId,
) -> Result<Option<u32>, StorageError> {
    let value: Option<i64> = conn
        .query_row(
            "SELECT MAX(revision) FROM profile_revisions WHERE profile_id = ?1",
            params![profile_id.as_str()],
            |row| row.get(0),
        )
        .optional()?
        .flatten();
    Ok(value.map(|v| v as u32))
}

pub fn list_profiles(conn: &Connection) -> Result<Vec<ProfileRow>, StorageError> {
    let mut stmt = conn.prepare(
        "SELECT p.id, p.name, p.description, p.created_at,
                (SELECT MAX(revision) FROM profile_revisions r WHERE r.profile_id = p.id)
         FROM profiles p ORDER BY p.name",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok(ProfileRow {
            id: row.get(0)?,
            name: row.get(1)?,
            description: row.get(2)?,
            created_at: row.get(3)?,
            latest_revision: row.get::<_, Option<i64>>(4)?.unwrap_or(0) as u32,
        })
    })?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

pub fn list_profile_revisions(
    conn: &Connection,
    profile_id: &ProfileId,
) -> Result<Vec<ProfileRevisionRow>, StorageError> {
    let mut stmt = conn.prepare(
        "SELECT revision, status, created_at, updated_at,
                (SELECT COUNT(*) FROM evaluation_runs e
                  WHERE e.profile_id = profile_revisions.profile_id
                    AND e.profile_revision = profile_revisions.revision)
         FROM profile_revisions WHERE profile_id = ?1 ORDER BY revision DESC",
    )?;
    let rows = stmt.query_map(params![profile_id.as_str()], |row| {
        Ok(ProfileRevisionRow {
            revision: row.get::<_, i64>(0)? as u32,
            status: row.get(1)?,
            created_at: row.get(2)?,
            updated_at: row.get(3)?,
            evaluation_count: row.get(4)?,
        })
    })?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

pub fn set_revision_status(
    conn: &Connection,
    profile_id: &ProfileId,
    revision: u32,
    status: ProfileStatus,
) -> Result<bool, StorageError> {
    let n = conn.execute(
        "UPDATE profile_revisions SET status = ?3, updated_at = ?4
         WHERE profile_id = ?1 AND revision = ?2",
        params![
            profile_id.as_str(),
            revision as i64,
            enum_str(&status),
            Utc::now().to_rfc3339(),
        ],
    )?;
    Ok(n > 0)
}

/// A revision referenced by historical evaluations must never be deleted;
/// archive it instead.
pub fn delete_profile_revision(
    conn: &Connection,
    profile_id: &ProfileId,
    revision: u32,
) -> Result<bool, StorageError> {
    let referenced: i64 = conn.query_row(
        "SELECT COUNT(*) FROM evaluation_runs WHERE profile_id = ?1 AND profile_revision = ?2",
        params![profile_id.as_str(), revision as i64],
        |row| row.get(0),
    )?;
    if referenced > 0 {
        return Err(StorageError::Corrupt(format!(
            "profile revision {revision} is referenced by {referenced} evaluation run(s); archive it instead of deleting"
        )));
    }
    let n = conn.execute(
        "DELETE FROM profile_revisions WHERE profile_id = ?1 AND revision = ?2",
        params![profile_id.as_str(), revision as i64],
    )?;
    Ok(n > 0)
}

pub fn set_assignment(
    conn: &Connection,
    assignment: &DeviceProfileAssignment,
) -> Result<(), StorageError> {
    conn.execute(
        "INSERT INTO device_profile_assignments
         (device_id, profile_id, profile_revision, activated_at, runtime_id)
         VALUES (?1, ?2, ?3, ?4, ?5)
         ON CONFLICT(device_id) DO UPDATE SET
             profile_id = excluded.profile_id,
             profile_revision = excluded.profile_revision,
             activated_at = excluded.activated_at,
             runtime_id = excluded.runtime_id",
        params![
            assignment.device_id.as_str(),
            assignment.profile_id.as_str(),
            assignment.profile_revision as i64,
            assignment.activated_at.to_rfc3339(),
            assignment.runtime_id,
        ],
    )?;
    Ok(())
}

pub fn get_assignment(
    conn: &Connection,
    device_id: &DeviceId,
) -> Result<Option<DeviceProfileAssignment>, StorageError> {
    let row = conn
        .query_row(
            "SELECT profile_id, profile_revision, activated_at, runtime_id
             FROM device_profile_assignments WHERE device_id = ?1",
            params![device_id.as_str()],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                ))
            },
        )
        .optional()?;
    row.map(|(profile_id, revision, activated, runtime)| {
        Ok(DeviceProfileAssignment {
            device_id: device_id.clone(),
            profile_id: ProfileId::from(profile_id.as_str()),
            profile_revision: revision as u32,
            activated_at: parse_ts(&activated)?,
            runtime_id: runtime,
        })
    })
    .transpose()
}

// ── evaluations ────────────────────────────────────────────────────────

pub fn insert_evaluation(
    conn: &mut Connection,
    evaluation: &EvaluationRun,
) -> Result<(), StorageError> {
    let tx = conn.transaction()?;
    tx.execute(
        "INSERT INTO evaluation_runs
         (id, device_id, diagnostic_run_id, profile_id, profile_revision, baseline_id,
          started_at, finished_at, app_version, plugin_versions,
          satisfied, unsatisfied, unknown, not_applicable)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
        params![
            evaluation.id,
            evaluation.device_id.as_str(),
            evaluation.diagnostic_run_id.as_str(),
            evaluation.profile_id.as_str(),
            evaluation.profile_revision as i64,
            evaluation
                .baseline_id
                .as_ref()
                .map(|b| b.as_str().to_owned()),
            evaluation.started_at.to_rfc3339(),
            evaluation.finished_at.to_rfc3339(),
            evaluation.app_version,
            json(&evaluation.plugin_versions),
            evaluation.satisfied() as i64,
            evaluation.unsatisfied() as i64,
            evaluation.unknown() as i64,
            evaluation.not_applicable() as i64,
        ],
    )?;
    for result in &evaluation.results {
        tx.execute(
            "INSERT INTO expectation_results
             (evaluation_run_id, expectation_id, description, namespace, kind, status,
              evaluated_entities, actual, expected, reason, evidence_ids, evaluated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                evaluation.id,
                result.expectation_id,
                result.description,
                result.namespace,
                result.kind,
                enum_str(&result.status),
                json(&result.evaluated_entities),
                result.actual.as_ref().map(json),
                result.expected,
                result.reason,
                json(&result.evidence_ids),
                result.evaluated_at.to_rfc3339(),
            ],
        )?;
    }
    tx.commit()?;
    Ok(())
}

pub fn get_evaluation(conn: &Connection, id: &str) -> Result<Option<EvaluationRun>, StorageError> {
    let header = conn
        .query_row(
            "SELECT device_id, diagnostic_run_id, profile_id, profile_revision, baseline_id,
                    started_at, finished_at, app_version, plugin_versions
             FROM evaluation_runs WHERE id = ?1",
            params![id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, i64>(3)?,
                    row.get::<_, Option<String>>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, String>(8)?,
                ))
            },
        )
        .optional()?;
    let Some((device, run, profile, revision, baseline, started, finished, app, plugins)) = header
    else {
        return Ok(None);
    };

    let mut stmt = conn.prepare(
        "SELECT expectation_id, description, namespace, kind, status, evaluated_entities,
                actual, expected, reason, evidence_ids, evaluated_at
         FROM expectation_results WHERE evaluation_run_id = ?1 ORDER BY expectation_id",
    )?;
    let results = stmt
        .query_map(params![id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, Option<String>>(6)?,
                row.get::<_, String>(7)?,
                row.get::<_, String>(8)?,
                row.get::<_, String>(9)?,
                row.get::<_, String>(10)?,
            ))
        })?
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(|row| {
            let (
                expectation_id,
                description,
                namespace,
                kind,
                status,
                entities,
                actual,
                expected,
                reason,
                evidence,
                at,
            ) = row;
            Ok(ExpectationResult {
                expectation_id,
                description,
                namespace,
                kind,
                status: enum_parse(&status)?,
                evaluated_entities: parse(&entities, "evaluated entities")?,
                actual: actual.map(|a| parse(&a, "actual value")).transpose()?,
                expected,
                reason,
                evidence_ids: parse(&evidence, "evidence ids")?,
                evaluated_at: parse_ts(&at)?,
            })
        })
        .collect::<Result<Vec<_>, StorageError>>()?;

    Ok(Some(EvaluationRun {
        id: id.to_owned(),
        device_id: DeviceId::from(device.as_str()),
        diagnostic_run_id: RunId::from(run.as_str()),
        profile_id: ProfileId::from(profile.as_str()),
        profile_revision: revision as u32,
        baseline_id: baseline.map(|b| BaselineId::from(b.as_str())),
        started_at: parse_ts(&started)?,
        finished_at: parse_ts(&finished)?,
        app_version: app,
        plugin_versions: parse(&plugins, "plugin versions")?,
        results,
    }))
}

/// Most recent evaluation for a diagnostic run, if any.
pub fn evaluation_for_run(
    conn: &Connection,
    run_id: &RunId,
) -> Result<Option<EvaluationRun>, StorageError> {
    let id: Option<String> = conn
        .query_row(
            "SELECT id FROM evaluation_runs WHERE diagnostic_run_id = ?1
             ORDER BY started_at DESC LIMIT 1",
            params![run_id.as_str()],
            |row| row.get(0),
        )
        .optional()?;
    match id {
        Some(id) => get_evaluation(conn, &id),
        None => Ok(None),
    }
}

// ── stored diffs ───────────────────────────────────────────────────────

pub fn insert_diff(conn: &Connection, id: &str, diff: &BaselineDiff) -> Result<(), StorageError> {
    conn.execute(
        "INSERT INTO comparison_diffs
         (id, device_id, baseline_id, diagnostic_run_id, compared_at, compatibility,
          notes, entities)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            id,
            diff.device_id.as_str(),
            diff.baseline_id.as_str(),
            diff.diagnostic_run_id.as_str(),
            diff.compared_at.to_rfc3339(),
            enum_str(&diff.compatibility),
            json(&diff.compatibility_notes),
            json(&diff.entities),
        ],
    )?;
    Ok(())
}
