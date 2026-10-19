#pragma once

#include <windows.h>
#include <cstdint>

namespace spirit_tray {
inline constexpr std::uint32_t state_magic = 0x53545259;
inline constexpr std::uint32_t state_version = 1;

struct SharedState {
    std::uint32_t magic;
    std::uint32_t version;
    std::uint32_t host_pid;
    std::uint32_t explorer_pid;
    std::uint64_t host_created;
    std::uint64_t taskbar;
    std::uint64_t heartbeat_tick;
    LONG active;
    LONG dark;
    LONG status;
    LONG error;
    std::uint32_t fill_count;
    std::uint32_t reserved;
};

static_assert(sizeof(SharedState) == 64);
static_assert(offsetof(SharedState, active) == 40);
static_assert(offsetof(SharedState, status) == 48);

inline constexpr std::uint32_t placement_magic = 0x5354504c;
inline constexpr std::uint32_t placement_version = 1;

struct PlacementState {
    std::uint32_t magic;
    std::uint32_t version;
    std::uint32_t host_pid;
    std::uint32_t explorer_pid;
    std::uint64_t host_created;
    std::uint64_t taskbar;
    LONG sequence;
    LONG enabled;
    LONG x;
    LONG y;
    LONG width;
    LONG height;
    LONG dpi;
    LONG status;
    LONG error;
    std::uint32_t corrections;
};

static_assert(sizeof(PlacementState) == 72);
static_assert(offsetof(PlacementState, sequence) == 32);
static_assert(offsetof(PlacementState, status) == 60);
static_assert(offsetof(PlacementState, corrections) == 68);
}
