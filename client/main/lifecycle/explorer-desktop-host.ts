import type { ChildProcessWithoutNullStreams } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'

import type { RunningApplicationsState } from '../shared/desktop-applications'
import type { DesktopHostOperations, DesktopHostOptions } from '../shared/desktop-host'
import { createSerialQueue } from '../shared/utils'

import { createExternalActivation } from './host/activation'
import { isValidJumpListTarget, isValidJumpListTaskId, parseJumpListTasks } from './host/jump-list'
import { createHelperProcesses } from './host/process-monitor'
import { encodeHandle, encodeWindow, isPhysicalRect, isRunningWindow, type PendingRequest } from './host/protocol'

export function createExplorerDesktopHost(options: DesktopHostOptions): DesktopHostOperations {
  if (!path.isAbsolute(options.helperPath) || !path.isAbsolute(options.journalPath)) {
    throw new Error('Desktop helper and journal paths must be absolute')
  }

  const processes = createHelperProcesses({
    helperPath: options.helperPath,
    journalPath: options.journalPath
  })

  let state: 'idle' | 'starting' | 'running' | 'stopping' = 'idle'
  let child: ChildProcessWithoutNullStreams | null = null
  let nextId = 1
  let generation = 0
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  const serialize = createSerialQueue()
  let failureReported = false

  let applicationBatch: {
    revision: number
    parts: number
    nextPart: number
    bytes: number
    state: RunningApplicationsState
  } | null = null

  const pending = new Map<number, PendingRequest>()

  const activation = createExternalActivation({
    helperPath: options.helperPath,
    request,
    log: options.log,
    onWarning: options.onWarning,
    isCurrentHost: (host, hostGeneration) =>
      state === 'running' && generation === hostGeneration && child === host && !processes.exited(host) && !host.killed,
    exited: processes.exited,
    waitForExit: processes.waitForExit
  })

  function applicationsMessage(message: Record<string, unknown>): void {
    const { revision, parts, part, windows, status, error } = message

    if (
      typeof revision !== 'number' ||
      !Number.isSafeInteger(revision) ||
      revision < 1 ||
      typeof parts !== 'number' ||
      !Number.isSafeInteger(parts) ||
      parts < 1 ||
      parts > 128 ||
      typeof part !== 'number' ||
      !Number.isSafeInteger(part) ||
      part < 0 ||
      part >= parts ||
      (status !== 'ready' && status !== 'unavailable') ||
      (error !== null && typeof error !== 'string') ||
      !Array.isArray(windows) ||
      !windows.every(isRunningWindow)
    ) {
      throw new Error('Desktop helper returned an invalid application snapshot')
    }

    if (part === 0) {
      applicationBatch = { revision, parts, nextPart: 0, bytes: 0, state: { status, error, windows: [] } }
    }

    const batch = applicationBatch

    if (
      !batch ||
      batch.revision !== revision ||
      batch.parts !== parts ||
      batch.nextPart !== part ||
      batch.state.status !== status ||
      batch.state.error !== error
    ) {
      throw new Error('Desktop helper application snapshot is incomplete')
    }

    batch.bytes += Buffer.byteLength(JSON.stringify(message))

    if (batch.bytes > 2 * 1024 * 1024) {
      throw new Error('Desktop helper application snapshot exceeds 2 MiB')
    }

    batch.state.windows.push(...windows)
    batch.nextPart += 1

    if (batch.nextPart === parts) {
      applicationBatch = null
      options.onApplicationsChanged?.(batch.state)
    }
  }

  function clearHeartbeat(): void {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  function rejectPending(error: Error): void {
    for (const request of pending.values()) {
      clearTimeout(request.timer)
      request.reject(error)
    }

    pending.clear()
  }

  function reportFailure(reason: string): void {
    activation.clear()
    clearHeartbeat()
    rejectPending(new Error(reason))

    if (!failureReported && state === 'running') {
      failureReported = true
      options.log(`Explorer desktop host failed: ${reason}`)
      options.onFailure(reason)
    }
  }

  function request<T = void>(command: Record<string, unknown>, timeoutMs: number): Promise<T> {
    const process = child

    if (!process || processes.exited(process) || process.killed) {
      return Promise.reject(new Error('Desktop helper is not running'))
    }

    const id = nextId++

    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id)
        const reason = `Desktop helper ${String(command.command)} timed out`

        reject(new Error(reason))
      }, timeoutMs)

      timer.unref()
      pending.set(id, { reject, resolve: value => resolve(value as T), timer })
      process.stdin.write(`${JSON.stringify({ ...command, id })}\n`, error => {
        if (!error) {
          return
        }

        const waiting = pending.get(id)

        if (waiting) {
          pending.delete(id)
          clearTimeout(timer)
          waiting.reject(error)
        }
      })
    })
  }

  async function spawnHost(): Promise<void> {
    const currentGeneration = ++generation
    await new Promise<void>((resolve, reject) => {
      let ready = false

      const fail = (reason: string): void => {
        clearTimeout(timer)
        reject(new Error(reason))

        if (currentGeneration === generation) {
          reportFailure(reason)
        }
      }

      const timer = setTimeout(() => {
        fail('Desktop helper readiness timed out')

        if (currentGeneration === generation) {
          child?.kill()
        }
      }, 5_000)

      timer.unref()
      child = processes.launch('host', {
        failure: fail,
        close: (code, signal, stderr) =>
          fail(`Desktop helper exited (${code ?? signal ?? 'unknown'})${stderr ? `: ${stderr}` : ''}`),
        message: message => {
          if (currentGeneration !== generation) {
            return
          }

          if (message.event === 'host_ready' && !ready) {
            ready = true
            clearTimeout(timer)
            resolve()
          } else if (message.event === 'failure' && typeof message.reason === 'string') {
            fail(message.reason)
          } else if (
            message.event === 'foreground' &&
            typeof message.active === 'boolean' &&
            typeof message.stage_available === 'boolean' &&
            typeof message.fullscreen === 'boolean'
          ) {
            options.onForegroundChanged?.({
              active: message.active,
              stageAvailable: message.stage_available,
              fullscreen: message.fullscreen
            })
          } else if (message.event === 'warning' && typeof message.reason === 'string') {
            options.log(`[desktop] ${message.reason}`)

            if (message.notify !== false) {
              options.onWarning?.(message.reason)
            }
          } else if (message.event === 'applications') {
            applicationsMessage(message)
          } else if (message.event === 'external_focus') {
            activation.message(message)
          } else if (message.event === 'work_area') {
            if (!isPhysicalRect(message.work_area)) {
              throw new Error('Desktop helper returned an invalid work area')
            }

            options.onWorkAreaChanged?.({ ...message.work_area })
          } else if (message.event === 'system_tray') {
            const tray = message.tray

            if (tray === null) {
              options.onSystemTrayChanged?.(null)
            } else {
              if (
                !tray ||
                typeof tray !== 'object' ||
                Array.isArray(tray) ||
                !('bounds' in tray) ||
                !isPhysicalRect(tray.bounds) ||
                !('visible' in tray) ||
                typeof tray.visible !== 'boolean'
              ) {
                throw new Error('Desktop helper returned an invalid system tray')
              }

              options.onSystemTrayChanged?.({ bounds: { ...tray.bounds }, visible: tray.visible })
            }
          } else if (
            Number.isInteger(message.id) &&
            typeof message.id === 'number' &&
            message.id > 0 &&
            typeof message.ok === 'boolean'
          ) {
            const waiting = pending.get(message.id)

            if (!waiting) {
              return
            }

            pending.delete(message.id)
            clearTimeout(waiting.timer)

            if (message.ok) {
              waiting.resolve(message.result)
            } else {
              waiting.reject(
                new Error(typeof message.reason === 'string' ? message.reason : 'Desktop helper command failed')
              )
            }
          } else {
            throw new Error('Desktop helper returned an unsupported message')
          }
        }
      })
    })
  }

  async function stopHost(): Promise<void> {
    activation.clear()
    clearHeartbeat()
    state = 'stopping'
    const process = child
    let stopError: unknown

    if (process && !processes.exited(process)) {
      if (!process.killed) {
        try {
          await request({ command: 'stop' }, 7_000)
        } catch (error) {
          stopError = error
        }
      }

      if (!(await processes.waitForExit(process, 1_000))) {
        process.kill()

        if (!(await processes.waitForExit(process, 2_000))) {
          throw new Error('Desktop helper could not be stopped')
        }
      }
    }

    ++generation
    applicationBatch = null
    child = null
    rejectPending(new Error('Desktop helper stopped'))
    state = 'idle'

    if (stopError || existsSync(options.journalPath)) {
      try {
        await processes.recoverJournal()
      } catch (recoveryError) {
        if (!stopError) {
          throw recoveryError
        }

        const reason = stopError instanceof Error ? stopError.message : String(stopError)
        const recovery = recoveryError instanceof Error ? recoveryError.message : String(recoveryError)
        throw new Error(`${reason}; restoration: ${recovery}`)
      }
    }
  }

  return {
    setTrayLayout: (layout, eligible = () => true) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          return
        }

        if (layout !== null && (!isPhysicalRect(layout.bounds) || !isPhysicalRect(layout.clip))) {
          throw new Error('无效系统托盘布局。')
        }

        await request({ command: 'set_tray_layout', layout }, 4_000)
      })
    },
    setPalette: palette => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration) {
          return
        }

        await request({ command: 'set_palette', palette }, 4_000)
      })
    },
    focus: (handle, eligible = () => true) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration) {
          throw new Error('Desktop focus requires the current running host')
        }

        if (!eligible()) {
          return false
        }

        // helper 单线程命令循环里启动期首轮应用扫描可达秒级；超时只放弃本次激活，存活由心跳与 lease 兜底。
        await request({ command: 'focus', handle: encodeHandle(handle) }, 3_000)

        return true
      })
    },
    refreshApplications: eligible => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新选择程序。')
        }

        await request({ command: 'refresh_applications' }, 4_000)
      })
    },
    activateExternal: (windowId, allowRemote, eligible, continuation = eligible) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新选择窗口。')
        }

        await activation.activate(windowId, allowRemote, continuation, currentGeneration, child)
      })
    },
    cancelExternalInteraction: () => {
      activation.clear()

      if (state !== 'running' || !child || processes.exited(child) || child.killed) {
        return Promise.resolve()
      }

      return request({ command: 'cancel_external_interaction' }, 3_000)
    },
    launchExternal: (target, appId, eligible) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新打开程序。')
        }

        if (
          typeof target !== 'string' ||
          target.length < 1 ||
          target.length > 32_768 ||
          typeof appId !== 'string' ||
          appId.length < 1 ||
          appId.length > 32_768
        ) {
          throw new Error('无效程序目标。')
        }

        await request({ command: 'launch_external', target, app_id: appId }, 4_000)
      })
    },
    closeExternal: (windowIds, eligible) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新选择窗口。')
        }

        await request({ command: 'close_external', window_ids: windowIds }, 3_000)
      })
    },
    getJumpListTasks: async (target, eligible) => {
      const currentGeneration = generation

      if (state !== 'running' || generation !== currentGeneration || !eligible()) {
        throw new Error('桌面已改变，请重新读取程序任务。')
      }

      if (!isValidJumpListTarget(target)) {
        throw new Error('无效程序目标。')
      }

      const result = await request<unknown>({ command: 'get_jump_list_tasks', target }, 5_000)

      if (state !== 'running' || generation !== currentGeneration || !eligible()) {
        throw new Error('桌面已改变，请重新读取程序任务。')
      }

      return parseJumpListTasks(result)
    },
    activateJumpListTask: (target, taskId, eligible) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新执行程序任务。')
        }

        if (!isValidJumpListTarget(target) || !isValidJumpListTaskId(taskId)) {
          throw new Error('无效程序任务。')
        }

        await request(
          { command: 'activate_jump_list_task', target, task_id: taskId, expires_at: Date.now() + 5_000 },
          5_000
        )
      })
    },
    recover: () =>
      serialize(async () => {
        if (state !== 'idle') {
          throw new Error('Desktop recovery requires an idle host')
        }

        return processes.recoverJournal()
      }),
    start: input =>
      serialize(async () => {
        if (process.platform !== 'win32') {
          throw new Error('Explorer desktop hosting is available on Windows only')
        }

        if (state !== 'idle') {
          throw new Error('Desktop host has already started')
        }

        if (processes.recoveryRunning()) {
          throw new Error('Previous desktop recovery helper has not stopped')
        }

        if (
          input.parentPid !== process.pid ||
          input.windows.length !== 2 ||
          input.windows.filter(window => window.role === 'background').length !== 1 ||
          input.windows.filter(window => window.role === 'overlay').length !== 1
        ) {
          throw new Error('Desktop host requires the current Electron process, one background and one overlay')
        }

        const windows = input.windows.map(encodeWindow)

        if (
          windows.some(
            window =>
              window.bounds.x !== windows[0].bounds.x ||
              window.bounds.y !== windows[0].bounds.y ||
              window.bounds.width !== windows[0].bounds.width ||
              window.bounds.height !== windows[0].bounds.height
          )
        ) {
          throw new Error('Desktop windows must share the interaction display')
        }

        state = 'starting'
        failureReported = false
        applicationBatch = null

        try {
          await spawnHost()
          await request(
            {
              command: 'start',
              parent_pid: input.parentPid,
              takeover: input.takeover,
              palette: input.palette,
              windows,
              work_area: input.workArea
            },
            15_000
          )
          state = 'running'
          heartbeatTimer = setInterval(() => {
            void request({ command: 'heartbeat' }, 4_000).catch(error => {
              reportFailure(error instanceof Error ? error.message : String(error))
            })
          }, 2_000)
          heartbeatTimer.unref()
        } catch (error) {
          try {
            await stopHost()
          } catch (cleanupError) {
            const reason = error instanceof Error ? error.message : String(error)
            const cleanup = cleanupError instanceof Error ? cleanupError.message : String(cleanupError)
            throw new Error(`${reason}; restoration: ${cleanup}`)
          }

          throw error
        }
      }),
    stop: () => serialize(stopHost)
  }
}
