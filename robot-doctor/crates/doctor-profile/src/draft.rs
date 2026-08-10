//! Conservative profile drafting from a baseline (Phase C2).
//!
//! Drafting is a *suggestion engine*, never an authority. It produces a
//! DRAFT profile the user reviews and edits; nothing here is ever
//! activated automatically.
//!
//! The rules are deliberately timid, because a wrong suggestion that a
//! user accepts becomes a permanent false expectation:
//!
//! - Only entities present in **every** source run become required
//!   candidates.
//! - Entities seen in some runs become optional candidates, unselected.
//! - Entities seen in exactly one run of a multi-run baseline are
//!   transient candidates: suggested only as EXISTS/optional, never with
//!   a numeric threshold.
//! - A numeric threshold from a **single sample** is never proposed as a
//!   hard bound; multi-run ranges are proposed and explicitly marked as
//!   suggested so the UI can show where the number came from.

use doctor_domain::baseline::{Baseline, BaselineEntity, EntityStability};
use doctor_domain::comparison::AttributeValue;
use doctor_domain::profile::{
    Constraint, Expectation, Profile, ProfileStatus, Requirement, Selector, PROFILE_SCHEMA_VERSION,
};
use doctor_domain::ProfileId;

/// Why an expectation was suggested, and whether it starts selected.
#[derive(Debug, Clone, PartialEq)]
pub struct DraftSuggestion {
    pub expectation: Expectation,
    /// Pre-ticked in the review UI. Only fully stable subjects are.
    pub selected_by_default: bool,
    /// Plain-language provenance shown next to the checkbox.
    pub rationale: String,
    /// True when a numeric bound was derived from observed values rather
    /// than stated by the user.
    pub suggested_threshold: bool,
}

/// The result of drafting: suggestions plus what was deliberately skipped.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct DraftReport {
    pub suggestions: Vec<DraftSuggestion>,
    /// Entities intentionally not turned into expectations, with reasons.
    pub skipped: Vec<SkippedEntity>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SkippedEntity {
    pub key: String,
    pub reason: String,
}

impl DraftReport {
    pub fn selected(&self) -> impl Iterator<Item = &DraftSuggestion> {
        self.suggestions.iter().filter(|s| s.selected_by_default)
    }

    /// Build a DRAFT profile from the suggestions the user accepted.
    pub fn into_profile(
        &self,
        id: ProfileId,
        name: String,
        description: String,
        accepted_ids: &[String],
    ) -> Profile {
        let now = chrono::Utc::now();
        let mut expectations: Vec<Expectation> = self
            .suggestions
            .iter()
            .filter(|s| accepted_ids.contains(&s.expectation.id))
            .map(|s| s.expectation.clone())
            .collect();
        expectations.sort_by(|a, b| a.id.cmp(&b.id));
        Profile {
            schema_version: PROFILE_SCHEMA_VERSION,
            id,
            name,
            description,
            revision: 1,
            // Never ACTIVE: a draft is reviewed before it can judge
            // anything.
            status: ProfileStatus::Draft,
            created_at: now,
            updated_at: now,
            tags: vec![],
            expectations,
        }
    }
}

/// Attributes worth proposing numeric bounds for. Everything else is
/// captured as an existence expectation only — a bound on a value nobody
/// asked about is noise that will fire later for no reason.
const BOUNDED_ATTRIBUTES: &[&str] = &[
    "observed_hz",
    "publisher_count",
    "subscriber_count",
    "logical_cores",
    "total_bytes",
    "mtu",
];

/// Attributes whose exact value is worth pinning with equality.
const PINNED_ATTRIBUTES: &[&str] = &[
    "type",
    "distro",
    "rmw",
    "domain_id",
    "architecture",
    "state_label",
];

/// Draft suggestions from a baseline.
pub fn draft(baseline: &Baseline) -> DraftReport {
    let multi_run = baseline.source_count() > 1;
    let mut report = DraftReport::default();

    for entity in &baseline.entities {
        match entity.stability {
            EntityStability::Stable => {
                suggest_for(&mut report, entity, Requirement::Required, true, multi_run);
            }
            EntityStability::Variable => {
                // Present in some runs: real, but not dependable. Offered
                // as optional and left unticked.
                suggest_for(&mut report, entity, Requirement::Optional, false, multi_run);
            }
            EntityStability::TransientCandidate => {
                // Seen exactly once across several runs. Existence only,
                // optional, unticked — never a threshold.
                report.suggestions.push(existence(
                    entity,
                    Requirement::Optional,
                    false,
                    format!(
                        "seen in only 1 of {} known-good runs — likely transient",
                        baseline.source_count()
                    ),
                ));
                report.skipped.push(SkippedEntity {
                    key: entity.key.canonical(),
                    reason: "transient: no value expectations proposed".into(),
                });
            }
        }
    }

    report
        .suggestions
        .sort_by(|a, b| a.expectation.id.cmp(&b.expectation.id));
    report.skipped.sort_by(|a, b| a.key.cmp(&b.key));
    report
}

fn suggest_for(
    report: &mut DraftReport,
    entity: &BaselineEntity,
    requirement: Requirement,
    selected: bool,
    multi_run: bool,
) {
    let rationale = if selected {
        "present in every known-good run".to_owned()
    } else {
        format!(
            "present in {:.0}% of known-good runs",
            entity.presence_ratio * 100.0
        )
    };
    report
        .suggestions
        .push(existence(entity, requirement, selected, rationale));

    // Value expectations are only ever proposed for subjects that are
    // themselves dependable.
    if requirement != Requirement::Required {
        return;
    }

    for (name, value) in &entity.attributes {
        if PINNED_ATTRIBUTES.contains(&name.as_str()) {
            if let Some(text) = pinnable(value) {
                report.suggestions.push(DraftSuggestion {
                    expectation: Expectation {
                        id: format!("{}::{name}", entity.key.canonical()),
                        description: format!("{} {name} is {text}", entity.display_name),
                        namespace: entity.key.namespace.clone(),
                        kind: entity.key.kind.clone(),
                        selector: exact(&entity.key.key),
                        requirement: Requirement::Required,
                        constraint: Constraint::Equals {
                            field: name.clone(),
                            value: doctor_domain::profile::ConstraintValue::Text(text),
                        },
                        from_baseline: true,
                    },
                    // Pinning an exact value is a real commitment; the
                    // user opts in.
                    selected_by_default: false,
                    rationale: "value was identical in every known-good run".into(),
                    suggested_threshold: false,
                });
            }
            continue;
        }

        if !BOUNDED_ATTRIBUTES.contains(&name.as_str()) {
            continue;
        }
        let Some(summary) = entity.numeric_summaries.get(name) else {
            continue;
        };
        if !multi_run || summary.samples < 2 {
            // One sample is an anecdote, not a range. Record why no
            // threshold was proposed instead of inventing one.
            report.skipped.push(SkippedEntity {
                key: format!("{}::{name}", entity.key.canonical()),
                reason: "only one sample — no threshold proposed".into(),
            });
            continue;
        }
        report.suggestions.push(DraftSuggestion {
            expectation: Expectation {
                id: format!("{}::{name}_min", entity.key.canonical()),
                description: format!(
                    "{} {name} stays at or above the known-good minimum",
                    entity.display_name
                ),
                namespace: entity.key.namespace.clone(),
                kind: entity.key.kind.clone(),
                selector: exact(&entity.key.key),
                requirement: Requirement::Required,
                constraint: Constraint::Min {
                    field: name.clone(),
                    value: summary.min,
                },
                from_baseline: true,
            },
            selected_by_default: false,
            rationale: format!(
                "observed {:.4}–{:.4} across {} known-good samples (median {:.4})",
                summary.min, summary.max, summary.samples, summary.median
            ),
            suggested_threshold: true,
        });
    }
}

fn existence(
    entity: &BaselineEntity,
    requirement: Requirement,
    selected: bool,
    rationale: String,
) -> DraftSuggestion {
    DraftSuggestion {
        expectation: Expectation {
            id: entity.key.canonical(),
            description: format!("{} is present", entity.display_name),
            namespace: entity.key.namespace.clone(),
            kind: entity.key.kind.clone(),
            selector: exact(&entity.key.key),
            requirement,
            constraint: Constraint::Exists,
            from_baseline: true,
        },
        selected_by_default: selected,
        rationale,
        suggested_threshold: false,
    }
}

fn exact(key: &str) -> Selector {
    Selector {
        key: Some(key.to_owned()),
        key_prefix: None,
    }
}

fn pinnable(value: &AttributeValue) -> Option<String> {
    match value {
        AttributeValue::Text(text) => Some(text.clone()),
        AttributeValue::Number(n) => Some(AttributeValue::Number(*n).render()),
        // Sets and bools are not pinned automatically: a set changes for
        // benign reasons (a second IP address), and a bool expectation is
        // clearer when the user writes it.
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use doctor_domain::baseline::{BaselineSource, NumericSummary};
    use doctor_domain::comparison::EntityKey;
    use doctor_domain::{BaselineId, DeviceId, RunId};
    use std::collections::{BTreeMap, BTreeSet};

    fn entity(
        kind: &str,
        key: &str,
        stability: EntityStability,
        presence: (u32, f64),
    ) -> BaselineEntity {
        BaselineEntity {
            key: EntityKey::new("ros", kind, key),
            display_name: key.into(),
            attributes: BTreeMap::new(),
            presence_count: presence.0,
            presence_ratio: presence.1,
            stability,
            numeric_summaries: BTreeMap::new(),
            observed_values: BTreeMap::new(),
            source_plugin: "ros2".into(),
            source_check: "ros.graph".into(),
            source_evidence_ids: vec![],
        }
    }

    fn baseline(sources: u32, entities: Vec<BaselineEntity>) -> Baseline {
        Baseline {
            id: BaselineId::from("b1"),
            device_id: DeviceId::from("local"),
            name: "known good".into(),
            description: String::new(),
            tags: vec![],
            created_at: Utc::now(),
            sources: (0..sources)
                .map(|i| BaselineSource {
                    run_id: RunId::from(format!("r{i}").as_str()),
                    run_started_at: Utc::now(),
                    mode: "FULL".into(),
                    overall_health: Some("HEALTHY".into()),
                    manually_accepted: false,
                })
                .collect(),
            app_version: "0.1.0".into(),
            plugin_versions: BTreeMap::new(),
            namespaces: BTreeMap::new(),
            entities,
        }
    }

    #[test]
    fn stable_entities_become_required_and_preselected() {
        let report = draft(&baseline(
            3,
            vec![entity("node", "/talker", EntityStability::Stable, (3, 1.0))],
        ));
        let suggestion = &report.suggestions[0];
        assert_eq!(suggestion.expectation.requirement, Requirement::Required);
        assert!(suggestion.selected_by_default);
        assert!(suggestion.expectation.from_baseline);
        assert_eq!(suggestion.expectation.constraint, Constraint::Exists);
    }

    #[test]
    fn variable_entities_are_optional_and_unticked() {
        let report = draft(&baseline(
            4,
            vec![entity(
                "node",
                "/flaky",
                EntityStability::Variable,
                (2, 0.5),
            )],
        ));
        let suggestion = &report.suggestions[0];
        assert_eq!(suggestion.expectation.requirement, Requirement::Optional);
        assert!(!suggestion.selected_by_default);
        assert!(suggestion.rationale.contains("50%"));
    }

    #[test]
    fn transient_entities_are_never_silently_required() {
        let report = draft(&baseline(
            5,
            vec![entity(
                "node",
                "/one_shot",
                EntityStability::TransientCandidate,
                (1, 0.2),
            )],
        ));
        assert_eq!(report.suggestions.len(), 1);
        let suggestion = &report.suggestions[0];
        assert_eq!(suggestion.expectation.requirement, Requirement::Optional);
        assert!(!suggestion.selected_by_default);
        assert!(report.suggestions.iter().all(|s| !s.suggested_threshold));
        assert!(report
            .skipped
            .iter()
            .any(|s| s.reason.contains("transient")));
    }

    #[test]
    fn single_sample_numeric_produces_no_threshold() {
        let mut topic = entity("topic_rate", "/scan", EntityStability::Stable, (1, 1.0));
        topic
            .attributes
            .insert("observed_hz".into(), AttributeValue::Number(10.0));
        topic.numeric_summaries.insert(
            "observed_hz".into(),
            NumericSummary {
                min: 10.0,
                max: 10.0,
                median: 10.0,
                samples: 1,
            },
        );
        let report = draft(&baseline(1, vec![topic]));
        assert!(
            report.suggestions.iter().all(|s| !s.suggested_threshold),
            "a single sample must not become a hard bound"
        );
        assert!(report
            .skipped
            .iter()
            .any(|s| s.reason.contains("one sample")));
    }

    #[test]
    fn multi_run_range_is_suggested_and_marked() {
        let mut topic = entity("topic_rate", "/scan", EntityStability::Stable, (4, 1.0));
        topic
            .attributes
            .insert("observed_hz".into(), AttributeValue::Number(9.6));
        topic.numeric_summaries.insert(
            "observed_hz".into(),
            NumericSummary {
                min: 9.2,
                max: 10.4,
                median: 9.8,
                samples: 4,
            },
        );
        let report = draft(&baseline(4, vec![topic]));
        let threshold = report
            .suggestions
            .iter()
            .find(|s| s.suggested_threshold)
            .expect("range suggestion");
        assert_eq!(
            threshold.expectation.constraint,
            Constraint::Min {
                field: "observed_hz".into(),
                value: 9.2
            }
        );
        // Suggested, never pre-accepted.
        assert!(!threshold.selected_by_default);
        assert!(threshold.rationale.contains("4 known-good samples"));
    }

    #[test]
    fn pinned_values_are_offered_but_not_preselected() {
        let mut runtime = entity("runtime", "default", EntityStability::Stable, (2, 1.0));
        runtime
            .attributes
            .insert("distro".into(), AttributeValue::text("jazzy"));
        runtime
            .observed_values
            .insert("distro".into(), BTreeSet::from(["jazzy".to_owned()]));
        let report = draft(&baseline(2, vec![runtime]));
        let pin = report
            .suggestions
            .iter()
            .find(|s| matches!(s.expectation.constraint, Constraint::Equals { .. }))
            .expect("equality suggestion");
        assert!(!pin.selected_by_default);
    }

    #[test]
    fn drafted_profile_is_never_active() {
        let report = draft(&baseline(
            2,
            vec![entity("node", "/talker", EntityStability::Stable, (2, 1.0))],
        ));
        let accepted: Vec<String> = report
            .selected()
            .map(|s| s.expectation.id.clone())
            .collect();
        let profile = report.into_profile(
            ProfileId::from("p1"),
            "drafted".into(),
            String::new(),
            &accepted,
        );
        assert_eq!(profile.status, ProfileStatus::Draft);
        assert_eq!(profile.revision, 1);
        assert_eq!(profile.expectations.len(), 1);
        assert!(profile.expectations.iter().all(|e| e.from_baseline));
    }
}
