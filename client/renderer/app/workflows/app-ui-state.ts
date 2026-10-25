import {
  $companionEditors,
  applyCompanionEditorUiSnapshot,
  captureCompanionEditorUiSnapshot
} from '@/app/features/companion/companion-editor-state'
import { $lifeUi, exportLifeUiSnapshot, importLifeUiSnapshot } from '@/app/features/life/life-state'
import { $visualsUi, exportVisualsUiSnapshot, importVisualsUiSnapshot } from '@/app/features/visuals/visuals-state'
import {
  $appNavigationRevision,
  $appPage,
  $settingsSection,
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
import { $presentation } from '@/shared/store/presentation'
import { $surfaceRole } from '@/shared/store/surfaces'
import { $uiStateError, $uiStateReady, $uiStateRetry } from '@/shared/store/ui-state'
import {
  type MainUiStateData,
  type MainUiStateSnapshot,
  normalizeAppNavigation,
  type UiStateRequest
} from '@ipc/contracts'
import { errorMessage } from '@runtime'

export function installAppUiStateSync(): () => void {
  const mainView = $surfaceRole.get() !== 'sprite'
  const bridge = shellApi().uiState
  const search = new URLSearchParams(window.location.search)
  const hashTarget = readAppNavigationHash()

  const initialTarget = normalizeAppNavigation({
    ...hashTarget,
    sessionId: search.get('sessionId') ?? hashTarget?.sessionId,
    messageKey: search.get('messageKey') ?? hashTarget?.messageKey,
    pursuitId: search.has('pursuitId') ? Number(search.get('pursuitId')) : hashTarget?.pursuitId
  })

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

        if (snapshot) {
          await apply(snapshot, identity, token, explicitTarget?.sessionId ?? snapshot.conversation.selectedSessionId)
        } else if (explicitTarget?.sessionId) {
          await restoreConversationSelection(explicitTarget.sessionId)
        }

        assertLive(identity, token)

        if (!initialTargetConsumed) {
          if (initialTarget.page || initialTarget.section || initialTarget.sessionId) {
            const { sessionId, ...target } = initialTarget
            await applyAppNavigation({ ...target, page: target.page ?? (sessionId ? 'chat' : undefined) })
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
    $appPage.listen(schedulePublish),
    $settingsSection.listen(schedulePublish),
    $appNavigationRevision.listen(schedulePublish),
    $chatSessionId.listen(schedulePublish),
    $mainConversationDraftRevision.listen(schedulePublish),
    $visualsUi.listen(schedulePublish),
    $lifeUi.listen(schedulePublish),
    $companionEditors.listen(schedulePublish),
    $presentation.listen(schedulePublish),
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
