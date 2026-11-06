import { randomUUID } from 'node:crypto'

import type { DockEntry } from '@ipc/contracts'

import type { RunningApplicationsState, RunningApplicationWindow } from '../../shared/desktop-applications'
import { isPackagedAppTarget } from '../windows-installed-apps'

import { sameApplicationTarget } from './registry'

export interface RunningDockApplication {
  id: string
  key: string
  target: string | null
  name: string
  icon: string | null
  windows: RunningApplicationWindow[]
  enriched: boolean
}

export function windowsFor(application: RunningDockApplication | undefined): DockEntry['windows'] {
  return (application?.windows ?? [])
    .filter(window => window.onDesktopDisplay)
    .map(window => ({
      id: window.id,
      title: window.title || application?.name || '',
      minimized: window.minimized
    }))
}

/** 运行中程序清单：按 appId 归并窗口，同一目标的不同启动器视为一个程序。 */
export function createRunningRegistry() {
  const running = new Map<string, RunningDockApplication>()
  let runtime: RunningApplicationsState = { status: 'inactive', error: null, windows: [] }
  let signature = ''

  function findTarget(identity: string): RunningDockApplication | undefined {
    const applications = [...running.values()].filter(item => sameApplicationTarget(identity, item.key))
    const application = applications[0]

    if (!application || applications.length === 1) {
      return application
    }

    return {
      ...application,
      windows: applications.flatMap(item => item.windows).sort((a, b) => b.lastActive - a.lastActive)
    }
  }

  function apply(next: RunningApplicationsState): boolean {
    const nextSignature = JSON.stringify(next)

    if (nextSignature === signature) {
      return false
    }

    signature = nextSignature
    runtime = next

    if (next.status === 'inactive' || next.status === 'loading') {
      running.clear()

      return true
    }

    const seen = new Set<string>()

    for (const window of next.windows) {
      let application =
        running.get(window.appId) ?? [...running.values()].find(item => sameApplicationTarget(window.appId, item.key))

      if (!application) {
        application = {
          id: randomUUID(),
          key: window.appId,
          target: window.target,
          name: isPackagedAppTarget(window.target ?? '') ? window.title || window.name : window.name,
          icon: null,
          windows: [],
          enriched: false
        }
        running.set(window.appId, application)
      }

      if (!seen.has(application.key)) {
        application.windows = []
        seen.add(application.key)
      }

      application.windows.push(window)
    }

    for (const [key, application] of running) {
      if (!seen.has(key)) {
        running.delete(key)
      } else {
        application.windows.sort((a, b) => b.lastActive - a.lastActive)

        if (!application.windows.some(window => window.target === application.target)) {
          application.target = application.windows[0]?.target ?? null
          application.enriched = false
        }
      }
    }

    return true
  }

  return {
    apply,
    getByKey: (key: string): RunningDockApplication | undefined => running.get(key),
    findTarget,
    state: (): RunningApplicationsState => runtime,
    values: (): RunningDockApplication[] => [...running.values()]
  }
}
