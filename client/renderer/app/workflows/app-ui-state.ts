import {
  $appNavigationRevision,
  applyAppNavigation,
  getAppNavigation,
  readAppNavigationHash
} from '@/app/navigation/app-navigation'
import { companionEditorsUiHandoff } from '@/features/companion'
import {
  $chatSessionId,
  $conversationInputBusy,
  conversationUiHandoff,
  restoreConversationSelection,
  setChatSession
} from '@/features/conversation'
import { actionsUiHandoff } from '@/features/creation/actions'
import { sceneUiHandoff } from '@/features/creation/environment'
import { visualsUiHandoff } from '@/features/creation/visuals'
import { appearanceUiHandoff, wardrobeDesignUiHandoff } from '@/features/creation/wardrobe'
import { diaryUiHandoff, lifeUiHandoff } from '@/features/life'
import { log } from '@/shared/lib/log'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $surfaceRole } from '@/shared/store/surfaces'
import { $uiStateError, $uiStateReady, $uiStateRetry } from '@/shared/store/ui-state'
import { type MainUiStateData, type MainUiStateSnapshot, type UiStateRequest } from '@ipc/contracts'
import { errorMessage } from '@runtime'

const UI_STATE_HANDOFFS = [
  conversationUiHandoff,
  lifeUiHandoff,
  diaryUiHandoff,
  visualsUiHandoff,
  actionsUiHandoff,
  appearanceUiHandoff,
  sceneUiHandoff,
  wardrobeDesignUiHandoff,
  companionEditorsUiHandoff
] as const

export function installAppUiStateSync(): () => void {
  const mainView = $surfaceRole.get() !== 'sprite'
  const bridge = shellApi().uiState
  let initialTarget = readAppNavigationHash()
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
      life: lifeUiHandoff.capture(),
      diary: diaryUiHandoff.capture(),
      conversation: conversationUiHandoff.capture(),
      visuals: visualsUiHandoff.capture(),
      actions: actionsUiHandoff.capture(),
      appearance: appearanceUiHandoff.capture(),
      scene: sceneUiHandoff.capture(),
      wardrobeDesign: wardrobeDesignUiHandoff.capture(),
      companionEditors: companionEditorsUiHandoff.capture()
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
    explicitTarget?: MainUiStateSnapshot['navigation'],
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

    conversationUiHandoff.apply(snapshot.conversation)
    diaryUiHandoff.apply(snapshot.diary)
    visualsUiHandoff.apply(snapshot.visuals)
    actionsUiHandoff.apply(snapshot.actions)
    appearanceUiHandoff.apply(snapshot.appearance)
    sceneUiHandoff.apply(snapshot.scene)
    wardrobeDesignUiHandoff.apply(snapshot.wardrobeDesign)
    companionEditorsUiHandoff.apply(snapshot.companionEditors)

    if (selectedSessionId) {
      await restoreConversationSelection(selectedSessionId)
    } else {
      setChatSession(null)
    }

    assertLive(identity, token)
    const navigation = applyAppNavigation(explicitTarget ?? snapshot.navigation)
    const restoredNavigation = explicitTarget ? undefined : $appNavigationRevision.get()
    await navigation
    assertLive(identity, token)
    lifeUiHandoff.apply(snapshot.life, restoredNavigation)
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
        assertLive(identity, token)
        const explicitTarget = initialTarget

        const explicitSession = explicitTarget?.focus.page === 'chat' ? explicitTarget.focus.sessionId : undefined

        if (snapshot) {
          await apply(
            snapshot,
            identity,
            token,
            explicitTarget ?? undefined,
            explicitSession ?? snapshot.conversation.selectedSessionId
          )
        } else if (explicitTarget) {
          if (explicitSession) {
            await restoreConversationSelection(explicitSession)
          }

          assertLive(identity, token)
          await applyAppNavigation(explicitTarget)
        }

        assertLive(identity, token)
        initialTarget = null
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
      if (authSessionId) {
        initialTarget = null
      }

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
    ...UI_STATE_HANDOFFS.map(handoff => handoff.revision.listen(schedulePublish)),
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
