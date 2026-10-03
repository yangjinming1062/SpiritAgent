import { useStore } from '@nanostores/react'
import { atom, computed } from 'nanostores'
import { createContext, type ReactNode, useContext, useEffect, useMemo } from 'react'

import { registerStorageClearHandler } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'

import { stripAttachmentDirectives } from './chat-display-text'
import { type ConversationRuntime } from './chat-runtime'
import { $chatSessionId, getConversationRuntime, retainConversationRuntime } from './chat-store'
import type { ChatEditDraft, PendingAttachment } from './chat-store'

function createViewDraft() {
  return {
    $chatEditDraft: atom<ChatEditDraft | null>(null),
    $text: atom(''),
    $pending: atom<PendingAttachment | null>(null),
    $externalPaths: atom<string[]>([]),
    $sending: atom(false)
  }
}

type ConversationViewDraft = ReturnType<typeof createViewDraft>

function createViewController(runtime: ConversationRuntime, draft: ConversationViewDraft) {
  const { $chatEditDraft, $text, $pending, $externalPaths, $sending } = draft

  const $retryableAssistantMessage = computed(
    [runtime.$retryableAssistantMessage, $chatEditDraft],
    (message, editing) => (editing ? null : message)
  )

  const startEditingMessage = (messageId: string): void => {
    const message = runtime.$lastEditableUserMessage.get()
    const sessionId = runtime.$chatSessionId.get()
    const body = runtime.$chatMessageBodies.get()[messageId]

    if (!sessionId || message?.id !== messageId || !message.backendMessageId || !body) {
      return
    }

    $chatEditDraft.set({
      sessionId,
      sourceMessageId: message.backendMessageId,
      text: stripAttachmentDirectives(body.editableText ?? body.text).trim()
    })
  }

  return {
    ...runtime,
    $chatEditDraft,
    $retryableAssistantMessage,
    $text,
    $pending,
    $externalPaths,
    $sending,
    startEditingMessage
  }
}

export type ConversationViewController = ReturnType<typeof createViewController>
export interface ConversationViewState {
  viewId: string
  runtime: ConversationRuntime
  controller: ConversationViewController
  active: boolean
  visible: boolean
  foreground: boolean
  eligible: boolean
  scoped: boolean
  receiveExternalAttachments: boolean
}
const ConversationViewContext = createContext<ConversationViewState | null>(null)
const drafts = new Map<string, ConversationViewDraft>()
export const $conversationViews = atom<ConversationViewState[]>([])

function controllerFor(viewId: string, runtime: ConversationRuntime): ConversationViewController {
  const key = `${viewId}:${runtime.$chatSessionId.get() ?? 'pending'}`
  let draft = drafts.get(key)

  if (!draft) {
    const pendingKey = `${viewId}:pending`
    draft = runtime.$chatSessionId.get() === null ? undefined : drafts.get(pendingKey)

    if (draft) {
      drafts.delete(pendingKey)
    } else {
      draft = createViewDraft()
    }

    drafts.set(key, draft)
  }

  return createViewController(runtime, draft)
}

export function ConversationViewProvider({
  viewId,
  sessionId,
  active = true,
  visible = true,
  foreground = true,
  receiveExternalAttachments = false,
  children
}: {
  viewId: string
  sessionId: string | null
  active?: boolean
  visible?: boolean
  foreground?: boolean
  receiveExternalAttachments?: boolean
  children: ReactNode
}): React.JSX.Element {
  const locked = useStore(presentationPorts().$screenLocked)
  const runtime = getConversationRuntime(sessionId)
  const controller = useMemo(() => controllerFor(viewId, runtime), [viewId, runtime])

  const state = useMemo<ConversationViewState>(
    () => ({
      viewId,
      runtime,
      controller,
      active,
      visible,
      foreground,
      eligible: sessionId !== null && active && visible && foreground && !locked,
      scoped: true,
      receiveExternalAttachments
    }),
    [viewId, runtime, controller, sessionId, active, visible, foreground, receiveExternalAttachments, locked]
  )

  useEffect(() => retainConversationRuntime(runtime), [runtime])
  useEffect(() => {
    $conversationViews.set([...$conversationViews.get().filter(view => view.viewId !== viewId), state])

    return () => {
      $conversationViews.set($conversationViews.get().filter(view => view !== state))
    }
  }, [state, viewId, runtime])

  return <ConversationViewContext.Provider value={state}>{children}</ConversationViewContext.Provider>
}

export function useConversationView(): ConversationViewState {
  const provided = useContext(ConversationViewContext)
  const sessionId = useStore(provided?.runtime.$chatSessionId ?? $chatSessionId)
  const runtime = provided?.runtime ?? getConversationRuntime(sessionId)

  const controller = useMemo(
    () => provided?.controller ?? controllerFor('window', runtime),
    [provided?.controller, runtime]
  )

  useEffect(() => (provided ? undefined : retainConversationRuntime(runtime)), [provided, runtime])

  return (
    provided ?? {
      viewId: 'window',
      runtime,
      controller,
      active: true,
      visible: true,
      foreground: true,
      eligible: true,
      scoped: false,
      receiveExternalAttachments: true
    }
  )
}

export function isConversationVisible(sessionId: string | null): boolean {
  return $conversationViews
    .get()
    .some(view => view.runtime.$chatSessionId.get() === sessionId && view.visible && view.foreground)
}

export function isConversationActive(sessionId: string | null): boolean {
  return $conversationViews.get().some(view => view.runtime.$chatSessionId.get() === sessionId && view.eligible)
}

registerStorageClearHandler(() => {
  drafts.clear()
  $conversationViews.set([])
})
