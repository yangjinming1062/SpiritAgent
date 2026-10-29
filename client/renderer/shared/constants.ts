/** Shared renderer budgets and limits. Keep user-facing behavior tunable in one place. */
export const RENDERER_TIMINGS = {
  actionReviewPollMs: 5_000,
  actionSearchDebounceMs: 200,
  accountPollDelayMs: 2_000,
  desktopBackgroundMediaTimeoutMs: 30_000,
  desktopClockRefreshMs: 30_000,
  desktopDisconnectedGraceHiddenMs: 30_000,
  desktopDisconnectedGraceVisibleMs: 3_000,
  sceneImageTimeoutMs: 15_000,
  stageMediaTimeoutMs: 15_000,
  visualActorFinishTimeoutMs: 330_000,
  visualActorStartTimeoutMs: 15_000,
  visualStageWaitTimeoutMs: 15_000
} as const

export const RENDERER_LIMITS = {
  sceneDescriptionMaxChars: 2_000,
  sessionPageSize: 100
} as const
