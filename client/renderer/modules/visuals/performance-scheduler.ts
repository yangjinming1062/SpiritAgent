import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'

import type { VisualPlayCommand } from './types'

export type PerformanceSource = 'ambient' | 'life' | 'interaction' | 'manual'
export type PerformanceState = 'idle' | 'dispatching' | 'waiting' | 'playing' | 'returning'

export interface PerformanceRequest {
  performanceId: string
  source: PerformanceSource
  visualId: string
  visualVersion: number
  interruptible: boolean
  intentRevision: number | null
  createdAt: number
  expiresAt: number
  durationMs?: number | null
  cooldownKey?: string
  cooldownMs?: number
}

export interface PerformanceTask extends PerformanceRequest {
  priority: number
  playId: string | null
}

export interface PerformanceSnapshot {
  state: PerformanceState
  current: PerformanceTask | null
  command: VisualPlayCommand | null
}

export interface PerformancePorts {
  dispatch: (request: PerformanceRequest) => Promise<VisualPlayCommand>
  cancel: (command: VisualPlayCommand, reason: string) => Promise<void>
  restore: () => Promise<void>
  valid: (request: PerformanceRequest) => boolean
}

const IDLE: PerformanceSnapshot = { state: 'idle', current: null, command: null }
const PRIORITY: Record<PerformanceSource, number> = { ambient: 10, life: 20, interaction: 30, manual: 30 }
const QUEUE_LIMIT = 2

interface Running {
  task: PerformanceTask
  command: VisualPlayCommand | null
  dispatch: Promise<VisualPlayCommand> | null
  closing: boolean
}

/** 一个宿主一份运行态；HTTP 受理只推进到 waiting，播放器及回执负责后续推进。 */
export function createPerformanceScheduler(ports: PerformancePorts, changed: (state: PerformanceSnapshot) => void) {
  let active: Running | null = null
  let state: PerformanceState = 'idle'
  let enabled = true
  let blocked = false
  let disposed = false
  let timer: ReturnType<typeof setTimeout> | null = null
  const queue: PerformanceTask[] = []
  const seen = new Set<string>()
  const requested = new Set<string>()
  const cooldowns = new Map<string, number>()
  const failedVersions = new Set<string>()
  // WS 可能先于点播 HTTP 返回；受理身份明确后才允许舞台认领。
  let deferred: VisualPlayCommand | null = null

  function publish(): void {
    changed({ state, current: active?.task ?? null, command: state === 'returning' ? null : (active?.command ?? null) })
  }

  function remember(id: string, collection = seen): void {
    collection.add(id)
    const oldest = collection.values().next().value

    if (collection.size > 128 && oldest) {
      collection.delete(oldest)
    }
  }

  function clearTimer(): void {
    if (timer !== null) {
      clearTimeout(timer)
    }

    timer = null
  }

  function valid(task: PerformanceTask): boolean {
    return (
      enabled &&
      !disposed &&
      task.expiresAt > Date.now() &&
      ports.valid(task) &&
      (task.source === 'manual' || !failedVersions.has(`${task.visualId}:${task.visualVersion}`))
    )
  }

  function arm(delay: number, running: Running): void {
    clearTimer()
    timer = setTimeout(
      () => {
        if (active === running) {
          void close(running, 'Playback did not progress before its deadline')
        }
      },
      Math.max(1, delay)
    )
  }

  async function close(running: Running, reason?: string): Promise<void> {
    if (running.closing) {
      return
    }

    running.closing = true
    clearTimer()
    state = 'returning'
    publish()

    try {
      // 不在点播请求仍在途时提交下一段；迟到受理也必须结算。
      const command = running.command ?? (await running.dispatch?.catch(() => null))

      if (command) {
        remember(command.play_id)

        if (reason) {
          await ports.cancel(command, reason)
        }
      }
    } catch (error) {
      log.warn('performance', 'Performance recovery failed', error)
    } finally {
      if (!disposed) {
        await ports.restore().catch(error => log.warn('performance', 'Base presentation refresh failed', error))
      }

      if (active === running) {
        active = null
        state = 'idle'
        publish()
        const pending = deferred
        deferred = null

        if (pending) {
          receive(pending)
        }

        drain()
      }
    }
  }

  function waitForPlayback(running: Running, command: VisualPlayCommand): void {
    running.command = command
    running.task.playId = command.play_id
    remember(command.play_id)
    state = 'waiting'
    arm(Math.min(30_000, running.task.expiresAt - Date.now(), Date.parse(command.expires_at) - Date.now()), running)
    publish()
  }

  function drain(): void {
    if (active || blocked || !enabled || disposed) {
      return
    }

    let task = queue.shift()

    while (task && !valid(task)) {
      task = queue.shift()
    }

    if (!task) {
      return
    }

    const running: Running = { task, command: null, dispatch: null, closing: false }
    active = running
    state = 'dispatching'

    if (task.cooldownKey) {
      cooldowns.set(task.cooldownKey, Date.now() + (task.cooldownMs ?? 0))
    }

    running.dispatch = Promise.resolve().then(() => {
      if (running.closing || !valid(running.task)) {
        throw new Error('Performance cancelled before dispatch')
      }

      return ports.dispatch(running.task)
    })
    publish()
    void running.dispatch
      .then(command => {
        if (running.closing) {
          return
        }

        if (seen.has(command.play_id)) {
          void close(running)

          return
        }

        if (!valid(running.task) || command.visual_version !== running.task.visualVersion) {
          running.command = command
          void close(running, 'Performance changed before acceptance')

          return
        }

        waitForPlayback(running, command)
        const pending = deferred
        deferred = null

        if (pending && pending.play_id !== command.play_id) {
          receive(pending)
        }
      })
      .catch(error => {
        if (!running.closing) {
          log.warn('performance', 'Performance dispatch failed', error)
        }

        void close(running)
      })
  }

  function request(input: PerformanceRequest): boolean {
    const task: PerformanceTask = { ...input, priority: PRIORITY[input.source], playId: null }

    if (!valid(task) || blocked || requested.has(task.performanceId)) {
      return false
    }

    remember(task.performanceId, requested)

    if (
      task.cooldownKey &&
      ((cooldowns.get(task.cooldownKey) ?? 0) > Date.now() ||
        active?.task.cooldownKey === task.cooldownKey ||
        queue.some(item => item.cooldownKey === task.cooldownKey))
    ) {
      return false
    }

    // 同一生活修订只保留一个选择；队列按优先级保留最多两个待执行请求。
    if (task.source === 'life') {
      for (let i = queue.length - 1; i >= 0; i -= 1) {
        if (queue[i].source === 'life') {
          queue.splice(i, 1)
        }
      }
    }

    queue.push(task)
    queue.sort((a, b) => b.priority - a.priority || a.createdAt - b.createdAt)
    const capacity = QUEUE_LIMIT - (deferred && state !== 'dispatching' ? 1 : 0)
    const accepted = queue.indexOf(task) < capacity
    queue.splice(capacity)

    if (accepted && active && task.priority > active.task.priority && active.task.interruptible) {
      void close(active, 'Preempted by a higher priority performance')
    } else {
      drain()
    }

    return accepted
  }

  function receive(command: VisualPlayCommand, restoring = false): void {
    if (disposed || seen.has(command.play_id)) {
      return
    }

    if (restoring && !command.loop) {
      remember(command.play_id)
      void ports
        .cancel(command, 'A rebuilt stage does not replay short actions')
        .catch(error => log.warn('performance', 'Stale command receipt failed', error))

      return
    }

    if (state === 'dispatching') {
      if (
        !deferred ||
        command.presentation_epoch > deferred.presentation_epoch ||
        (command.presentation_epoch === deferred.presentation_epoch &&
          command.state_revision >= deferred.state_revision)
      ) {
        deferred = command
      }

      return
    }

    // 账本来源 → 调度来源：manual=用户点播；life=生活导演（可与互动竞争、可中断）。
    // 恢复沿组合来源继承 user/life；只有 base 恢复才回到 ambient 语义。
    const restoredSelection = command.source === 'restore' ? command.composition?.selection_source : undefined

    const source =
      command.source === 'life' || restoredSelection === 'life'
        ? 'life'
        : command.source === 'manual' || restoredSelection === 'user'
          ? 'manual'
          : 'ambient'

    const intentRevision = restoredSelection === 'life' ? (command.composition?.life_revision ?? null) : null

    const task: PerformanceTask = {
      performanceId: command.play_id,
      source,
      visualId: command.visual_id,
      visualVersion: command.visual_version,
      priority: PRIORITY[source],
      interruptible: source !== 'manual',
      intentRevision,
      playId: command.play_id,
      createdAt: Date.now(),
      expiresAt: Date.parse(command.expires_at)
    }

    if (
      !valid(task) ||
      blocked ||
      (active &&
        (task.priority < active.task.priority || (task.priority === active.task.priority && source !== 'manual')))
    ) {
      remember(command.play_id)
      void ports
        .cancel(command, 'Performance is no longer eligible')
        .catch(error => log.warn('performance', 'Rejected command receipt failed', error))

      return
    }

    if (active) {
      deferred = command
      queue.splice(QUEUE_LIMIT - 1)

      if (active.task.interruptible && task.priority > active.task.priority) {
        void close(active, 'Replaced by an explicit playback command')
      }

      return
    }

    const running: Running = { task, command, dispatch: null, closing: false }
    active = running
    waitForPlayback(running, command)
  }

  return {
    request,
    receive,
    sync(eligible: boolean, mechanical: boolean): void {
      enabled = eligible
      blocked = mechanical

      for (let i = queue.length - 1; i >= 0; i -= 1) {
        if (!valid(queue[i]) || blocked) {
          queue.splice(i, 1)
        }
      }

      if (
        active &&
        (!eligible ||
          blocked ||
          (active.task.source !== 'manual' &&
            (state !== 'playing' || active.task.interruptible) &&
            !ports.valid(active.task)))
      ) {
        void close(active, 'Stage or performance intent changed')
      }

      drain()
    },
    started(playId: string): void {
      if (!active || active.command?.play_id !== playId || state !== 'waiting') {
        return
      }

      state = 'playing'
      clearTimer()
      // 持续媒体以开播回执结算交接；短动作等 ended，停滞时有界退出。
      arm((active.task.durationMs ?? 300_000) + 30_000, active)
      publish()
    },
    settled(playId: string, failed = false): void {
      remember(playId)

      if (failed && active?.command?.play_id === playId) {
        failedVersions.add(`${active.task.visualId}:${active.task.visualVersion}`)
      }

      if (active?.command?.play_id === playId) {
        void close(active)
      }
    },
    interrupt(playId: string, reason: string): void {
      if (active?.command?.play_id === playId) {
        void close(active, reason)
      }
    },
    dispose(): void {
      disposed = true
      queue.length = 0
      deferred = null
      clearTimer()

      if (active) {
        void close(active, 'Presentation host released')
      }
    }
  }
}

export const $performance = atom<PerformanceSnapshot>(IDLE)
let scheduler: ReturnType<typeof createPerformanceScheduler> | null = null

export function bindPerformanceScheduler(ports: PerformancePorts): () => void {
  scheduler?.dispose()

  const next = createPerformanceScheduler(ports, state => {
    if (scheduler === next) {
      $performance.set(state)
    }
  })

  scheduler = next
  $performance.set(IDLE)

  return () => {
    if (scheduler === next) {
      scheduler = null
      $performance.set(IDLE)
    }

    next.dispose()
  }
}

export function requestPerformance(request: PerformanceRequest): boolean {
  return scheduler?.request(request) ?? false
}

export function receivePerformanceCommand(command: VisualPlayCommand, restoring = false): void {
  scheduler?.receive(command, restoring)
}

export function syncPerformance(eligible: boolean, mechanical = false): void {
  scheduler?.sync(eligible, mechanical)
}

export function performanceStarted(playId: string): void {
  scheduler?.started(playId)
}

export function performanceSettled(playId: string, failed = false): void {
  scheduler?.settled(playId, failed)
}

export function interruptPerformance(playId: string, reason: string): void {
  scheduler?.interrupt(playId, reason)
}
