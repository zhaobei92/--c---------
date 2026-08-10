//! Baseline capture and semantic diff (Phase C2).
//!
//! Capture always works from *already persisted* run projections, so what
//! the user approved is exactly what becomes the baseline, and nothing is
//! silently re-run. The diff is a statement of difference — it never
//! decides health.

use doctor_domain::baseline::{
    Baseline, BaselineAttributeDiff, BaselineCompatibility, BaselineDiff, BaselineDiffEntity,
    BaselineEntity, BaselineSource, CompatibilityNote, EntityDiffState, EntityStability,
    NumericSummary, RunProjection,
};
use doctor_domain::comparison::{
    AttributeValue, ComparisonEntity, EntityKey, NamespaceAvailability,
};
use doctor_domain::{BaselineId, DeviceId};
use std::collections::{BTreeMap, BTreeSet};

/// Everything capture needs about one approved run.
pub struct CaptureInput {
    pub source: BaselineSource,
    pub projection: RunProjection,
}

/// Warnings a user should see before approving a run as known-good.
/// Capture is never blocked — the user is the authority (§11).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EligibilityWarning {
    pub code: String,
    pub message: String,
}

/// Inspect a run for conditions worth confirming before capture.
pub fn eligibility_warnings(
    run: &doctor_domain::CheckRun,
    projection: &RunProjection,
    persisted: bool,
) -> Vec<EligibilityWarning> {
    let mut warnings = Vec::new();
    if !persisted {
        warnings.push(EligibilityWarning {
            code: "INCOMPLETE_PERSISTENCE".into(),
            message: "This run was not fully persisted; the baseline may be incomplete.".into(),
        });
    }
    if run.finished_at.is_none() {
        warnings.push(EligibilityWarning {
            code: "RUN_UNFINISHED".into(),
            message: "This run never reported completion.".into(),
        });
    }
    let cancelled = run
        .results
        .iter()
        .filter(|r| r.status == doctor_domain::CheckStatus::Cancelled)
        .count();
    if cancelled > 0 {
        warnings.push(EligibilityWarning {
            code: "CANCELLED_CHECKS".into(),
            message: format!("{cancelled} check(s) were cancelled and observed nothing."),
        });
    }
    let critical = run
        .results
        .iter()
        .filter(|r| {
            matches!(
                r.status,
                doctor_domain::CheckStatus::Error | doctor_domain::CheckStatus::Timeout
            )
        })
        .count();
    if critical > 0 {
        warnings.push(EligibilityWarning {
            code: "PLUGIN_FAILURES".into(),
            message: format!("{critical} check(s) failed to execute (error or timeout)."),
        });
    }
    let blind: Vec<&String> = projection
        .namespaces
        .iter()
        .filter(|(_, state)| **state == NamespaceAvailability::NotObserved)
        .map(|(name, _)| name)
        .collect();
    if !blind.is_empty() {
        warnings.push(EligibilityWarning {
            code: "NAMESPACE_NOT_OBSERVED".into(),
            message: format!(
                "These capabilities could not be observed and will be missing from the baseline: {}",
                blind
                    .iter()
                    .map(|n| n.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        });
    }
    warnings
}

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(f64::total_cmp);
    let mid = values.len() / 2;
    if values.len().is_multiple_of(2) {
        (values[mid - 1] + values[mid]) / 2.0
    } else {
        values[mid]
    }
}

/// Capture an immutable baseline from one or more approved runs.
///
/// A single-run baseline is the degenerate case of the same aggregation:
/// every entity gets `presence_count = 1`, `presence_ratio = 1.0`.
pub fn capture(
    id: BaselineId,
    device_id: DeviceId,
    name: String,
    description: String,
    app_version: String,
    plugin_versions: BTreeMap<String, String>,
    inputs: Vec<CaptureInput>,
) -> Baseline {
    let source_count = inputs.len().max(1) as u32;

    // Union of namespaces: a namespace observed in any source counts as
    // captured, so a single flaky run cannot erase it.
    let mut namespaces: BTreeMap<String, NamespaceAvailability> = BTreeMap::new();
    for input in &inputs {
        for (name, state) in &input.projection.namespaces {
            let entry = namespaces
                .entry(name.clone())
                .or_insert(NamespaceAvailability::Unsupported);
            if *state == NamespaceAvailability::Observed
                || *entry == NamespaceAvailability::Unsupported
            {
                *entry = *state;
            }
        }
    }

    // Aggregate per entity key across runs.
    struct Acc {
        latest: ComparisonEntity,
        count: u32,
        numeric: BTreeMap<String, Vec<f64>>,
        values: BTreeMap<String, BTreeSet<String>>,
    }
    let mut acc: BTreeMap<EntityKey, Acc> = BTreeMap::new();
    for input in &inputs {
        for entity in &input.projection.entities {
            let slot = acc.entry(entity.key.clone()).or_insert_with(|| Acc {
                latest: entity.clone(),
                count: 0,
                numeric: BTreeMap::new(),
                values: BTreeMap::new(),
            });
            slot.count += 1;
            slot.latest = entity.clone();
            for (name, value) in &entity.attributes {
                match value {
                    AttributeValue::Number(n) => {
                        slot.numeric.entry(name.clone()).or_default().push(*n);
                    }
                    other => {
                        slot.values
                            .entry(name.clone())
                            .or_default()
                            .insert(other.render());
                    }
                }
            }
        }
    }

    let entities = acc
        .into_iter()
        .map(|(key, mut slot)| {
            let ratio = slot.count as f64 / source_count as f64;
            let stability = if slot.count == source_count {
                EntityStability::Stable
            } else if slot.count == 1 && source_count > 2 {
                EntityStability::TransientCandidate
            } else {
                EntityStability::Variable
            };
            let numeric_summaries = slot
                .numeric
                .iter_mut()
                .map(|(name, values)| {
                    let min = values.iter().cloned().fold(f64::INFINITY, f64::min);
                    let max = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
                    (
                        name.clone(),
                        NumericSummary {
                            min,
                            max,
                            median: median(values),
                            samples: values.len() as u32,
                        },
                    )
                })
                .collect();
            BaselineEntity {
                key,
                display_name: slot.latest.display_name.clone(),
                attributes: slot.latest.attributes.clone(),
                presence_count: slot.count,
                presence_ratio: ratio,
                stability,
                numeric_summaries,
                observed_values: slot.values,
                source_plugin: slot.latest.source_plugin.to_string(),
                source_check: slot.latest.source_check.to_string(),
                source_evidence_ids: slot
                    .latest
                    .source_evidence_ids
                    .iter()
                    .map(|id| id.to_string())
                    .collect(),
            }
        })
        .collect();

    Baseline {
        id,
        device_id,
        name,
        description,
        tags: Vec::new(),
        created_at: chrono::Utc::now(),
        sources: inputs.into_iter().map(|i| i.source).collect(),
        app_version,
        plugin_versions,
        namespaces,
        entities,
    }
}

/// Compare a baseline against a current run projection.
pub fn diff(baseline: &Baseline, current: &RunProjection) -> BaselineDiff {
    let mut entities = Vec::new();
    let current_by_key: BTreeMap<&EntityKey, &ComparisonEntity> =
        current.entities.iter().map(|e| (&e.key, e)).collect();

    // Baseline side: unchanged / changed / removed / unavailable / unknown.
    for baseline_entity in &baseline.entities {
        let availability = current.availability(&baseline_entity.key.namespace);
        match current_by_key.get(&baseline_entity.key) {
            Some(current_entity) => {
                let attribute_diffs =
                    attribute_diffs(&baseline_entity.attributes, &current_entity.attributes);
                entities.push(BaselineDiffEntity {
                    key: baseline_entity.key.clone(),
                    display_name: baseline_entity.display_name.clone(),
                    state: if attribute_diffs.is_empty() {
                        EntityDiffState::Unchanged
                    } else {
                        EntityDiffState::Changed
                    },
                    attribute_diffs,
                    baseline_presence_ratio: Some(baseline_entity.presence_ratio),
                    reason: None,
                });
            }
            None => {
                // Absence only means REMOVED when we actually looked.
                let (state, reason) = match availability {
                    NamespaceAvailability::Observed => (EntityDiffState::Removed, None),
                    NamespaceAvailability::NotObserved => (
                        EntityDiffState::Unavailable,
                        Some(
                            current
                                .namespace_reasons
                                .get(&baseline_entity.key.namespace)
                                .cloned()
                                .unwrap_or_else(|| {
                                    format!(
                                        "the '{}' capability could not be observed in this run",
                                        baseline_entity.key.namespace
                                    )
                                }),
                        ),
                    ),
                    NamespaceAvailability::Unsupported => (
                        EntityDiffState::Unknown,
                        Some(format!(
                            "no plugin in this run projects the '{}' namespace",
                            baseline_entity.key.namespace
                        )),
                    ),
                };
                entities.push(BaselineDiffEntity {
                    key: baseline_entity.key.clone(),
                    display_name: baseline_entity.display_name.clone(),
                    state,
                    attribute_diffs: vec![],
                    baseline_presence_ratio: Some(baseline_entity.presence_ratio),
                    reason,
                });
            }
        }
    }

    // Current side: anything the baseline never had is ADDED.
    let baseline_keys: BTreeSet<&EntityKey> = baseline.entities.iter().map(|e| &e.key).collect();
    for entity in &current.entities {
        if !baseline_keys.contains(&entity.key) {
            entities.push(BaselineDiffEntity {
                key: entity.key.clone(),
                display_name: entity.display_name.clone(),
                state: EntityDiffState::Added,
                attribute_diffs: vec![],
                baseline_presence_ratio: None,
                reason: None,
            });
        }
    }

    // Deterministic ordering regardless of how either side was produced.
    entities.sort_by(|a, b| a.key.cmp(&b.key));

    let (compatibility, notes) = compatibility(baseline, current);
    BaselineDiff {
        baseline_id: baseline.id.clone(),
        device_id: baseline.device_id.clone(),
        diagnostic_run_id: current.run_id.clone(),
        compared_at: chrono::Utc::now(),
        compatibility,
        compatibility_notes: notes,
        entities,
    }
}

fn attribute_diffs(
    baseline: &BTreeMap<String, AttributeValue>,
    current: &BTreeMap<String, AttributeValue>,
) -> Vec<BaselineAttributeDiff> {
    let names: BTreeSet<&String> = baseline.keys().chain(current.keys()).collect();
    names
        .into_iter()
        .filter_map(|name| {
            let before = baseline.get(name);
            let after = current.get(name);
            if before == after {
                return None;
            }
            let (absolute, relative) = match (before, after) {
                (Some(AttributeValue::Number(a)), Some(AttributeValue::Number(b))) => {
                    let delta = b - a;
                    let relative = if *a != 0.0 { Some(delta / a) } else { None };
                    (Some(delta), relative)
                }
                _ => (None, None),
            };
            Some(BaselineAttributeDiff {
                attribute: name.clone(),
                baseline_value: before.cloned(),
                current_value: after.cloned(),
                absolute_delta: absolute,
                relative_delta: relative,
            })
        })
        .collect()
}

/// Detect changes that make a diff misleading rather than informative.
///
/// Generic by construction: entities of kind `runtime` are compared
/// attribute-wise, so a plugin declaring its runtime identity gets
/// compatibility checking without core knowing what ROS or CUDA are.
fn compatibility(
    baseline: &Baseline,
    current: &RunProjection,
) -> (BaselineCompatibility, Vec<CompatibilityNote>) {
    let mut notes = Vec::new();
    let mut verdict = BaselineCompatibility::Compatible;

    if baseline.device_id != current.device_id {
        notes.push(CompatibilityNote {
            code: "DEVICE_MISMATCH".into(),
            message: format!(
                "baseline was captured on device '{}' but this run is from '{}'",
                baseline.device_id, current.device_id
            ),
        });
        return (BaselineCompatibility::Incompatible, notes);
    }

    // Namespaces the baseline captured but this run cannot compare.
    for (namespace, state) in &baseline.namespaces {
        if *state != NamespaceAvailability::Observed {
            continue;
        }
        match current.availability(namespace) {
            NamespaceAvailability::Observed => {}
            NamespaceAvailability::NotObserved => {
                verdict = BaselineCompatibility::PartiallyCompatible;
                notes.push(CompatibilityNote {
                    code: "NAMESPACE_NOT_OBSERVED".into(),
                    message: format!(
                        "'{namespace}' was captured in the baseline but could not be observed now"
                    ),
                });
            }
            NamespaceAvailability::Unsupported => {
                verdict = BaselineCompatibility::PartiallyCompatible;
                notes.push(CompatibilityNote {
                    code: "PROJECTION_UNSUPPORTED".into(),
                    message: format!(
                        "'{namespace}' was captured in the baseline but no plugin projects it now"
                    ),
                });
            }
        }
    }

    // Runtime identity drift (ROS distro/domain, driver generation, …).
    for baseline_entity in baseline.entities.iter().filter(|e| e.key.kind == "runtime") {
        let Some(current_entity) = current.entity(&baseline_entity.key) else {
            continue;
        };
        for diff in attribute_diffs(&baseline_entity.attributes, &current_entity.attributes) {
            verdict = match verdict {
                BaselineCompatibility::Incompatible => BaselineCompatibility::Incompatible,
                _ => BaselineCompatibility::PartiallyCompatible,
            };
            notes.push(CompatibilityNote {
                code: "RUNTIME_CHANGED".into(),
                message: format!(
                    "{} {} changed: {} -> {}",
                    baseline_entity.key.namespace,
                    diff.attribute,
                    diff.baseline_value
                        .as_ref()
                        .map(|v| v.render())
                        .unwrap_or_else(|| "(absent)".into()),
                    diff.current_value
                        .as_ref()
                        .map(|v| v.render())
                        .unwrap_or_else(|| "(absent)".into()),
                ),
            });
        }
    }

    (verdict, notes)
}
