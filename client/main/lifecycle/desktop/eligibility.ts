import { powerMonitor, type WebContents } from 'electron'

import type { PresentationMode, PresentationState } from '@ipc/contracts'

export interface EligibilitySnapshot {
  disposed: boolean
  status: PresentationState['status']
  effectiveMode: PresentationMode
  stageEpoch: number
  startupGeneration: number
  authenticated: boolean
  identity: string | null
  locked: boolean
  isDesktopSender: (sender: WebContents) => boolean
}

export function assertDesktopSender(
  snapshot: Pick<EligibilitySnapshot, 'disposed' | 'isDesktopSender'>,
  sender: WebContents
): void {
  if (snapshot.disposed) {
    throw new Error('桌面呈现已关闭。')
  }

  if (!snapshot.isDesktopSender(sender)) {
    throw new Error('仅桌面入口允许此操作。')
  }
}

export function assertSettingsSender(
  snapshot: Pick<EligibilitySnapshot, 'disposed' | 'isDesktopSender'> & {
    isSettingsSender: (sender: WebContents) => boolean
  },
  sender: WebContents
): void {
  if (snapshot.disposed) {
    throw new Error('桌面呈现已关闭。')
  }

  if (!snapshot.isDesktopSender(sender) && !snapshot.isSettingsSender(sender)) {
    throw new Error('仅本机设置入口允许此操作。')
  }
}

/** 捕获账户、代次与窗口身份；回写前重新取一次，任一变化即失效。 */
export function captureDesktopEligibility(
  current: () => EligibilitySnapshot,
  sender: WebContents,
  epoch: number
): () => boolean {
  const initial = current()
  assertDesktopSender(initial, sender)

  const generation = initial.startupGeneration
  const identity = initial.identity

  return () => {
    const snapshot = current()

    return (
      !snapshot.disposed &&
      snapshot.status === 'active' &&
      snapshot.effectiveMode === 'desktop' &&
      generation === snapshot.startupGeneration &&
      epoch === snapshot.stageEpoch &&
      !snapshot.locked &&
      powerMonitor.getSystemIdleState(1) !== 'locked' &&
      snapshot.authenticated &&
      identity === snapshot.identity &&
      snapshot.isDesktopSender(sender)
    )
  }
}
