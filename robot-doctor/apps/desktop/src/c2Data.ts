// Pure view helpers for the Phase C2 pages. Kept free of React and of
// the Tauri boundary so they can be unit-tested directly.
//
// Nothing here interprets a difference as a fault: rendering an ADDED
// entity or an UNSATISFIED expectation is presentation, and the words
// stay the ones the backend produced.

import type {
  AttributeValue,
  BaselineDiff,
  BaselineDiffEntity,
  EntityDiffState,
  EvaluationRun,
  ExpectationResult,
  ExpectationStatus,
  Expectation,
  Constraint,
} from "./types";

/** Render an attribute the same way the backend does in its reasons. */
export function renderAttribute(value: AttributeValue | null | undefined): string {
  if (!value) return "—";
  switch (value.type) {
    case "NUMBER":
      return Number.isInteger(value.value) ? String(value.value) : value.value.toFixed(4);
    case "TEXT":
      return value.value;
    case "BOOL":
      return String(value.value);
    case "TEXT_SET":
      return [...value.value].sort().join(", ");
  }
}

export const DIFF_STATES: EntityDiffState[] = [
  "ADDED",
  "REMOVED",
  "CHANGED",
  "UNAVAILABLE",
  "UNKNOWN",
  "UNCHANGED",
];

export type DiffCounts = Record<EntityDiffState, number>;

export function diffCounts(diff: BaselineDiff | null | undefined): DiffCounts {
  const counts = Object.fromEntries(DIFF_STATES.map((s) => [s, 0])) as DiffCounts;
  for (const entity of diff?.entities ?? []) {
    counts[entity.state] += 1;
  }
  return counts;
}

/**
 * Entities worth showing first. UNCHANGED is the bulk of a healthy diff
 * and is collapsed by default, but never hidden outright.
 */
export function notableEntities(diff: BaselineDiff | null | undefined): BaselineDiffEntity[] {
  return (diff?.entities ?? []).filter((e) => e.state !== "UNCHANGED");
}

/** Group entities by namespace for sectioned rendering, in stable order. */
export function groupByNamespace<T extends { key: { namespace: string } }>(
  items: T[],
): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const list = groups.get(item.key.namespace);
    if (list) list.push(item);
    else groups.set(item.key.namespace, [item]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

export const EXPECTATION_STATUSES: ExpectationStatus[] = [
  "UNSATISFIED",
  "UNKNOWN",
  "NOT_APPLICABLE",
  "SATISFIED",
];

export type StatusCounts = Record<ExpectationStatus, number>;

export function statusCounts(evaluation: EvaluationRun | null | undefined): StatusCounts {
  const counts = Object.fromEntries(EXPECTATION_STATUSES.map((s) => [s, 0])) as StatusCounts;
  for (const result of evaluation?.results ?? []) {
    counts[result.status] += 1;
  }
  return counts;
}

/**
 * Order results so the ones needing attention come first, then UNKNOWN
 * (which needs a *different* kind of attention — Robot Doctor could not
 * see), then the rest.
 */
export function orderedResults(evaluation: EvaluationRun | null | undefined): ExpectationResult[] {
  const rank = (status: ExpectationStatus) => EXPECTATION_STATUSES.indexOf(status);
  return [...(evaluation?.results ?? [])].sort(
    (a, b) => rank(a.status) - rank(b.status) || a.expectation_id.localeCompare(b.expectation_id),
  );
}

/**
 * One-line summary of an evaluation. Deliberately not a score: counts
 * only, with UNKNOWN called out as "not observed" rather than folded in
 * with failures.
 */
export function evaluationSummary(evaluation: EvaluationRun | null | undefined): string {
  if (!evaluation) return "No profile evaluated for this run";
  const counts = statusCounts(evaluation);
  const parts = [
    `${counts.SATISFIED} satisfied`,
    `${counts.UNSATISFIED} unsatisfied`,
    `${counts.UNKNOWN} not observed`,
  ];
  if (counts.NOT_APPLICABLE > 0) parts.push(`${counts.NOT_APPLICABLE} n/a`);
  return parts.join(" · ");
}

/** Human rendering of a constraint, mirroring the backend's wording. */
export function renderConstraint(constraint: Constraint): string {
  switch (constraint.operator) {
    case "exists":
      return "exists";
    case "not_exists":
      return "does not exist";
    case "equals":
      return `${constraint.field} == ${constraint.value}`;
    case "not_equals":
      return `${constraint.field} != ${constraint.value}`;
    case "min":
      return `${constraint.field} >= ${constraint.value}`;
    case "max":
      return `${constraint.field} <= ${constraint.value}`;
    case "between":
      return `${constraint.min} <= ${constraint.field} <= ${constraint.max}`;
    case "count_min":
      return `count >= ${constraint.value}`;
    case "count_max":
      return `count <= ${constraint.value}`;
    case "contains":
      return `${constraint.field} contains '${constraint.value}'`;
    case "set_contains":
      return `${constraint.field} includes '${constraint.value}'`;
    case "relationship_exists":
      return constraint.mode === "direct_edge"
        ? `direct edge ${constraint.from} -> ${constraint.to}`
        : `path ${constraint.from} -> ${constraint.to}`;
  }
}

/** What an expectation applies to, for list rendering. */
export function renderSubject(expectation: Expectation): string {
  const selector = expectation.selector ?? {};
  const scope = selector.key
    ? selector.key
    : selector.key_prefix
      ? `${selector.key_prefix}*`
      : "any";
  return `${expectation.namespace}.${expectation.kind} ${scope}`;
}

/** Whether the CAPTURE button should warn before proceeding. */
export function captureNeedsConfirmation(candidate: {
  overall_health?: string | null;
  warnings: { code: string }[];
  unobserved_namespaces: string[];
}): boolean {
  return (
    (candidate.warnings?.length ?? 0) > 0 ||
    (candidate.unobserved_namespaces?.length ?? 0) > 0 ||
    (candidate.overall_health != null && candidate.overall_health !== "HEALTHY")
  );
}
