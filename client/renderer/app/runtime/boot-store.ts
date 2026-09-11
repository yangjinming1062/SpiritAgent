import { clampBootProgress, type DesktopBootProgress } from '@ipc/contracts'
import { atom } from 'nanostores'

import { getStrings } from '@/shared/strings'

interface DesktopBootState extends DesktopBootProgress {
  visible: boolean
}

const INITIAL_BOOT_STATE: DesktopBootState = (() => {
  const strings = getStrings()

  return {
    error: null,
    message: strings.boot.steps.startingSpiritAgentDesktop(strings.brand.fullName),
    phase: 'renderer.init',
    progress: 2,
    running: true,
    timestamp: Date.now(),
    visible: true
  }
})()

export const $desktopBoot = atom<DesktopBootState>(INITIAL_BOOT_STATE)

export function applyDesktopBootProgress(progress: DesktopBootProgress): void {
  const current = $desktopBoot.get()
  const nextProgress = clampBootProgress(progress.progress)
  const mergedProgress = progress.running ? Math.max(current.progress, nextProgress) : nextProgress

  $desktopBoot.set({
    ...current,
    ...progress,
    error: progress.error ?? null,
    progress: mergedProgress,
    visible: progress.running || mergedProgress < 100 || Boolean(progress.error)
  })
}

export function setDesktopBootStep(step: {
  phase: string
  message: string
  progress: number
  running?: boolean
  error?: string | null
}): void {
  applyDesktopBootProgress({
    error: step.error ?? null,
    message: step.message,
    phase: step.phase,
    progress: step.progress,
    running: step.running ?? true,
    timestamp: Date.now()
  })
}

export function completeDesktopBoot(message?: string): void {
  if (message === undefined) {
    const strings = getStrings()
    message = strings.boot.ready(strings.brand.fullName)
  }

  const current = $desktopBoot.get()
  $desktopBoot.set({
    ...current,
    error: null,
    message,
    phase: 'renderer.ready',
    progress: 100,
    running: false,
    timestamp: Date.now(),
    visible: false
  })
}

export function failDesktopBoot(message: string): void {
  const current = $desktopBoot.get()
  $desktopBoot.set({
    ...current,
    error: message,
    message: getStrings().boot.desktopBootFailedWithMessage(message),
    phase: 'renderer.error',
    progress: clampBootProgress(current.progress),
    running: false,
    timestamp: Date.now(),
    visible: true
  })
}
