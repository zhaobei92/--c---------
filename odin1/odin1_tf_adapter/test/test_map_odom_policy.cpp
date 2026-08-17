// Copyright 2026 Odin1 integration contributors
// Licensed under the Apache License, Version 2.0
//
// Exhaustive tests for the map -> odom decision.
//
// No ROS graph, no node, no timing: decideMapOdom() is a pure function on
// purpose, because this is the one place in the adapter that can emit a
// confidently WRONG robot pose rather than no pose at all.

#include <gtest/gtest.h>

#include "odin1_tf_adapter/map_odom_policy.hpp"

using odin1_tf_adapter::MapOdomAction;
using odin1_tf_adapter::MapOdomFallback;
using odin1_tf_adapter::MapOdomInput;
using odin1_tf_adapter::decideMapOdom;

namespace
{
MapOdomInput input(
  bool have_fix, bool fresh, int mode, MapOdomFallback fb = MapOdomFallback::Auto,
  bool switching = false)
{
  MapOdomInput in;
  in.have_fix = have_fix;
  in.fresh = fresh;
  in.device_map_mode = mode;
  in.fallback = fb;
  in.mode_switch_in_progress = switching;
  return in;
}
}  // namespace

// ===========================================================================
// THE hazard: identity map->odom while relocalizing
// ===========================================================================

TEST(MapOdomPolicy, NeverPublishesIdentityWhileRelocalizing)
{
  // map_mode 2 with no fix: a planner reading map->base_link here would get an
  // odom-frame pose presented as a map-frame pose, and nothing would look broken.
  for (auto fb : {MapOdomFallback::Auto, MapOdomFallback::Hold, MapOdomFallback::None}) {
    const auto d = decideMapOdom(input(false, false, 2, fb));
    EXPECT_EQ(d.action, MapOdomAction::Skip) << "fallback variant leaked a map frame";
    EXPECT_FALSE(d.identity_fallback);
  }
}

TEST(MapOdomPolicy, UnknownModeIsTreatedAsUnsafe)
{
  // -1 means GetDeviceState has not answered yet. It could be relocalization
  // mode, so guessing map == odom is not allowed.
  const auto d = decideMapOdom(input(false, false, -1));
  EXPECT_EQ(d.action, MapOdomAction::Skip);
}

TEST(MapOdomPolicy, ExplicitIdentityOptInStillWorksButIsFlagged)
{
  // The escape hatch for bench/RViz work. It must still mark itself so
  // LocalizationStatus can warn.
  const auto d = decideMapOdom(input(false, false, 2, MapOdomFallback::Identity));
  EXPECT_EQ(d.action, MapOdomAction::PublishIdentity);
  EXPECT_TRUE(d.identity_fallback);
}

// ===========================================================================
// Identity where the device itself defines map == odom
// ===========================================================================

TEST(MapOdomPolicy, IdentityIsCorrectInOdometryAndMappingModes)
{
  for (int mode : {0, 1}) {
    const auto d = decideMapOdom(input(false, false, mode));
    EXPECT_EQ(d.action, MapOdomAction::PublishIdentity) << "mode " << mode;
    EXPECT_TRUE(d.identity_fallback);
  }
}

TEST(MapOdomPolicy, NoneSuppressesEvenTheCorrectIdentity)
{
  const auto d = decideMapOdom(input(false, false, 0, MapOdomFallback::None));
  EXPECT_EQ(d.action, MapOdomAction::Skip);
}

// ===========================================================================
// A fresh fix always wins
// ===========================================================================

TEST(MapOdomPolicy, FreshFixIsPublishedInEveryMode)
{
  for (int mode : {-1, 0, 1, 2}) {
    for (auto fb : {MapOdomFallback::Auto, MapOdomFallback::Identity,
        MapOdomFallback::Hold, MapOdomFallback::None})
    {
      const auto d = decideMapOdom(input(true, true, mode, fb));
      EXPECT_EQ(d.action, MapOdomAction::PublishStored) << "mode " << mode;
      EXPECT_FALSE(d.identity_fallback);
    }
  }
}

// ===========================================================================
// Losing a fix must never teleport the robot
// ===========================================================================

TEST(MapOdomPolicy, NeverFallsBackToIdentityAfterARealFix)
{
  // Going identity here would snap the robot from its true map pose to the odom
  // origin - worse than a stale transform, and it would look like a valid fix.
  for (int mode : {-1, 0, 1, 2}) {
    for (auto fb : {MapOdomFallback::Auto, MapOdomFallback::Identity,
        MapOdomFallback::Hold, MapOdomFallback::None})
    {
      const auto d = decideMapOdom(input(true, false, mode, fb));
      EXPECT_NE(d.action, MapOdomAction::PublishIdentity)
        << "mode " << mode << " leaked an identity after a real fix";
      EXPECT_FALSE(d.identity_fallback);
    }
  }
}

TEST(MapOdomPolicy, StaleFixIsHeldUnderHoldAndIdentity)
{
  EXPECT_EQ(
    decideMapOdom(input(true, false, 2, MapOdomFallback::Hold)).action,
    MapOdomAction::PublishStored);
  // `identity` degrades to hold rather than teleporting - it preserves the
  // user's intent (keep the tree connected) without the jump.
  EXPECT_EQ(
    decideMapOdom(input(true, false, 2, MapOdomFallback::Identity)).action,
    MapOdomAction::PublishStored);
}

TEST(MapOdomPolicy, StaleFixIsDroppedUnderAutoAndNone)
{
  EXPECT_EQ(
    decideMapOdom(input(true, false, 2, MapOdomFallback::Auto)).action, MapOdomAction::Skip);
  EXPECT_EQ(
    decideMapOdom(input(true, false, 2, MapOdomFallback::None)).action, MapOdomAction::Skip);
}

// ===========================================================================
// Mode switches reset odom
// ===========================================================================

TEST(MapOdomPolicy, NothingIsPublishedWhileAModeSwitchIsRunning)
{
  // switch_mode stops and restarts the stream; odom returns to the origin, so
  // any stored correction belongs to the previous epoch.
  for (bool have_fix : {false, true}) {
    for (bool fresh : {false, true}) {
      const auto d = decideMapOdom(
        input(have_fix, fresh, 2, MapOdomFallback::Identity, /*switching=*/true));
      EXPECT_EQ(d.action, MapOdomAction::Skip);
    }
  }
}

// ===========================================================================
// Every decision explains itself
// ===========================================================================

TEST(MapOdomPolicy, EveryDecisionCarriesANonEmptyReason)
{
  for (bool have_fix : {false, true}) {
    for (bool fresh : {false, true}) {
      for (int mode : {-1, 0, 1, 2}) {
        for (auto fb : {MapOdomFallback::Auto, MapOdomFallback::Identity,
            MapOdomFallback::Hold, MapOdomFallback::None})
        {
          for (bool sw : {false, true}) {
            const auto d = decideMapOdom(input(have_fix, fresh, mode, fb, sw));
            ASSERT_NE(d.reason, nullptr);
            EXPECT_STRNE(d.reason, "") << "a silent skip is undebuggable in the field";
          }
        }
      }
    }
  }
}

TEST(MapOdomPolicy, IdentityFlagIsSetIfAndOnlyIfIdentityIsPublished)
{
  for (bool have_fix : {false, true}) {
    for (bool fresh : {false, true}) {
      for (int mode : {-1, 0, 1, 2}) {
        for (auto fb : {MapOdomFallback::Auto, MapOdomFallback::Identity,
            MapOdomFallback::Hold, MapOdomFallback::None})
        {
          const auto d = decideMapOdom(input(have_fix, fresh, mode, fb));
          EXPECT_EQ(d.identity_fallback, d.action == MapOdomAction::PublishIdentity);
        }
      }
    }
  }
}
