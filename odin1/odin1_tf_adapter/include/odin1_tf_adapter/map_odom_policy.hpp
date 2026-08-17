// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// What map -> odom to publish, as a pure function.
//
// WHY THIS IS ITS OWN HEADER
// --------------------------
// This is the one decision in the adapter that can silently produce a WRONG
// robot pose rather than no pose. Publishing an identity map -> odom while the
// device has not relocalized means every `lookupTransform("map", "base_link")`
// returns an odom-frame pose dressed up as a map-frame pose: a planner will
// happily drive against a map it is not actually localized in, and nothing in
// the TF tree looks broken. Extracting it here keeps the rule readable and lets
// it be unit-tested exhaustively without a ROS graph.
//
// THE INVARIANTS
//   1. A fresh fix always wins.
//   2. Identity is only ever published when the DEVICE ITSELF defines map == odom,
//      i.e. map_mode 0 (odometry) or 1 (mapping). The vendor README says so
//      explicitly: "Set custom_map_mode = 0 to enable odometry mode. In this mode,
//      the map frame and odom frame share the same pose."
//   3. In relocalization mode (map_mode 2) with no fix yet, NOTHING is published.
//      An unknown map_mode is treated the same way, because "unknown" cannot rule
//      out relocalization.
//   4. Once a real fix has been seen, identity is never published again, under
//      any setting. Falling back to identity after a fix would teleport the robot
//      to the odom origin - strictly worse than letting the transform go stale.

#ifndef ODIN1_TF_ADAPTER__MAP_ODOM_POLICY_HPP_
#define ODIN1_TF_ADAPTER__MAP_ODOM_POLICY_HPP_

namespace odin1_tf_adapter
{

/// User-selectable behaviour when there is no fresh fix.
enum class MapOdomFallback
{
  /// Safe default. Identity only where the device defines map == odom; nothing
  /// at all while relocalization is still searching or the mode is unknown.
  Auto,
  /// Always keep the tree connected with identity when no fix has been seen.
  /// UNSAFE FOR AUTONOMY - a consumer cannot distinguish this from a real fix
  /// except by reading LocalizationStatus. Intended for bench/RViz work.
  /// Invariant 4 still applies: after a real fix this degrades to Hold.
  Identity,
  /// Republish the last correction with fresh stamps.
  Hold,
  /// Publish only a fresh fix, nothing else.
  None,
};

enum class MapOdomAction
{
  /// Publish the stored map -> odom transform.
  PublishStored,
  /// Publish an identity transform.
  PublishIdentity,
  /// Publish nothing this cycle.
  Skip,
};

struct MapOdomInput
{
  /// A real map -> odom has been received at least once since the last reset.
  bool have_fix = false;
  /// ... and the most recent one is within map_odom_timeout.
  bool fresh = false;
  /// -1 unknown, 0 odometry, 1 mapping, 2 relocalization.
  int device_map_mode = -1;
  /// A switch_mode action is running: the stream is being restarted and odom is
  /// about to reset, so any stored correction is meaningless.
  bool mode_switch_in_progress = false;
  MapOdomFallback fallback = MapOdomFallback::Auto;
};

struct MapOdomDecision
{
  MapOdomAction action = MapOdomAction::Skip;
  /// Set when the published transform does NOT come from a device fix, so
  /// LocalizationStatus can flag it to consumers.
  bool identity_fallback = false;
  /// Short, human-readable justification; surfaced in LocalizationStatus.
  const char * reason = "";
};

/// See the invariants at the top of this file.
inline MapOdomDecision decideMapOdom(const MapOdomInput & in)
{
  MapOdomDecision d;

  // A mode switch restarts the stream and resets odom to the origin. Anything
  // stored refers to the previous odom epoch.
  if (in.mode_switch_in_progress) {
    d.action = MapOdomAction::Skip;
    d.reason = "mode switch in progress; odom is about to reset";
    return d;
  }

  // Invariant 1.
  if (in.fresh) {
    d.action = MapOdomAction::PublishStored;
    d.reason = "fresh fix from the device";
    return d;
  }

  // Invariant 4: a fix was seen and has gone stale. Never identity from here.
  if (in.have_fix) {
    if (in.fallback == MapOdomFallback::Hold || in.fallback == MapOdomFallback::Identity) {
      d.action = MapOdomAction::PublishStored;
      d.reason = "fix went stale; holding the last correction";
    } else {
      d.action = MapOdomAction::Skip;
      d.reason = "fix went stale; map is intentionally disconnected";
    }
    return d;
  }

  // No fix has ever been seen.
  if (in.fallback == MapOdomFallback::None) {
    d.action = MapOdomAction::Skip;
    d.reason = "no fix yet; fallback disabled";
    return d;
  }
  if (in.fallback == MapOdomFallback::Identity) {
    d.action = MapOdomAction::PublishIdentity;
    d.identity_fallback = true;
    d.reason = "no fix yet; identity forced by configuration (unsafe for autonomy)";
    return d;
  }

  // Auto / Hold with nothing to hold: decide on what the device is doing.
  switch (in.device_map_mode) {
    case 0:
      d.action = MapOdomAction::PublishIdentity;
      d.identity_fallback = true;
      d.reason = "odometry mode: the device defines map == odom";
      return d;
    case 1:
      d.action = MapOdomAction::PublishIdentity;
      d.identity_fallback = true;
      d.reason = "mapping mode: the device defines map == odom";
      return d;
    case 2:
      // Invariant 3. This is the case the whole file exists for.
      d.action = MapOdomAction::Skip;
      d.reason = "relocalization has not succeeded yet; refusing to publish a map frame "
        "that would be silently wrong";
      return d;
    default:
      d.action = MapOdomAction::Skip;
      d.reason = "device map_mode unknown; refusing to guess that map == odom";
      return d;
  }
}

}  // namespace odin1_tf_adapter

#endif  // ODIN1_TF_ADAPTER__MAP_ODOM_POLICY_HPP_
