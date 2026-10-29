/**
 * Client-wide operational limits. Keep values in one place so callers can
 * reuse the same budget and future configuration can replace the source.
 */
export const CLIENT_LIMITS = {
  backendRequestTimeoutMs: 5_000,
  backendReadyTimeoutMs: 45_000,
  backendReadyRetryDelayMs: 500,
  gatewayRetryDelayMs: 5_000,
  maxCompletedGatewayCalls: 500,
  runnerConfigTimeoutMs: 5_000,
  desktopHeartbeatTimeoutMs: 10_000,
  desktopHeartbeatCheckMs: 2_000,
  desktopDisplayChangeDebounceMs: 100,
  maxDockSelections: 100
} as const
