//! Baseline-comparison projection for the ROS namespace (Phase C2).
//!
//! Translates normalized ROS observations into stable [`ComparisonEntity`]
//! values. Identity is semantic — namespace+name for nodes, fully
//! qualified name + type for topics/services/actions, parent+child for TF
//! edges. DDS GIDs stay in Evidence and are deliberately *not* used as
//! identity: they change between executions and would make every run look
//! completely different.
//!
//! Volatile data (capture timestamps, DDS discovery duration, endpoint
//! ordering) is excluded so a diff shows real change, not noise.

use doctor_domain::ros::{
    RosDiagnosticStatus, RosGraphSnapshot, RosLifecycleState, RosTfSnapshot, RosTopicSample,
};
use doctor_domain::{AttributeValue, ComparisonEntity};
use doctor_plugin_host::CheckContext;

pub const NAMESPACE: &str = "ros";

/// Entity kinds each ROS check is authoritative for.
///
/// Declared per check rather than per namespace so that one failing check
/// (TF timing out, say) leaves the kinds other checks did observe fully
/// comparable, instead of forcing every ROS expectation to UNKNOWN.
pub fn kinds_for(check_id: &str) -> &'static [&'static str] {
    match check_id {
        "ros.env" => &["runtime"],
        "ros.graph" => &["node", "topic", "service", "action"],
        "ros.diagnostics" => &["diagnostic_component"],
        "ros.topic_rate" | "ros.topic_age" => &["topic_rate"],
        "ros.tf" => &["tf_edge"],
        "ros.lifecycle" => &["lifecycle_node"],
        // ros.clock and ros.qos observe values, not comparable entities.
        _ => &[],
    }
}

/// Stable topic identity: `name|type`. Multiple types produce multiple
/// entities, which is exactly the fact a diff should show.
pub fn topic_key(name: &str, message_type: &str) -> String {
    format!("{name}|{message_type}")
}

/// Stable TF edge identity: `parent->child`.
pub fn tf_edge_key(parent: &str, child: &str) -> String {
    format!("{parent}->{child}")
}

/// Project a graph snapshot into nodes, topics, services and actions.
pub fn graph_entities(ctx: &CheckContext, snapshot: &RosGraphSnapshot) -> Vec<ComparisonEntity> {
    let evidence = ctx.evidence_ids();
    let mut entities = Vec::new();

    for node in &snapshot.nodes {
        entities.push(
            ctx.entity(
                NAMESPACE,
                "node",
                node.full_name.clone(),
                node.full_name.clone(),
            )
            .with("namespace", AttributeValue::text(node.namespace.clone()))
            .with("name", AttributeValue::text(node.name.clone()))
            .with(
                "publisher_count",
                AttributeValue::Number(node.publishers.len() as f64),
            )
            .with(
                "subscription_count",
                AttributeValue::Number(node.subscriptions.len() as f64),
            )
            .with_evidence(evidence.clone()),
        );
    }

    for topic in &snapshot.topics {
        for message_type in &topic.types {
            let key = topic_key(&topic.name, message_type);
            let mut entity = ctx
                .entity(NAMESPACE, "topic", key, topic.name.clone())
                .with("name", AttributeValue::text(topic.name.clone()))
                .with("type", AttributeValue::text(message_type.clone()))
                .with(
                    "publisher_count",
                    AttributeValue::Number(topic.publisher_count as f64),
                )
                .with(
                    "subscriber_count",
                    AttributeValue::Number(topic.subscriber_count as f64),
                )
                // Sets, so publisher discovery order can never look like
                // a change.
                .with(
                    "publisher_nodes",
                    AttributeValue::set(topic.publishers.iter().map(|e| e.node.clone())),
                )
                .with(
                    "subscriber_nodes",
                    AttributeValue::set(topic.subscribers.iter().map(|e| e.node.clone())),
                )
                .with_evidence(evidence.clone());

            // QoS is compared structurally: the distinct settings present
            // on each side, not a serialized endpoint list.
            if let Some(first) = topic.publishers.first() {
                entity = entity
                    .with(
                        "publisher_reliability",
                        AttributeValue::text(format!("{:?}", first.qos.reliability).to_uppercase()),
                    )
                    .with(
                        "publisher_durability",
                        AttributeValue::text(format!("{:?}", first.qos.durability).to_uppercase()),
                    );
                if let Some(depth) = first.qos.depth {
                    entity = entity.with("publisher_depth", AttributeValue::Number(depth as f64));
                }
            }
            if let Some(first) = topic.subscribers.first() {
                entity = entity
                    .with(
                        "subscriber_reliability",
                        AttributeValue::text(format!("{:?}", first.qos.reliability).to_uppercase()),
                    )
                    .with(
                        "subscriber_durability",
                        AttributeValue::text(format!("{:?}", first.qos.durability).to_uppercase()),
                    );
            }
            entities.push(entity);
        }
    }

    for service in &snapshot.services {
        let key = if service.types.is_empty() {
            service.name.clone()
        } else {
            format!("{}|{}", service.name, service.types.join(","))
        };
        entities.push(
            ctx.entity(NAMESPACE, "service", key, service.name.clone())
                .with("name", AttributeValue::text(service.name.clone()))
                .with("types", AttributeValue::set(service.types.clone()))
                .with("providers", AttributeValue::set(service.providers.clone()))
                .with_evidence(evidence.clone()),
        );
    }

    for action in &snapshot.actions {
        let key = if action.types.is_empty() {
            action.name.clone()
        } else {
            format!("{}|{}", action.name, action.types.join(","))
        };
        entities.push(
            ctx.entity(NAMESPACE, "action", key, action.name.clone())
                .with("name", AttributeValue::text(action.name.clone()))
                .with("types", AttributeValue::set(action.types.clone()))
                .with("servers", AttributeValue::set(action.servers.clone()))
                .with_evidence(evidence.clone()),
        );
    }

    entities
}

/// Project TF edges. `from`/`to` attributes make the entities edge-shaped
/// so the generic relationship/path evaluator can traverse them without
/// knowing anything about ROS.
pub fn tf_entities(ctx: &CheckContext, snapshot: &RosTfSnapshot) -> Vec<ComparisonEntity> {
    let evidence = ctx.evidence_ids();
    snapshot
        .edges
        .iter()
        .map(|edge| {
            let key = tf_edge_key(&edge.parent, &edge.child);
            let mut entity = ctx
                .entity(NAMESPACE, "tf_edge", key.clone(), key)
                .with("from", AttributeValue::text(edge.parent.clone()))
                .with("to", AttributeValue::text(edge.child.clone()))
                .with_evidence(evidence.clone());
            if let Some(is_static) = edge.is_static {
                entity = entity.with("static", AttributeValue::Bool(is_static));
            }
            // Transform timestamps are volatile and deliberately excluded.
            entity
        })
        .collect()
}

/// Project diagnostic component states. The source level is preserved
/// verbatim — C2 never reinterprets it.
pub fn diagnostics_entities(
    ctx: &CheckContext,
    statuses: &[RosDiagnosticStatus],
) -> Vec<ComparisonEntity> {
    let evidence = ctx.evidence_ids();
    statuses
        .iter()
        .map(|status| {
            ctx.entity(
                NAMESPACE,
                "diagnostic_component",
                status.name.clone(),
                status.name.clone(),
            )
            .with(
                "hardware_id",
                AttributeValue::text(status.hardware_id.clone()),
            )
            .with("level", AttributeValue::Number(status.level as f64))
            .with("level_label", AttributeValue::text(status.level_label()))
            .with(
                "source_topic",
                AttributeValue::text(status.source_topic.clone()),
            )
            .with_evidence(evidence.clone())
        })
        .collect()
}

/// Project lifecycle-managed nodes and their current state.
pub fn lifecycle_entities(
    ctx: &CheckContext,
    states: &[RosLifecycleState],
) -> Vec<ComparisonEntity> {
    let evidence = ctx.evidence_ids();
    states
        .iter()
        .map(|state| {
            ctx.entity(
                NAMESPACE,
                "lifecycle_node",
                state.node.clone(),
                state.node.clone(),
            )
            .with(
                "state_label",
                AttributeValue::text(state.state_label.clone()),
            )
            .with("state_id", AttributeValue::Number(state.state_id as f64))
            .with(
                "services_available",
                AttributeValue::Bool(state.services_available),
            )
            .with_evidence(evidence.clone())
        })
        .collect()
}

/// Project a bounded topic sample. Numeric values are preserved for
/// comparison; whether a delta matters is a Profile decision.
pub fn sample_entity(ctx: &CheckContext, sample: &RosTopicSample) -> ComparisonEntity {
    let evidence = ctx.evidence_ids();
    let mut entity = ctx
        .entity(
            NAMESPACE,
            "topic_rate",
            sample.topic.clone(),
            sample.topic.clone(),
        )
        .with("topic", AttributeValue::text(sample.topic.clone()))
        .with(
            "sample_count",
            AttributeValue::Number(sample.sample_count as f64),
        )
        .with_evidence(evidence);
    if let Some(hz) = sample.observed_hz {
        entity = entity.with("observed_hz", AttributeValue::Number(hz));
    }
    if let Some(age) = sample.message_stamp_age_s {
        entity = entity.with("message_stamp_age_s", AttributeValue::Number(age));
    }
    if let Some(age) = sample.receive_age_s {
        entity = entity.with("receive_age_s", AttributeValue::Number(age));
    }
    if let Some(message_type) = &sample.message_type {
        entity = entity.with("type", AttributeValue::text(message_type.clone()));
    }
    entity
}

/// Project the runtime identity. Core compares `kind == "runtime"`
/// entities generically to detect environment changes between a baseline
/// and a current run (distro/domain/RMW), with no ROS knowledge in core.
pub fn runtime_entity(
    ctx: &CheckContext,
    info: &doctor_domain::ros::RosEnvironmentInfo,
) -> ComparisonEntity {
    let mut entity = ctx
        .entity(NAMESPACE, "runtime", "default", "ROS runtime")
        .with_evidence(ctx.evidence_ids());
    if let Some(distro) = &info.distro {
        entity = entity.with("distro", AttributeValue::text(distro.clone()));
    }
    if let Some(version) = &info.ros_version {
        entity = entity.with("ros_version", AttributeValue::text(version.clone()));
    }
    if let Some(domain) = info.domain_id {
        entity = entity.with("domain_id", AttributeValue::Number(domain as f64));
    }
    if let Some(rmw) = &info.rmw_implementation {
        entity = entity.with("rmw", AttributeValue::text(rmw.clone()));
    }
    if let Some(provider) = &info.provider {
        entity = entity.with("provider", AttributeValue::text(provider.clone()));
    }
    entity
}
