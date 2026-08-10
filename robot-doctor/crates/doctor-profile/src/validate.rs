//! Profile parsing and validation (Phase C2).
//!
//! Validation errors always carry a machine code *and* a path, so the UI
//! can point at the offending expectation instead of saying "invalid
//! profile". Parsing is pure data: a profile can never execute anything.

use doctor_domain::profile::{
    Constraint, Expectation, Profile, ProfileStatus, Requirement, PROFILE_SCHEMA_VERSION,
};
use doctor_domain::ProfileId;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// One validation problem, located inside the document.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ValidationError {
    /// Machine-readable code, e.g. `DUPLICATE_EXPECTATION_ID`.
    pub code: String,
    /// Document path, e.g. `expectations[2].constraint.value`.
    pub path: String,
    pub message: String,
}

impl ValidationError {
    fn new(code: &str, path: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.to_owned(),
            path: path.into(),
            message: message.into(),
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ProfileError {
    #[error("profile YAML is malformed: {0}")]
    Malformed(String),
    #[error("profile failed validation with {} error(s)", .0.len())]
    Invalid(Vec<ValidationError>),
}

impl ProfileError {
    pub fn errors(&self) -> Vec<ValidationError> {
        match self {
            ProfileError::Malformed(message) => {
                vec![ValidationError::new("MALFORMED_YAML", "", message.clone())]
            }
            ProfileError::Invalid(errors) => errors.clone(),
        }
    }
}

/// The YAML shape a human writes. Kept separate from the domain `Profile`
/// so the file format can stay friendly (defaults, no timestamps) while
/// the internal model stays explicit.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProfileDocument {
    pub schema_version: u32,
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default = "one")]
    pub revision: u32,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub expectations: Vec<Expectation>,
}

fn one() -> u32 {
    1
}

/// Parse and validate a profile from YAML.
pub fn parse_yaml(yaml: &str) -> Result<Profile, ProfileError> {
    let document: ProfileDocument =
        serde_yaml::from_str(yaml).map_err(|e| ProfileError::Malformed(e.to_string()))?;
    let now = chrono::Utc::now();
    let profile = Profile {
        schema_version: document.schema_version,
        id: ProfileId::from(document.id.as_str()),
        name: document.name,
        description: document.description,
        revision: document.revision,
        status: ProfileStatus::Draft,
        created_at: now,
        updated_at: now,
        tags: document.tags,
        expectations: document.expectations,
    };
    validate(&profile)?;
    Ok(profile)
}

/// Deterministic YAML export — stable field order and sorted expectation
/// ids, so profiles produce meaningful git diffs.
pub fn to_yaml(profile: &Profile) -> String {
    let mut expectations = profile.expectations.clone();
    expectations.sort_by(|a, b| a.id.cmp(&b.id));
    let document = ProfileDocument {
        schema_version: profile.schema_version,
        id: profile.id.to_string(),
        name: profile.name.clone(),
        description: profile.description.clone(),
        revision: profile.revision,
        tags: profile.tags.clone(),
        expectations,
    };
    serde_yaml::to_string(&document).expect("profile documents serialize")
}

/// Full structural validation. Returns every problem found, not just the
/// first — a user fixing a profile should see the whole list.
pub fn validate(profile: &Profile) -> Result<(), ProfileError> {
    let mut errors = Vec::new();

    if profile.schema_version == 0 || profile.schema_version > PROFILE_SCHEMA_VERSION {
        errors.push(ValidationError::new(
            "UNSUPPORTED_SCHEMA_VERSION",
            "schema_version",
            format!(
                "schema_version {} is not supported (this build understands 1..={PROFILE_SCHEMA_VERSION})",
                profile.schema_version
            ),
        ));
    }
    if profile.id.as_str().trim().is_empty() {
        errors.push(ValidationError::new(
            "MISSING_FIELD",
            "id",
            "profile id must not be empty",
        ));
    }
    if profile.name.trim().is_empty() {
        errors.push(ValidationError::new(
            "MISSING_FIELD",
            "name",
            "profile name must not be empty",
        ));
    }
    if profile.revision == 0 {
        errors.push(ValidationError::new(
            "INVALID_REVISION",
            "revision",
            "profile revision must start at 1",
        ));
    }

    let mut seen: BTreeSet<&str> = BTreeSet::new();
    for (index, expectation) in profile.expectations.iter().enumerate() {
        let path = format!("expectations[{index}]");
        validate_expectation(expectation, &path, &mut seen, &mut errors);
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(ProfileError::Invalid(errors))
    }
}

fn validate_expectation<'a>(
    expectation: &'a Expectation,
    path: &str,
    seen: &mut BTreeSet<&'a str>,
    errors: &mut Vec<ValidationError>,
) {
    if expectation.id.trim().is_empty() {
        errors.push(ValidationError::new(
            "MISSING_FIELD",
            format!("{path}.id"),
            "expectation id must not be empty",
        ));
    } else if !seen.insert(expectation.id.as_str()) {
        errors.push(ValidationError::new(
            "DUPLICATE_EXPECTATION_ID",
            format!("{path}.id"),
            format!("expectation id '{}' is used more than once", expectation.id),
        ));
    }
    if expectation.namespace.trim().is_empty() {
        errors.push(ValidationError::new(
            "MISSING_FIELD",
            format!("{path}.namespace"),
            "namespace must not be empty",
        ));
    }
    if expectation.kind.trim().is_empty() {
        errors.push(ValidationError::new(
            "MISSING_FIELD",
            format!("{path}.kind"),
            "kind must not be empty",
        ));
    }

    // An empty selector only makes sense for count/relationship style
    // constraints; anything else would silently apply to everything.
    if expectation.selector.is_empty() && !expectation.constraint.allows_empty_selector() {
        errors.push(ValidationError::new(
            "EMPTY_SELECTOR",
            format!("{path}.selector"),
            format!(
                "operator '{}' needs a selector (key or key_prefix)",
                expectation.constraint.operator_name()
            ),
        ));
    }
    if let Some(key) = &expectation.selector.key {
        if key.trim().is_empty() {
            errors.push(ValidationError::new(
                "MALFORMED_ENTITY_KEY",
                format!("{path}.selector.key"),
                "entity key must not be blank",
            ));
        }
    }
    if expectation.selector.key.is_some() && expectation.selector.key_prefix.is_some() {
        errors.push(ValidationError::new(
            "AMBIGUOUS_SELECTOR",
            format!("{path}.selector"),
            "set either key or key_prefix, not both",
        ));
    }

    validate_constraint(&expectation.constraint, path, errors);

    // OPTIONAL + NOT_EXISTS is contradictory: absence is the point, but
    // OPTIONAL means absence is not evaluated.
    if expectation.requirement == Requirement::Optional
        && matches!(expectation.constraint, Constraint::NotExists)
    {
        errors.push(ValidationError::new(
            "CONTRADICTORY_REQUIREMENT",
            format!("{path}.requirement"),
            "not_exists with requirement 'optional' can never be evaluated; use 'required'",
        ));
    }
}

fn validate_constraint(constraint: &Constraint, path: &str, errors: &mut Vec<ValidationError>) {
    let field_path = format!("{path}.constraint.field");
    if let Some(field) = constraint.field() {
        if field.trim().is_empty() {
            errors.push(ValidationError::new(
                "MISSING_FIELD",
                field_path.clone(),
                format!(
                    "operator '{}' requires a field name",
                    constraint.operator_name()
                ),
            ));
        }
    }
    match constraint {
        Constraint::Between { min, max, .. } => {
            if min > max {
                errors.push(ValidationError::new(
                    "REVERSED_RANGE",
                    format!("{path}.constraint"),
                    format!("between requires min <= max (got min={min}, max={max})"),
                ));
            }
            if !min.is_finite() || !max.is_finite() {
                errors.push(ValidationError::new(
                    "INVALID_NUMBER",
                    format!("{path}.constraint"),
                    "between bounds must be finite numbers",
                ));
            }
        }
        Constraint::Min { value, .. } | Constraint::Max { value, .. } => {
            if !value.is_finite() {
                errors.push(ValidationError::new(
                    "INVALID_NUMBER",
                    format!("{path}.constraint.value"),
                    format!(
                        "operator '{}' requires a finite number",
                        constraint.operator_name()
                    ),
                ));
            }
        }
        Constraint::Contains { value, .. } | Constraint::SetContains { value, .. } => {
            if value.is_empty() {
                errors.push(ValidationError::new(
                    "MISSING_FIELD",
                    format!("{path}.constraint.value"),
                    format!(
                        "operator '{}' requires a non-empty value",
                        constraint.operator_name()
                    ),
                ));
            }
        }
        Constraint::RelationshipExists { from, to, .. }
            if from.trim().is_empty() || to.trim().is_empty() =>
        {
            errors.push(ValidationError::new(
                "MISSING_FIELD",
                format!("{path}.constraint"),
                "relationship_exists requires both 'from' and 'to'",
            ));
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use doctor_domain::profile::{ConstraintValue, Selector};

    fn expectation(id: &str, constraint: Constraint, selector: Selector) -> Expectation {
        Expectation {
            id: id.into(),
            description: String::new(),
            namespace: "ros".into(),
            kind: "topic".into(),
            selector,
            requirement: Requirement::Required,
            constraint,
            from_baseline: false,
        }
    }

    fn profile(expectations: Vec<Expectation>) -> Profile {
        let now = chrono::Utc::now();
        Profile {
            schema_version: 1,
            id: ProfileId::from("p"),
            name: "P".into(),
            description: String::new(),
            revision: 1,
            status: ProfileStatus::Draft,
            created_at: now,
            updated_at: now,
            tags: vec![],
            expectations,
        }
    }

    fn key(k: &str) -> Selector {
        Selector {
            key: Some(k.into()),
            key_prefix: None,
        }
    }

    fn codes(err: ProfileError) -> Vec<String> {
        err.errors().into_iter().map(|e| e.code).collect()
    }

    #[test]
    fn duplicate_expectation_ids_are_rejected_with_path() {
        let err = validate(&profile(vec![
            expectation("dup", Constraint::Exists, key("/a")),
            expectation("dup", Constraint::Exists, key("/b")),
        ]))
        .unwrap_err();
        let errors = err.errors();
        assert_eq!(errors[0].code, "DUPLICATE_EXPECTATION_ID");
        assert_eq!(errors[0].path, "expectations[1].id");
    }

    #[test]
    fn reversed_range_and_empty_selector_are_reported() {
        let err = validate(&profile(vec![
            expectation(
                "range",
                Constraint::Between {
                    field: "hz".into(),
                    min: 10.0,
                    max: 2.0,
                },
                key("/a"),
            ),
            expectation(
                "no-selector",
                Constraint::Min {
                    field: "hz".into(),
                    value: 1.0,
                },
                Selector::default(),
            ),
        ]))
        .unwrap_err();
        let codes = codes(err);
        assert!(codes.contains(&"REVERSED_RANGE".to_string()));
        // A value operator still needs a selector; only existence and
        // count operators may span a whole kind.
        assert!(codes.contains(&"EMPTY_SELECTOR".to_string()));
    }

    #[test]
    fn count_constraints_may_omit_a_selector() {
        assert!(validate(&profile(vec![expectation(
            "count",
            Constraint::CountMin { value: 2 },
            Selector::default(),
        )]))
        .is_ok());
    }

    #[test]
    fn unsupported_schema_version_is_rejected() {
        let mut p = profile(vec![]);
        p.schema_version = 99;
        assert!(
            codes(validate(&p).unwrap_err()).contains(&"UNSUPPORTED_SCHEMA_VERSION".to_string())
        );
    }

    #[test]
    fn yaml_round_trip_is_deterministic() {
        let yaml = r#"
schema_version: 1
id: generic-nav-robot
name: Generic Navigation Robot
revision: 2
expectations:
  - id: scan-rate
    namespace: ros
    kind: topic
    selector:
      key: /scan|sensor_msgs/msg/LaserScan
    requirement: required
    constraint:
      operator: min
      field: observed_hz
      value: 8
  - id: lidar-node
    namespace: ros
    kind: node
    selector:
      key: /lidar_driver
    requirement: required
    constraint:
      operator: exists
"#;
        let profile = parse_yaml(yaml).unwrap();
        assert_eq!(profile.revision, 2);
        assert_eq!(profile.expectations.len(), 2);

        let exported = to_yaml(&profile);
        let reparsed = parse_yaml(&exported).unwrap();
        assert_eq!(reparsed.expectations, {
            let mut sorted = profile.expectations.clone();
            sorted.sort_by(|a, b| a.id.cmp(&b.id));
            sorted
        });
        // Export is stable: exporting twice yields identical bytes.
        assert_eq!(exported, to_yaml(&reparsed));
        // Sorted by id, so git diffs stay meaningful.
        assert!(exported.find("lidar-node").unwrap() < exported.find("scan-rate").unwrap());
    }

    #[test]
    fn malformed_yaml_reports_a_code_not_just_a_message() {
        let err = parse_yaml("this: [is: not: valid").unwrap_err();
        assert_eq!(err.errors()[0].code, "MALFORMED_YAML");
    }

    #[test]
    fn equality_operators_accept_typed_values() {
        let yaml = r#"
schema_version: 1
id: sys
name: System
expectations:
  - id: arch
    namespace: system
    kind: host
    selector:
      key: primary
    requirement: required
    constraint:
      operator: equals
      field: architecture
      value: x86_64
"#;
        let profile = parse_yaml(yaml).unwrap();
        match &profile.expectations[0].constraint {
            Constraint::Equals { value, .. } => {
                assert_eq!(value, &ConstraintValue::Text("x86_64".into()))
            }
            other => panic!("unexpected constraint {other:?}"),
        }
    }
}
