import type { DesktopVideoAction } from '@/modules/desktop-videos'
import type { useStrings } from '@/shared/strings'

import type { useDesktopLifeStrings } from './desktop-life-strings'

export function isActionPaused(action: DesktopVideoAction): boolean {
  return action.status === 'processing' && action.stage === 'paused'
}

export function desktopLifeCreationStage(
  action: DesktopVideoAction,
  t: ReturnType<typeof useDesktopLifeStrings>
): string {
  const stages: Record<string, string> = t.creationStages

  return stages[action.stage] ?? t.creationStages.prepare
}

export function desktopLifeActionStatus(
  action: DesktopVideoAction,
  t: ReturnType<typeof useDesktopLifeStrings>,
  queueStrings: ReturnType<typeof useStrings>['common']['videoQueue'],
  variantStrings: ReturnType<typeof useStrings>['common']['actionVariants']
): string {
  const ownVariant = action.sync_targets.find(
    target => target.target_kind === 'desktop' && target.target_id === action.set_id
  )

  if (
    !action.draft &&
    ownVariant &&
    ['not_generated', 'queued', 'blocked', 'disabled', 'excluded'].includes(ownVariant.status)
  ) {
    return variantStrings.statuses[ownVariant.status]
  }

  if (action.draft?.submission_unknown || action.draft?.recovery_action === 'wait') {
    return t.resultPending
  }

  if (isActionPaused(action)) {
    return t.paused
  }

  const queue = action.draft?.video_queue ?? action.video_queue

  if (queue && action.status === 'processing') {
    return queueStrings[queue.state]
  }

  if (action.stage === 'image_review') {
    return t.stepImage
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

    return action.stage === 'video' || action.stage === 'motion' ? t.generatingVideo : t.preparing
  }

  if (action.status === 'failed') {
    return t.statuses.failed
  }

  if (action.status === 'review_pending' && action.draft?.candidate_video_url) {
    return t.videoReviewPending
  }

  if (action.status === 'not_generated' || action.status === 'blocked') {
    return variantStrings.statuses[action.status]
  }

  return action.playable ? t.ready : action.status === 'queued' ? t.notCreated : t.statuses[action.status]
}
