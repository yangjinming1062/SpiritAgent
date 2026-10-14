import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import {
  $chatSessionId,
  $conversationViews,
  beginConversationInputActivity,
  pushExternalAttachment,
  useConversationView
} from '@/modules/conversation'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'

interface FeedRecipient {
  viewId: string
  sessionId: string
  controller: ReturnType<typeof useConversationView>['controller']
  authSessionId: string
  authCurrent: () => boolean
}

let request: Promise<void> | null = null
let requestRecipient: FeedRecipient | null = null
let queuedRecipient: FeedRecipient | null = null

function recipientIsEligible(recipient: FeedRecipient): boolean {
  return (
    recipient.authCurrent() &&
    $chatSessionId.get() === recipient.sessionId &&
    $conversationViews
      .get()
      .some(
        view =>
          view.viewId === recipient.viewId &&
          view.eligible &&
          view.receiveExternalAttachments &&
          view.controller.$historyHydrated.get() &&
          !view.controller.$chatSessionReadOnly.get() &&
          view.runtime.$chatSessionId.get() === recipient.sessionId &&
          view.controller.$externalPaths === recipient.controller.$externalPaths
      )
  )
}

function receivePendingFeed(recipient: FeedRecipient, fromSignal = false): void {
  if (!recipientIsEligible(recipient)) {
    return
  }

  if (request) {
    const owner = requestRecipient

    if (
      fromSignal ||
      !owner ||
      owner.authSessionId !== recipient.authSessionId ||
      owner.viewId !== recipient.viewId ||
      owner.sessionId !== recipient.sessionId ||
      owner.controller.$externalPaths !== recipient.controller.$externalPaths
    ) {
      queuedRecipient = recipient
    }

    return
  }

  const finishPreparation = beginConversationInputActivity('uploading')
  requestRecipient = recipient
  request = Promise.resolve().then(async () => {
    try {
      if (!recipient.authCurrent()) {
        return
      }

      const paths = await shellApi().chat.takePendingFeed(recipient.authSessionId)

      if (!recipient.authCurrent() || paths.length === 0) {
        return
      }

      if (recipientIsEligible(recipient)) {
        pushExternalAttachment(paths)
      } else {
        // take 已清空信箱；卸载或切换会话后仍将附件交给来源草稿。
        const { $externalPaths } = recipient.controller
        $externalPaths.set([...$externalPaths.get(), ...paths])
      }
    } catch (error) {
      if (recipient.authCurrent()) {
        log.warn('conversation-feed', 'Could not receive pending attachments', error)
      }
    } finally {
      finishPreparation()
      request = null
      requestRecipient = null
      const next = queuedRecipient
      queuedRecipient = null

      if (next) {
        receivePendingFeed(next)
      }
    }
  })
}

export function usePendingConversationFeed(): void {
  const view = useConversationView()
  const sessionId = useStore(view.controller.$chatSessionId)
  const readOnly = useStore(view.controller.$chatSessionReadOnly)
  const hydrated = useStore(view.controller.$historyHydrated)
  const auth = useStore($auth)
  const authSessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null

  useEffect(() => {
    const authCurrent = captureAuthScope()

    if (
      !view.eligible ||
      !view.receiveExternalAttachments ||
      !hydrated ||
      readOnly ||
      !sessionId ||
      !authSessionId ||
      !authCurrent
    ) {
      return
    }

    const recipient: FeedRecipient = {
      viewId: view.viewId,
      sessionId,
      controller: view.controller,
      authSessionId,
      authCurrent
    }

    let disposed = false

    const scheduleReceive = (fromSignal = false): void => {
      queueMicrotask(() => {
        if (!disposed) {
          receivePendingFeed(recipient, fromSignal)
        }
      })
    }

    const stop = shellApi().chat.onPendingFeed(() => scheduleReceive(true))

    // Provider 的登记 effect 与 StrictMode 重挂载完成后才读取信箱。
    scheduleReceive()

    return () => {
      disposed = true
      stop()
    }
  }, [
    view.eligible,
    view.receiveExternalAttachments,
    view.viewId,
    view.controller,
    sessionId,
    hydrated,
    readOnly,
    authSessionId
  ])
}

registerStorageClearHandler(() => {
  queuedRecipient = null
})
