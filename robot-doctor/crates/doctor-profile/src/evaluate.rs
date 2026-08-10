//! Generic expectation evaluation (Phase C2).
//!
//! Answers exactly one question: *are the explicit expectations in this
//! profile satisfied by what this run observed?* It never asks why
//! something is broken, never emits a Finding or a root cause, and never
//! computes a compliance score.
//!
//! The single most important rule here is the difference between
//! **UNSATISFIED** and **UNKNOWN**:
//!
//! - UNSATISFIED means Robot Doctor looked at the subject and the
//!   constraint did not hold — including "we successfully observed that
//!   nothing of this kind exists".
//! - UNKNOWN means Robot Doctor could not look, or could not read the
//!   value it needed. Absence of evidence is never evidence of absence.
//!
//! That distinction is decided by [`RunProjection::availability_for`],
//! which plugins drive by declaring the entity kinds each check is
//! authoritative for.

use chrono::{DateTime, Utc};
use doctor_domain::baseline::RunProjection;
use doctor_domain::comparison::{
    AttributeValue, ComparisonEntity, EntityKey, NamespaceAvailability,
};
use doctor_domain::evaluation::{EvaluationRun, ExpectationResult, ExpectationStatus};
use doctor_domain::profile::{
    Constraint, Expectation, Profile, RelationshipMode, Requirement, Selector,
};
use doctor_domain::{BaselineId, DeviceId};
use std::collections::{BTreeMap, BTreeSet, VecDeque};

/// Provenance recorded alongside an evaluation.
#[derive(Debug, Clone, Default)]
pub struct EvaluationContext {
    /// Baseline the profile was drafted from, when there was one.
    pub baseline_id: Option<BaselineId>,
    pub app_version: String,
    /// plugin id → version, captured at evaluation time.
    pub plugin_versions: BTreeMap<String, String>,
}

/// Evaluate every expectation in `profile` against one run's projection.
///
/// Deterministic: expectations are evaluated in profile order and every
/// entity set is sorted by canonical key, so the same inputs always
/// produce byte-identical output.
pub fn evaluate(
    profile: &Profile,
    projection: &RunProjection,
    context: &EvaluationContext,
) -> EvaluationRun {
    let started_at = Utc::now();
    let results: Vec<ExpectationResult> = profile
        .expectations
        .iter()
        .map(|expectation| evaluate_expectation(expectation, projection, started_at))
        .collect();
    EvaluationRun {
        id: uuid::Uuid::new_v4().to_string(),
        device_id: device_of(profile, projection),
        diagnostic_run_id: projection.run_id.clone(),
        profile_id: profile.id.clone(),
        profile_revision: profile.revision,
        baseline_id: context.baseline_id.clone(),
        started_at,
        finished_at: Utc::now(),
        app_version: context.app_version.clone(),
        plugin_versions: context.plugin_versions.clone(),
        results,
    }
}

fn device_of(_profile: &Profile, projection: &RunProjection) -> DeviceId {
    projection.device_id.clone()
}

/// Evaluate a single expectation. Exposed so the UI can preview one
/// expectation while the user is editing it.
pub fn evaluate_expectation(
    expectation: &Expectation,
    projection: &RunProjection,
    evaluated_at: DateTime<Utc>,
) -> ExpectationResult {
    let build = |outcome: Outcome| ExpectationResult {
        expectation_id: expectation.id.clone(),
        description: expectation.description.clone(),
        namespace: expectation.namespace.clone(),
        kind: expectation.kind.clone(),
        status: outcome.status,
        evaluated_entities: outcome.entities,
        actual: outcome.actual,
        expected: expectation.constraint.render(),
        reason: outcome.reason,
        evidence_ids: outcome.evidence_ids,
        evaluated_at,
    };

    // Step 1 — could we look at all? This gate is what keeps a blind run
    // from being reported as a broken robot.
    match projection.availability_for(&expectation.namespace, &expectation.kind) {
        NamespaceAvailability::NotObserved => {
            let detail = projection
                .reason_for(&expectation.namespace, &expectation.kind)
                .unwrap_or("the plugin reported that it could not observe this namespace");
            return build(Outcome::unknown(format!(
                "{}.{} could not be observed in this run ({detail}); \
                 nothing can be concluded about this expectation",
                expectation.namespace, expectation.kind
            )));
        }
        NamespaceAvailability::Unsupported => {
            return build(Outcome::unknown(format!(
                "no plugin in this run reports '{}.{}', so this expectation was not evaluated",
                expectation.namespace, expectation.kind
            )));
        }
        NamespaceAvailability::Observed => {}
    }

    // Step 2 — the namespace/kind was genuinely observed, so absence is a
    // fact and the constraint can be decided.
    build(decide(expectation, select(expectation, projection)))
}

/// Entities this expectation applies to, in canonical key order.
fn select<'a>(
    expectation: &Expectation,
    projection: &'a RunProjection,
) -> Vec<&'a ComparisonEntity> {
    let mut selected: Vec<&ComparisonEntity> = projection
        .entities
        .iter()
        .filter(|entity| {
            entity.key.namespace == expectation.namespace
                && entity.key.kind == expectation.kind
                && expectation.selector.matches(&entity.key.key)
        })
        .collect();
    selected.sort_by(|a, b| a.key.cmp(&b.key));
    selected
}

// ── outcome plumbing ───────────────────────────────────────────────────

struct Outcome {
    status: ExpectationStatus,
    reason: String,
    entities: Vec<EntityKey>,
    actual: Option<AttributeValue>,
    evidence_ids: Vec<String>,
}

impl Outcome {
    fn new(status: ExpectationStatus, reason: impl Into<String>) -> Self {
        Self {
            status,
            reason: reason.into(),
            entities: Vec::new(),
            actual: None,
            evidence_ids: Vec::new(),
        }
    }

    fn unknown(reason: impl Into<String>) -> Self {
        Self::new(ExpectationStatus::Unknown, reason)
    }

    fn satisfied(reason: impl Into<String>) -> Self {
        Self::new(ExpectationStatus::Satisfied, reason)
    }

    fn unsatisfied(reason: impl Into<String>) -> Self {
        Self::new(ExpectationStatus::Unsatisfied, reason)
    }

    fn not_applicable(reason: impl Into<String>) -> Self {
        Self::new(ExpectationStatus::NotApplicable, reason)
    }

    fn with_entities(mut self, entities: &[&ComparisonEntity]) -> Self {
        self.entities = entities.iter().map(|e| e.key.clone()).collect();
        let mut seen = BTreeSet::new();
        for entity in entities {
            for id in &entity.source_evidence_ids {
                if seen.insert(id.as_str().to_owned()) {
                    self.evidence_ids.push(id.as_str().to_owned());
                }
            }
        }
        self
    }

    fn with_actual(mut self, actual: AttributeValue) -> Self {
        self.actual = Some(actual);
        self
    }
}

/// Render a selector for human-readable reasons.
fn describe(selector: &Selector) -> String {
    match (&selector.key, &selector.key_prefix) {
        (Some(key), _) => format!("'{key}'"),
        (None, Some(prefix)) => format!("keys starting with '{prefix}'"),
        (None, None) => "any key".to_owned(),
    }
}

fn subject(expectation: &Expectation) -> String {
    format!(
        "{}.{} {}",
        expectation.namespace,
        expectation.kind,
        describe(&expectation.selector)
    )
}

/// What happens when the selector matched nothing, for constraints where
/// that means "the subject is not there".
fn absent(expectation: &Expectation) -> Outcome {
    match expectation.requirement {
        Requirement::Required => Outcome::unsatisfied(format!(
            "required {} was not present, and this run did observe {}.{}",
            subject(expectation),
            expectation.namespace,
            expectation.kind
        )),
        Requirement::Optional => Outcome::not_applicable(format!(
            "optional {} is not present on this device",
            subject(expectation)
        )),
    }
}

// ── constraint decision ────────────────────────────────────────────────

fn decide(expectation: &Expectation, selected: Vec<&ComparisonEntity>) -> Outcome {
    let count = selected.len();
    match &expectation.constraint {
        Constraint::Exists => {
            if count == 0 {
                absent(expectation)
            } else {
                Outcome::satisfied(format!("{count} matching entities present"))
                    .with_entities(&selected)
                    .with_actual(AttributeValue::Number(count as f64))
            }
        }
        Constraint::NotExists => {
            if count == 0 {
                Outcome::satisfied(format!("no {} present, as expected", subject(expectation)))
            } else {
                Outcome::unsatisfied(format!(
                    "{count} entities matching {} are present but were expected to be absent",
                    subject(expectation)
                ))
                .with_entities(&selected)
                .with_actual(AttributeValue::Number(count as f64))
            }
        }
        Constraint::CountMin { value } => {
            let wanted = *value as usize;
            if count >= wanted {
                Outcome::satisfied(format!("{count} present, at least {wanted} expected"))
                    .with_entities(&selected)
                    .with_actual(AttributeValue::Number(count as f64))
            } else if count == 0 && expectation.requirement == Requirement::Optional {
                Outcome::not_applicable(format!(
                    "optional {} is not present on this device",
                    subject(expectation)
                ))
            } else {
                Outcome::unsatisfied(format!("{count} present, at least {wanted} expected"))
                    .with_entities(&selected)
                    .with_actual(AttributeValue::Number(count as f64))
            }
        }
        Constraint::CountMax { value } => {
            let allowed = *value as usize;
            let outcome = if count <= allowed {
                Outcome::satisfied(format!("{count} present, at most {allowed} allowed"))
            } else {
                Outcome::unsatisfied(format!("{count} present, at most {allowed} allowed"))
            };
            outcome
                .with_entities(&selected)
                .with_actual(AttributeValue::Number(count as f64))
        }
        Constraint::RelationshipExists { from, to, mode } => {
            if count == 0 {
                return absent(expectation);
            }
            relationship(expectation, &selected, from, to, *mode)
        }
        constraint => {
            if count == 0 {
                return absent(expectation);
            }
            // Universal quantification: every selected entity must hold.
            // The first violation decides, in canonical key order, so the
            // reported entity is stable across runs.
            for entity in &selected {
                match check_attribute(entity, constraint) {
                    Verdict::Holds => {}
                    Verdict::Violated { actual } => {
                        let rendered = actual
                            .as_ref()
                            .map(AttributeValue::render)
                            .unwrap_or_else(|| "<none>".to_owned());
                        let mut outcome = Outcome::unsatisfied(format!(
                            "{} has {} = {rendered}, expected {}",
                            entity.key.canonical(),
                            constraint.field().unwrap_or("value"),
                            constraint.render()
                        ))
                        .with_entities(&selected);
                        if let Some(actual) = actual {
                            outcome = outcome.with_actual(actual);
                        }
                        return outcome;
                    }
                    Verdict::Undecidable { reason } => {
                        return Outcome::unknown(format!("{}: {reason}", entity.key.canonical()))
                            .with_entities(&selected);
                    }
                }
            }
            Outcome::satisfied(format!(
                "all {count} matching entities satisfy {}",
                constraint.render()
            ))
            .with_entities(&selected)
        }
    }
}

enum Verdict {
    Holds,
    Violated { actual: Option<AttributeValue> },
    Undecidable { reason: String },
}

fn check_attribute(entity: &ComparisonEntity, constraint: &Constraint) -> Verdict {
    let Some(field) = constraint.field() else {
        return Verdict::Undecidable {
            reason: "constraint reads no attribute".to_owned(),
        };
    };
    let Some(value) = entity.attribute(field) else {
        // The entity exists but this attribute was never projected: we did
        // not observe the value, so we must not claim it is wrong.
        return Verdict::Undecidable {
            reason: format!("attribute '{field}' was not observed for this entity"),
        };
    };

    let numeric = |value: &AttributeValue| -> Result<f64, String> {
        value.as_number().ok_or_else(|| {
            format!(
                "attribute '{field}' is {}, so a numeric comparison cannot be decided",
                value.type_name()
            )
        })
    };

    match constraint {
        Constraint::Equals {
            value: expected, ..
        } => {
            if expected.equals(value) {
                Verdict::Holds
            } else {
                Verdict::Violated {
                    actual: Some(value.clone()),
                }
            }
        }
        Constraint::NotEquals {
            value: expected, ..
        } => {
            if expected.equals(value) {
                Verdict::Violated {
                    actual: Some(value.clone()),
                }
            } else {
                Verdict::Holds
            }
        }
        Constraint::Min { value: min, .. } => match numeric(value) {
            Ok(actual) if actual >= *min => Verdict::Holds,
            Ok(_) => Verdict::Violated {
                actual: Some(value.clone()),
            },
            Err(reason) => Verdict::Undecidable { reason },
        },
        Constraint::Max { value: max, .. } => match numeric(value) {
            Ok(actual) if actual <= *max => Verdict::Holds,
            Ok(_) => Verdict::Violated {
                actual: Some(value.clone()),
            },
            Err(reason) => Verdict::Undecidable { reason },
        },
        Constraint::Between { min, max, .. } => match numeric(value) {
            Ok(actual) if actual >= *min && actual <= *max => Verdict::Holds,
            Ok(_) => Verdict::Violated {
                actual: Some(value.clone()),
            },
            Err(reason) => Verdict::Undecidable { reason },
        },
        Constraint::Contains { value: needle, .. } => match value.as_text() {
            Some(text) if text.contains(needle.as_str()) => Verdict::Holds,
            Some(_) => Verdict::Violated {
                actual: Some(value.clone()),
            },
            None => Verdict::Undecidable {
                reason: format!(
                    "attribute '{field}' is {}, so a substring test cannot be decided",
                    value.type_name()
                ),
            },
        },
        Constraint::SetContains { value: member, .. } => match value {
            AttributeValue::TextSet(set) => {
                if set.contains(member) {
                    Verdict::Holds
                } else {
                    Verdict::Violated {
                        actual: Some(value.clone()),
                    }
                }
            }
            // A single text value behaves as a one-element set, so a
            // plugin that later widens an attribute does not break
            // existing profiles.
            AttributeValue::Text(text) => {
                if text == member {
                    Verdict::Holds
                } else {
                    Verdict::Violated {
                        actual: Some(value.clone()),
                    }
                }
            }
            other => Verdict::Undecidable {
                reason: format!(
                    "attribute '{field}' is {}, so set membership cannot be decided",
                    other.type_name()
                ),
            },
        },
        _ => Verdict::Undecidable {
            reason: "constraint does not read an attribute".to_owned(),
        },
    }
}

// ── relationships ──────────────────────────────────────────────────────

/// Evaluate a relationship over edge-shaped entities.
///
/// "Edge-shaped" means the entity carries generic `from` and `to` text
/// attributes. Core knows nothing about TF, CAN buses or dependency
/// graphs — a plugin that projects edges this way gets path evaluation
/// for free.
fn relationship(
    expectation: &Expectation,
    selected: &[&ComparisonEntity],
    from: &str,
    to: &str,
    mode: RelationshipMode,
) -> Outcome {
    let mut adjacency: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    let mut edge_count = 0usize;
    for entity in selected {
        let (Some(source), Some(target)) = (
            entity.attribute("from").and_then(AttributeValue::as_text),
            entity.attribute("to").and_then(AttributeValue::as_text),
        ) else {
            continue;
        };
        adjacency.entry(source).or_default().insert(target);
        edge_count += 1;
    }

    if edge_count == 0 {
        // The kind exists but nothing in it is edge-shaped, so the
        // question cannot be answered — it is not a device fault.
        return Outcome::unknown(format!(
            "no entity of {}.{} carries 'from'/'to' attributes, so relationships cannot be \
             evaluated",
            expectation.namespace, expectation.kind
        ))
        .with_entities(selected);
    }

    let connected = match mode {
        RelationshipMode::DirectEdge => adjacency
            .get(from)
            .is_some_and(|targets| targets.contains(to)),
        RelationshipMode::Path => reachable(&adjacency, from, to),
    };

    let label = match mode {
        RelationshipMode::DirectEdge => "direct edge",
        RelationshipMode::Path => "path",
    };

    if connected {
        Outcome::satisfied(format!("{label} {from} -> {to} exists")).with_entities(selected)
    } else if expectation.requirement == Requirement::Optional {
        Outcome::not_applicable(format!(
            "optional {label} {from} -> {to} is not present among the {edge_count} observed edges"
        ))
        .with_entities(selected)
    } else {
        Outcome::unsatisfied(format!(
            "no {label} {from} -> {to} among the {edge_count} edges observed in this run"
        ))
        .with_entities(selected)
    }
}

/// Cycle-safe directed reachability.
fn reachable(adjacency: &BTreeMap<&str, BTreeSet<&str>>, from: &str, to: &str) -> bool {
    if from == to {
        return adjacency.contains_key(from);
    }
    let mut seen: BTreeSet<&str> = BTreeSet::new();
    let mut queue: VecDeque<&str> = VecDeque::new();
    seen.insert(from);
    queue.push_back(from);
    while let Some(node) = queue.pop_front() {
        let Some(targets) = adjacency.get(node) else {
            continue;
        };
        for target in targets {
            if *target == to {
                return true;
            }
            if seen.insert(target) {
                queue.push_back(target);
            }
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use doctor_domain::comparison::EntityKey;
    use doctor_domain::profile::{ConstraintValue, ProfileStatus};
    use doctor_domain::{CheckId, PluginId, ProfileId, RunId};

    fn entity(namespace: &str, kind: &str, key: &str) -> ComparisonEntity {
        ComparisonEntity::new(
            EntityKey::new(namespace, kind, key),
            key,
            &PluginId::from("test"),
            &CheckId::from("test.check"),
        )
    }

    fn projection(
        entities: Vec<ComparisonEntity>,
        kinds: &[(&str, NamespaceAvailability)],
    ) -> RunProjection {
        let mut projection = RunProjection {
            run_id: RunId::from("run-1"),
            device_id: DeviceId::from("device-1"),
            entities,
            ..Default::default()
        };
        for (key, state) in kinds {
            let (namespace, kind) = key.split_once('/').expect("ns/kind");
            projection
                .namespaces
                .entry(namespace.to_owned())
                .and_modify(|existing| {
                    if *state == NamespaceAvailability::Observed {
                        *existing = NamespaceAvailability::Observed;
                    }
                })
                .or_insert(*state);
            projection
                .kinds
                .insert(RunProjection::kind_key(namespace, kind), *state);
        }
        projection
    }

    fn expectation(namespace: &str, kind: &str, constraint: Constraint) -> Expectation {
        Expectation {
            id: "e1".into(),
            description: String::new(),
            namespace: namespace.into(),
            kind: kind.into(),
            selector: Selector::default(),
            requirement: Requirement::Required,
            constraint,
            from_baseline: false,
        }
    }

    fn status(expectation: &Expectation, projection: &RunProjection) -> ExpectationStatus {
        evaluate_expectation(expectation, projection, Utc::now()).status
    }

    // §25: ROS could not initialize → UNKNOWN, never UNSATISFIED.
    #[test]
    fn unobservable_namespace_is_unknown_not_unsatisfied() {
        let mut projection =
            projection(vec![], &[("ros/topic", NamespaceAvailability::NotObserved)]);
        projection
            .namespace_reasons
            .insert("ros".into(), "ROS_NOT_INSTALLED".into());
        let mut expectation = expectation("ros", "topic", Constraint::Exists);
        expectation.selector.key = Some("/scan".into());

        let result = evaluate_expectation(&expectation, &projection, Utc::now());
        assert_eq!(result.status, ExpectationStatus::Unknown);
        assert!(
            result.reason.contains("could not be observed"),
            "reason must explain the blindness: {}",
            result.reason
        );
        assert!(result.reason.contains("ROS_NOT_INSTALLED"));
    }

    // §43: a GPU-less machine was successfully observed → UNSATISFIED.
    #[test]
    fn observed_absence_is_unsatisfied_when_required() {
        let projection = projection(vec![], &[("nvidia/gpu", NamespaceAvailability::Observed)]);
        let expectation = expectation("nvidia", "gpu", Constraint::Exists);
        assert_eq!(
            status(&expectation, &projection),
            ExpectationStatus::Unsatisfied
        );
    }

    // §43 continued: NVML failed unexpectedly → UNKNOWN, not UNSATISFIED.
    #[test]
    fn failed_nvidia_initialization_is_unknown() {
        let projection = projection(
            vec![],
            &[("nvidia/gpu", NamespaceAvailability::NotObserved)],
        );
        let expectation = expectation("nvidia", "gpu", Constraint::Exists);
        assert_eq!(
            status(&expectation, &projection),
            ExpectationStatus::Unknown
        );
    }

    #[test]
    fn optional_absent_subject_is_not_applicable() {
        let projection = projection(vec![], &[("ros/node", NamespaceAvailability::Observed)]);
        let mut expectation = expectation("ros", "node", Constraint::Exists);
        expectation.requirement = Requirement::Optional;
        assert_eq!(
            status(&expectation, &projection),
            ExpectationStatus::NotApplicable
        );
    }

    #[test]
    fn one_failed_check_does_not_blind_other_kinds() {
        // ROS graph read fine; TF timed out. A topic expectation must
        // still be decidable, and the TF one must be UNKNOWN.
        let projection = projection(
            vec![entity("ros", "topic", "/scan|sensor_msgs/msg/LaserScan")],
            &[
                ("ros/topic", NamespaceAvailability::Observed),
                ("ros/tf_edge", NamespaceAvailability::NotObserved),
            ],
        );
        let mut topic = expectation("ros", "topic", Constraint::Exists);
        topic.selector.key_prefix = Some("/scan".into());
        assert_eq!(status(&topic, &projection), ExpectationStatus::Satisfied);

        let tf = expectation(
            "ros",
            "tf_edge",
            Constraint::RelationshipExists {
                from: "map".into(),
                to: "base_link".into(),
                mode: RelationshipMode::Path,
            },
        );
        assert_eq!(status(&tf, &projection), ExpectationStatus::Unknown);
    }

    #[test]
    fn kind_nobody_reports_is_unknown() {
        let projection = projection(vec![], &[("ros/topic", NamespaceAvailability::Observed)]);
        // Nothing claimed `service`, so we must not pretend to know.
        let expectation = expectation("ros", "service", Constraint::Exists);
        assert_eq!(
            status(&expectation, &projection),
            ExpectationStatus::Unknown
        );
    }

    #[test]
    fn numeric_thresholds_compare_observed_values() {
        let projection = projection(
            vec![entity("ros", "topic_rate", "/scan")
                .with("observed_hz", AttributeValue::Number(6.2))],
            &[("ros/topic_rate", NamespaceAvailability::Observed)],
        );
        let mut expectation = expectation(
            "ros",
            "topic_rate",
            Constraint::Min {
                field: "observed_hz".into(),
                value: 8.0,
            },
        );
        expectation.selector.key = Some("/scan".into());
        let result = evaluate_expectation(&expectation, &projection, Utc::now());
        assert_eq!(result.status, ExpectationStatus::Unsatisfied);
        assert_eq!(result.actual, Some(AttributeValue::Number(6.2)));
        assert_eq!(result.expected, "observed_hz >= 8");

        expectation.constraint = Constraint::Min {
            field: "observed_hz".into(),
            value: 5.0,
        };
        assert_eq!(
            status(&expectation, &projection),
            ExpectationStatus::Satisfied
        );
    }

    #[test]
    fn missing_attribute_is_unknown_not_unsatisfied() {
        let projection = projection(
            vec![entity("ros", "topic_rate", "/scan")],
            &[("ros/topic_rate", NamespaceAvailability::Observed)],
        );
        let expectation = expectation(
            "ros",
            "topic_rate",
            Constraint::Min {
                field: "observed_hz".into(),
                value: 8.0,
            },
        );
        let result = evaluate_expectation(&expectation, &projection, Utc::now());
        assert_eq!(result.status, ExpectationStatus::Unknown);
        assert!(result.reason.contains("was not observed"));
    }

    #[test]
    fn type_mismatch_is_unknown() {
        let projection = projection(
            vec![entity("system", "cpu", "primary")
                .with("logical_cores", AttributeValue::text("many"))],
            &[("system/cpu", NamespaceAvailability::Observed)],
        );
        let expectation = expectation(
            "system",
            "cpu",
            Constraint::Min {
                field: "logical_cores".into(),
                value: 4.0,
            },
        );
        assert_eq!(
            status(&expectation, &projection),
            ExpectationStatus::Unknown
        );
    }

    #[test]
    fn all_selected_entities_must_hold() {
        let projection = projection(
            vec![
                entity("ros", "topic_rate", "/camera/a").with("hz", AttributeValue::Number(30.0)),
                entity("ros", "topic_rate", "/camera/b").with("hz", AttributeValue::Number(2.0)),
            ],
            &[("ros/topic_rate", NamespaceAvailability::Observed)],
        );
        let mut expectation = expectation(
            "ros",
            "topic_rate",
            Constraint::Min {
                field: "hz".into(),
                value: 10.0,
            },
        );
        expectation.selector.key_prefix = Some("/camera/".into());
        let result = evaluate_expectation(&expectation, &projection, Utc::now());
        assert_eq!(result.status, ExpectationStatus::Unsatisfied);
        assert!(result.reason.contains("/camera/b"), "{}", result.reason);
        assert_eq!(result.evaluated_entities.len(), 2);
    }

    #[test]
    fn count_and_set_operators() {
        let projection = projection(
            vec![
                entity("network", "interface", "eth0")
                    .with("ipv4", AttributeValue::set(["10.0.0.2", "10.0.0.3"])),
                entity("network", "interface", "lo"),
            ],
            &[("network/interface", NamespaceAvailability::Observed)],
        );
        let count = expectation("network", "interface", Constraint::CountMin { value: 2 });
        assert_eq!(status(&count, &projection), ExpectationStatus::Satisfied);

        let too_many = expectation("network", "interface", Constraint::CountMax { value: 1 });
        assert_eq!(
            status(&too_many, &projection),
            ExpectationStatus::Unsatisfied
        );

        let mut member = expectation(
            "network",
            "interface",
            Constraint::SetContains {
                field: "ipv4".into(),
                value: "10.0.0.2".into(),
            },
        );
        member.selector.key = Some("eth0".into());
        assert_eq!(status(&member, &projection), ExpectationStatus::Satisfied);
    }

    #[test]
    fn equality_and_not_exists() {
        let projection = projection(
            vec![entity("system", "os", "primary").with("name", AttributeValue::text("Ubuntu"))],
            &[
                ("system/os", NamespaceAvailability::Observed),
                ("system/swap", NamespaceAvailability::Observed),
            ],
        );
        let equals = expectation(
            "system",
            "os",
            Constraint::Equals {
                field: "name".into(),
                value: ConstraintValue::Text("Ubuntu".into()),
            },
        );
        assert_eq!(status(&equals, &projection), ExpectationStatus::Satisfied);

        let none = expectation("system", "swap", Constraint::NotExists);
        assert_eq!(status(&none, &projection), ExpectationStatus::Satisfied);
    }

    #[test]
    fn relationship_paths_traverse_generic_edges() {
        let edge = |from: &str, to: &str| {
            entity("ros", "tf_edge", &format!("{from}->{to}"))
                .with("from", AttributeValue::text(from))
                .with("to", AttributeValue::text(to))
        };
        let projection = projection(
            vec![
                edge("map", "odom"),
                edge("odom", "base_link"),
                edge("base_link", "laser"),
            ],
            &[("ros/tf_edge", NamespaceAvailability::Observed)],
        );

        let path = expectation(
            "ros",
            "tf_edge",
            Constraint::RelationshipExists {
                from: "map".into(),
                to: "laser".into(),
                mode: RelationshipMode::Path,
            },
        );
        assert_eq!(status(&path, &projection), ExpectationStatus::Satisfied);

        let direct = expectation(
            "ros",
            "tf_edge",
            Constraint::RelationshipExists {
                from: "map".into(),
                to: "laser".into(),
                mode: RelationshipMode::DirectEdge,
            },
        );
        assert_eq!(status(&direct, &projection), ExpectationStatus::Unsatisfied);

        let missing = expectation(
            "ros",
            "tf_edge",
            Constraint::RelationshipExists {
                from: "map".into(),
                to: "gripper".into(),
                mode: RelationshipMode::Path,
            },
        );
        assert_eq!(
            status(&missing, &projection),
            ExpectationStatus::Unsatisfied
        );
    }

    #[test]
    fn relationship_traversal_survives_cycles() {
        let edge = |from: &str, to: &str| {
            entity("ros", "tf_edge", &format!("{from}->{to}"))
                .with("from", AttributeValue::text(from))
                .with("to", AttributeValue::text(to))
        };
        let projection = projection(
            vec![edge("a", "b"), edge("b", "a")],
            &[("ros/tf_edge", NamespaceAvailability::Observed)],
        );
        let expectation = expectation(
            "ros",
            "tf_edge",
            Constraint::RelationshipExists {
                from: "a".into(),
                to: "z".into(),
                mode: RelationshipMode::Path,
            },
        );
        // Terminates rather than looping forever.
        assert_eq!(
            status(&expectation, &projection),
            ExpectationStatus::Unsatisfied
        );
    }

    fn profile_with(expectations: Vec<Expectation>) -> Profile {
        Profile {
            schema_version: doctor_domain::profile::PROFILE_SCHEMA_VERSION,
            id: ProfileId::from("p1"),
            name: "test".into(),
            description: String::new(),
            revision: 3,
            status: ProfileStatus::Active,
            created_at: Utc::now(),
            updated_at: Utc::now(),
            tags: vec![],
            expectations,
        }
    }

    #[test]
    fn evaluation_is_deterministic_and_pins_the_revision() {
        let mut first = expectation("ros", "topic", Constraint::Exists);
        first.id = "topic-present".into();
        let mut second = expectation("ros", "tf_edge", Constraint::Exists);
        second.id = "tf-present".into();
        let profile = profile_with(vec![first, second]);
        let projection = projection(
            vec![entity("ros", "topic", "/scan")],
            &[
                ("ros/topic", NamespaceAvailability::Observed),
                ("ros/tf_edge", NamespaceAvailability::Observed),
            ],
        );
        let context = EvaluationContext {
            app_version: "0.1.0".into(),
            ..Default::default()
        };
        let a = evaluate(&profile, &projection, &context);
        let b = evaluate(&profile, &projection, &context);
        assert_eq!(a.profile_revision, 3);
        assert_eq!(
            a.results.iter().map(|r| r.status).collect::<Vec<_>>(),
            b.results.iter().map(|r| r.status).collect::<Vec<_>>()
        );
        assert_eq!(a.satisfied(), 1);
        assert_eq!(a.unsatisfied(), 1);
    }

    // §40: C2 answers "is the expectation satisfied", never "why".
    #[test]
    fn evaluation_output_contains_no_diagnosis_vocabulary() {
        let profile = profile_with(vec![expectation("ros", "topic", Constraint::Exists)]);
        let projection = projection(vec![], &[("ros/topic", NamespaceAvailability::Observed)]);
        let run = evaluate(&profile, &projection, &EvaluationContext::default());
        let json = serde_json::to_string(&run).unwrap().to_lowercase();
        for forbidden in [
            "finding",
            "root_cause",
            "rootcause",
            "severity",
            "score",
            "critical",
            "remediation",
        ] {
            assert!(
                !json.contains(forbidden),
                "evaluation leaked '{forbidden}': {json}"
            );
        }
    }
}
