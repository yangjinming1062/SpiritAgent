import type { DockTask } from '@ipc/contracts'

import type { ApplicationEligibility } from '../../shared/desktop-applications'
import type { DesktopJumpListTask } from '../../shared/desktop-jump-list'

import { jumpListTaskIcon } from './icons'
import type { RunningDockApplication } from './running'

interface ActivateOptions {
  application: RunningDockApplication | undefined
  allowRemote: boolean
  windowId?: string
  eligible: ApplicationEligibility
  ready: boolean
  activate: (windowId: string, allowRemote: boolean, eligible: ApplicationEligibility) => Promise<void>
}

export async function activateRunningApplication({
  application,
  allowRemote,
  windowId,
  eligible,
  ready,
  activate
}: ActivateOptions): Promise<boolean> {
  if (!ready) {
    return false
  }

  const window =
    windowId === undefined
      ? (application?.windows.find(item => item.onDesktopDisplay) ??
        (allowRemote ? application?.windows[0] : undefined))
      : application?.windows.find(item => item.id === windowId && item.onDesktopDisplay)

  if (!window) {
    if (windowId !== undefined) {
      throw new Error('窗口已关闭，请重新选择程序。')
    }

    return false
  }

  await activate(window.id, allowRemote && windowId === undefined && !window.onDesktopDisplay, eligible)

  return true
}

interface LaunchOptions {
  target: string
  identity: string
  eligible: ApplicationEligibility
  validateTarget: (target: string) => Promise<unknown>
  assertCurrentTarget?: () => void
  launch: (target: string, appId: string, eligible: ApplicationEligibility) => Promise<void>
}

export async function launchApplication({
  target,
  identity,
  eligible,
  validateTarget,
  assertCurrentTarget,
  launch
}: LaunchOptions): Promise<void> {
  await validateTarget(target)

  if (!eligible.current()) {
    throw new Error('桌面已改变，请重新选择程序。')
  }

  assertCurrentTarget?.()
  await launch(target, identity, eligible)
}

export async function projectTasks(tasks: DesktopJumpListTask[], fallbackIcon: string | null): Promise<DockTask[]> {
  return Promise.all(
    tasks.map(async task => {
      let icon: string | null = null

      try {
        icon = await jumpListTaskIcon(task.iconPath, task.iconIndex)
      } catch {
        /* 任务图标失败不影响任务执行。 */
      }

      return {
        id: task.id,
        title: task.title,
        ...(task.description ? { description: task.description } : {}),
        icon: icon ?? fallbackIcon
      }
    })
  )
}
