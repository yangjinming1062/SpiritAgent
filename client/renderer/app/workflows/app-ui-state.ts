import {
  $companionEditors,
  applyCompanionEditorUiSnapshot,
  captureCompanionEditorUiSnapshot
} from '@/app/features/companion/companion-editor-state'
import {
  $visualsUi,
  exportVisualsUiSnapshot,
  importVisualsUiSnapshot
} from '@/app/features/creation/visuals/visuals-state'
import { $diaryUi, exportDiaryUiSnapshot, importDiaryUiSnapshot } from '@/app/features/life/diary/diary-state'
import { $lifeUi, exportLifeUiSnapshot, importLifeUiSnapshot } from '@/app/features/life/overview/life-state'
import {
  $appNavigationRevision,
  applyAppNavigation,
  getAppNavigation,
  readAppNavigationHash
} from '@/app/navigation/app-navigation'
import {
  $chatSessionId,
  $conversationInputBusy,
  $mainConversationDraftRevision,
  exportMainConversationDraftSnapshot,
  importMainConversationDraftSnapshot,
  restoreConversationSelection,
  setChatSession
} from '@/modules/conversation'
import { log } from '@/shared/lib/log'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $surfaceRole } from '@/shared/store/surfaces'
import { $uiStateError, $uiStateReady, $uiStateRetry } from '@/shared/store/ui-state'
import {
  type AppNavigationTarget,
  composeNavigation,
  type MainUiStateData,
  type MainUiStateSnapshot,
  type PageFocus,
  type UiStateRequest
} from '@ipc/contracts'
import { errorMessage } from '@runtime'

export function installAppUiStateSync(): () => void {
  const mainView = $surfaceRole.get() !== 'sprite'
  const bridge = shellApi().uiState
  const search = new URLSearchParams(window.location.search)
  const hashTarget = readAppNavigationHash()
  const searchSession = search.get('sessionId')

  // 启动地址携带的定位参数优先于账户快照；没有地址目标时不覆盖已恢复的位置。
  const withSearchFocus = (target: AppNavigationTarget): AppNavigationTarget => {
    const focus: Record<string, string | undefined> = { ...target.focus }

    for (const key of ['sessionId', 'messageKey', 'pursuitId', 'outfitId', 'sceneId', 'visualId']) {
      const value = search.get(key)

      if (value) {
        focus[key] = value
      }
    }

    return composeNavigation(target.location, focus as PageFocus)
  }

  const initialTarget = hashTarget
    ? withSearchFocus(hashTarget)
    : searchSession
      ? composeNavigation(
          { page: 'chat' },
          { page: 'chat', sessionId: searchSession, messageKey: search.get('messageKey') ?? undefined }
        )
      : null

  let initialTargetConsumed = false
  let disposed = false
  let authSessionId: string | null = null
  let appliedRevision = -1
  let generation = 0
  let loading: Promise<void> = Promise.resolve()
  let responding: Promise<void> = Promise.resolve()
  let timer: ReturnType<typeof setTimeout> | undefined
  const pendingRequests: UiStateRequest[] = []

  function data(): MainUiStateData {
    return {
      navigation: getAppNavigation(),
      life: exportLifeUiSnapshot(),
      diary: exportDiaryUiSnapshot(),
      conversation: exportMainConversationDraftSnapshot(),
      visuals: exportVisualsUiSnapshot(),
      companionEditors: captureCompanionEditorUiSnapshot()
    }
  }

  function isLive(identity: string): boolean {
    const auth = $auth.get()

    return !disposed && auth.kind === 'authenticated' && auth.snapshot.sessionId === identity
  }

  function assertLive(identity: string, token: number): void {
    if (!isLive(identity) || token !== generation) {
      throw new Error('界面交接账户或恢复请求已变化。')
    }
  }

  function fail(error: unknown, identity: string, token: number): void {
    if (isLive(identity) && token === generation) {
      $uiStateReady.set(false)
      $uiStateError.set(errorMessage(error))
      log.warn('ui-state', 'snapshot restore failed', error)
    }
  }

  async function apply(
    snapshot: MainUiStateSnapshot,
    identity: string,
    token: number,
    selectedSessionId = snapshot.conversation.selectedSessionId
  ): Promise<void> {
    assertLive(identity, token)
    const auth = $auth.get()

    if (
      auth.kind !== 'authenticated' ||
      snapshot.accountId !== auth.snapshot.accountId ||
      snapshot.authSessionId !== identity ||
      snapshot.revision < appliedRevision
    ) {
      throw new Error('界面状态所属账户或版本已失效。')
    }

    importMainConversationDraftSnapshot(snapshot.conversation)
    importLifeUiSnapshot(snapshot.life)
    importDiaryUiSnapshot(snapshot.diary)
    importVisualsUiSnapshot(snapshot.visuals)

    applyCompanionEditorUiSnapshot(snapshot.companionEditors)

    if (selectedSessionId) {
      await restoreConversationSelection(selectedSessionId)
    } else {
      setChatSession(null)
    }

    assertLive(identity, token)
    await applyAppNavigation(snapshot.navigation)
    assertLive(identity, token)
    appliedRevision = snapshot.revision
  }

  function schedulePublish(): void {
    clearTimeout(timer)

    if (!mainView || !$uiStateReady.get() || !authSessionId) {
      return
    }

    const identity = authSessionId
    timer = setTimeout(() => {
      if (isLive(identity)) {
        void bridge.update({ ...data(), authSessionId: identity }).catch(error => {
          if (isLive(identity)) {
            log.warn('ui-state', 'snapshot save failed', error)
          }
        })
      }
    }, 200)
  }

  function publishBusy(): void {
    if (!authSessionId) {
      return
    }

    const identity = authSessionId
    void bridge.inputBusy(identity, $conversationInputBusy.get()).catch(error => {
      if (isLive(identity)) {
        log.warn('ui-state', 'input state failed', error)
      }
    })
  }

  function initialize(): void {
    const identity = authSessionId
    const token = ++generation
    clearTimeout(timer)
    $uiStateReady.set(false)
    $uiStateError.set(null)

    if (!identity) {
      return
    }

    publishBusy()
    loading = (async () => {
      if (mainView) {
        const snapshot = await bridge.get(identity)
        const explicitTarget = initialTargetConsumed ? null : initialTarget

        const explicitSession = explicitTarget?.focus.page === 'chat' ? explicitTarget.focus.sessionId : undefined

        if (snapshot) {
          await apply(snapshot, identity, token, explicitSession ?? snapshot.conversation.selectedSessionId)
        } else if (explicitSession) {
          await restoreConversationSelection(explicitSession)
        }

        assertLive(identity, token)

        if (!initialTargetConsumed) {
          if (initialTarget) {
            await applyAppNavigation(initialTarget)
          }

          assertLive(identity, token)
          initialTargetConsumed = true
        }

        await bridge.ready(identity, snapshot?.revision ?? 0)
      }

      assertLive(identity, token)
      $uiStateReady.set(true)
      schedulePublish()
    })()
      .catch(error => fail(error, identity, token))
      .finally(() => {
        const requests = pendingRequests.splice(0)

        for (const request of requests) {
          enqueue(request)
        }
      })
  }

  function authChanged(): void {
    const auth = $auth.get()
    const identity = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null

    if (authSessionId !== identity) {
      authSessionId = identity
      appliedRevision = -1
      initialize()
    }
  }

  async function respond(request: UiStateRequest): Promise<void> {
    let awaited: Promise<void>

    do {
      awaited = loading
      await awaited
    } while (!disposed && awaited !== loading)

    const auth = $auth.get()

    if (!mainView || disposed) {
      return
    }

    if (auth.kind !== 'authenticated') {
      pendingRequests.push(request)

      return
    }

    const identity = auth.snapshot.sessionId
    const token = generation

    if (request.authSessionId !== identity) {
      const failure = {
        requestId: request.requestId,
        authSessionId: request.authSessionId,
        error: '界面交接账户已变化。'
      }

      await bridge.reply(request.kind === 'capture' ? { ...failure, kind: 'capture' } : { ...failure, kind: 'restore' })

      return
    }

    if (request.kind === 'capture') {
      clearTimeout(timer)
      await bridge.reply({
        kind: 'capture',
        requestId: request.requestId,
        authSessionId: identity,
        ...($uiStateReady.get() ? { data: data() } : { error: $uiStateError.get() ?? '界面状态尚未恢复。' })
      })
    } else {
      try {
        clearTimeout(timer)
        $uiStateReady.set(false)
        $uiStateError.set(null)
        await apply(request.snapshot, identity, token)
        $uiStateReady.set(true)

        await bridge.reply({ kind: 'restore', requestId: request.requestId, authSessionId: identity })
        schedulePublish()
      } catch (error) {
        fail(error, identity, token)
        await bridge.reply({
          kind: 'restore',
          requestId: request.requestId,
          authSessionId: identity,
          error: errorMessage(error)
        })
      }
    }
  }

  function enqueue(request: UiStateRequest): void {
    responding = responding.then(() => respond(request)).catch(error => log.warn('ui-state', 'handoff failed', error))
  }

  const stop = [
    $auth.listen(authChanged),
    $uiStateRetry.listen(initialize),
    $conversationInputBusy.listen(publishBusy),
    $appNavigationRevision.listen(schedulePublish),
    $chatSessionId.listen(schedulePublish),
    $mainConversationDraftRevision.listen(schedulePublish),
    $visualsUi.listen(schedulePublish),
    $lifeUi.listen(schedulePublish),
    $diaryUi.listen(schedulePublish),
    $companionEditors.listen(schedulePublish),
    bridge.onRequest(enqueue)
  ]

  authChanged()

  return () => {
    disposed = true
    generation++
    clearTimeout(timer)
    pendingRequests.length = 0
    stop.forEach(dispose => dispose())
  }
}
