import { deepEqual } from '@/shared/lib/deep-equal'
import { log } from '@/shared/lib/log'
import type { DesktopWindowSceneSnapshot } from '@ipc/contracts'

import type { SpatialPeek, WindowPeekLayout } from '../spatial-peek'

import type { Position, WindowPeekBinding } from './types'

type SceneWindow = DesktopWindowSceneSnapshot['windows'][number]

interface WindowTrackerPorts {
  getPeek: () => SpatialPeek | null
  getPosition: () => Position
  getScale: () => number
  getGeneration: () => number
  isStageActive: () => boolean
  readScene: () => Promise<DesktopWindowSceneSnapshot | null>
  moveToDisplay: (position: Position) => Promise<void>
  buildLayout: (
    scene: DesktopWindowSceneSnapshot,
    target: SceneWindow,
    action: 'peek_left' | 'peek_right'
  ) => WindowPeekLayout | null
  applyLayout: (layout: WindowPeekLayout) => void
  leave: () => void
}

export function findBoundWindow(
  scene: DesktopWindowSceneSnapshot | null,
  binding: WindowPeekBinding
): SceneWindow | undefined {
  return scene?.runnerInstanceId === binding.runnerInstanceId
    ? scene.windows.find(item => item.visible && item.id === binding.windowId && item.pid === binding.windowPid)
    : undefined
}

/** 单飞读取冻结窗口；停止不提前释放在途读取，以免跨代次请求并发。 */
export function createWindowPeekTracker(ports: WindowTrackerPorts) {
  let timer: ReturnType<typeof setInterval> | null = null
  let snapshotPending = false

  function stop(): void {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  async function update(): Promise<void> {
    if (snapshotPending || ports.getPeek()?.mode !== 'window') {
      return
    }

    snapshotPending = true
    const generation = ports.getGeneration()

    try {
      const peek = ports.getPeek()

      if (!peek || peek.mode !== 'window') {
        return
      }

      if (!ports.isStageActive()) {
        ports.leave()

        return
      }

      const scene = await ports.readScene()

      if (generation !== ports.getGeneration() || ports.getPeek() !== peek) {
        return
      }

      const target = findBoundWindow(scene, peek)

      if (!scene || !target) {
        ports.leave()

        return
      }

      if (target.displayId !== scene.viewport.displayId) {
        await ports.moveToDisplay({ x: target.x + target.w / 2, y: target.y + target.h / 2 })

        return
      }

      const layout = ports.buildLayout(scene, target, peek.action)

      if (!layout) {
        ports.leave()

        return
      }

      // 外部位置回写后即使窗口几何不变也重新落位，保留自愈行为。
      if (
        deepEqual(ports.getPeek(), layout.peek) &&
        deepEqual(ports.getPosition(), layout.position) &&
        ports.getScale() === layout.scale
      ) {
        return
      }

      ports.applyLayout(layout)
    } catch (error) {
      log.warn('spatial', 'Could not follow window peek', error)

      if (generation === ports.getGeneration()) {
        ports.leave()
      }
    } finally {
      snapshotPending = false
    }
  }

  return {
    start: (): void => {
      stop()
      timer = setInterval(() => void update(), 100)
    },
    stop,
    update
  }
}
