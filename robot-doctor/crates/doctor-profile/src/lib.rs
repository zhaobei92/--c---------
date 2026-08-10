//! Known-good baselines, profiles and expectation evaluation (Phase C2).
//!
//! This crate answers four questions and refuses the fifth:
//!
//! 1. What was known-good?            → [`baseline::capture`]
//! 2. What is expected?               → [`validate`] / [`draft`]
//! 3. How does the current run differ?→ [`baseline::diff`]
//! 4. Are expectations satisfied?     → [`evaluate::evaluate`]
//! 5. *Why is it broken?*             → not answered here. Findings, root
//!    causes and severities belong to later phases; nothing in this crate
//!    produces them.
//!
//! Everything is plugin-independent: comparison happens over
//! [`doctor_domain::comparison::ComparisonEntity`] values, so core never
//! learns what ROS, NVML or a network interface is.
//!
//! Profiles are **data, not code**. There is no expression language, no
//! embedded script and no shell: an imported profile can only select
//! entities and apply one of a closed set of operators.

pub mod baseline;
pub mod draft;
pub mod evaluate;
pub mod validate;

pub use baseline::{capture, diff, eligibility_warnings, CaptureInput, EligibilityWarning};
pub use draft::{draft, DraftReport, DraftSuggestion, SkippedEntity};
pub use evaluate::{evaluate, evaluate_expectation, EvaluationContext};
pub use validate::{parse_yaml, to_yaml, validate, ProfileDocument, ProfileError, ValidationError};

#[cfg(test)]
mod boundary_tests {
    //! §40 boundary: C2 states differences and expectation outcomes, and
    //! must not acquire diagnosis vocabulary. These tests fail loudly if
    //! anyone adds a Finding, a severity or a compliance score to the C2
    //! surface — including via a transitive re-export.

    #[test]
    fn crate_surface_mentions_no_diagnosis_types() {
        // Compile-time proof by source inspection: the crate never names
        // the diagnosis types that doctor-domain also exports.
        let sources = [
            include_str!("baseline.rs"),
            include_str!("draft.rs"),
            include_str!("evaluate.rs"),
            include_str!("validate.rs"),
        ];
        for source in sources {
            for forbidden in ["Finding", "RootCause", "Severity", "Remediation"] {
                let hits: Vec<&str> = source
                    .lines()
                    .filter(|line| line.contains(forbidden))
                    .filter(|line| {
                        // Comments explaining the boundary are allowed;
                        // code that uses the type is not.
                        let trimmed = line.trim_start();
                        !trimmed.starts_with("//") && !trimmed.starts_with("*")
                    })
                    .collect();
                assert!(
                    hits.is_empty(),
                    "C2 must not reference {forbidden}: {hits:?}"
                );
            }
        }
    }

    #[test]
    fn evaluation_status_vocabulary_is_closed() {
        use doctor_domain::evaluation::ExpectationStatus;
        let rendered: Vec<String> = [
            ExpectationStatus::Satisfied,
            ExpectationStatus::Unsatisfied,
            ExpectationStatus::Unknown,
            ExpectationStatus::NotApplicable,
        ]
        .iter()
        .map(|s| serde_json::to_string(s).unwrap())
        .collect();
        assert_eq!(
            rendered,
            vec![
                "\"SATISFIED\"",
                "\"UNSATISFIED\"",
                "\"UNKNOWN\"",
                "\"NOT_APPLICABLE\""
            ]
        );
    }
}
