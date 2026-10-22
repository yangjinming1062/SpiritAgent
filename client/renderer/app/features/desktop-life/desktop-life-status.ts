import type { DesktopVideoAction, DesktopVideoSet } from '@/modules/desktop-videos'
import type { useStrings } from '@/shared/strings'

export function desktopLifeActionName(
  action: DesktopVideoAction,
  t: ReturnType<typeof useStrings>['desktopLife']
): string {
  return action.preset ? t.actionNames[action.key as keyof typeof t.actionNames] || action.name : action.name
}

export function desktopLifeSelectedAction(
  set: DesktopVideoSet,
  actionId: number | null
): DesktopVideoAction | undefined {
  return (
    set.actions.find(action => action.id === actionId) ??
    desktopLifePendingAction(set) ??
    set.actions.find(action => action.key === 'idle') ??
    set.actions[0]
  )
}

export function desktopLifeReadyForActions(set: DesktopVideoSet | undefined): boolean {
  const idle = set?.actions.find(action => action.key === 'idle')

  return Boolean(set?.base_image_url && set.base_version > 0 && idle?.current?.video_url && idle.playable)
}

export function desktopLifePendingAction(set: DesktopVideoSet): DesktopVideoAction | undefined {
  if (!desktopLifeReadyForActions(set)) {
    return set.actions.find(action => action.key === 'idle')
  }

  return (
    set.actions.find(action => action.draft?.submission_unknown) ??
    set.actions.find(action => action.status === 'processing' && !isActionPaused(action)) ??
    set.actions.find(isActionPaused) ??
    set.actions.find(action => action.draft)
  )
}

export function desktopLifeActionsToCreate(actions: readonly DesktopVideoAction[]): DesktopVideoAction[] {
  return actions.filter(action => {
    const alreadyCreated = action.playable || Boolean(action.current?.video_url && !action.needs_update)

    return action.key !== 'idle' && !alreadyCreated
  })
}

export function isActionPaused(action: DesktopVideoAction): boolean {
  return action.status === 'processing' && action.stage === 'paused'
}

export function isActionOutcomeUnknown(action: DesktopVideoAction): boolean {
  return Boolean(action.draft?.submission_unknown || action.draft?.recovery_action === 'wait')
}

export function desktopLifeCreationStage(
  action: DesktopVideoAction,
  t: ReturnType<typeof useStrings>['desktopLife']
): string {
  const stages: Record<string, string> = t.creationStages

  return stages[action.stage] ?? t.creationStages.prepare
}

export function desktopLifeActionStatus(
  action: DesktopVideoAction,
  t: ReturnType<typeof useStrings>['desktopLife'],
  queueStrings: ReturnType<typeof useStrings>['common']['videoQueue'],
  variantStrings: ReturnType<typeof useStrings>['common']['actionVariants']
): string {
  const ownVariant = action.sync_targets.find(target => target.target_id === action.set_id)

  if (
    !action.draft &&
    ownVariant &&
    ['not_generated', 'queued', 'blocked', 'disabled', 'excluded'].includes(ownVariant.status)
  ) {
    return variantStrings.statuses[ownVariant.status]
  }

  if (isActionOutcomeUnknown(action)) {
    return t.resultPending
  }

  if (isActionPaused(action)) {
    return t.paused
  }

  const queue = action.draft?.video_queue ?? action.video_queue

  if (queue && action.status === 'processing') {
    return queueStrings[queue.state]
  }

  if (action.status === 'processing') {
    if (action.stage === 'planning') {
      return t.planning
    }

    if (action.stage === 'checking') {
      return t.checking
    }

    if (action.stage === 'repairing') {
      return t.repairing
    }

    if (action.stage === 'image' || action.stage === 'image_upload') {
      return t.generatingImage
    }

    if (['process', 'download', 'upload'].includes(action.stage)) {
      return t.processingVideo
    }

    if (action.stage === 'review') {
      return t.checkingVideo
    }

    return action.stage === 'video' || action.stage === 'motion' ? t.generatingVideo : t.preparing
  }

  if (action.status === 'failed') {
    return variantStrings.statuses.failed
  }

  if (action.status === 'not_generated' || action.status === 'blocked') {
    return variantStrings.statuses[action.status]
  }

  if (action.status === 'ready' && action.needs_update && !action.playable) {
    return t.needsUpdateLabel
  }

  return action.playable ? t.ready : action.status === 'queued' ? t.notCreated : variantStrings.statuses[action.status]
}
