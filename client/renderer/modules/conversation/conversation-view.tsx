import { useStore } from '@nanostores/react'
import { atom, computed } from 'nanostores'
import { createContext, type ReactNode, useContext, useEffect, useMemo } from 'react'

import { registerStorageClearHandler } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { getStrings } from '@/shared/strings'
import type { ConversationScrollAnchor, MainConversationDraftSnapshot } from '@client-shared/ipc/ui-state'

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
    $sending: atom(false),
    $scrollAnchor: atom<ConversationScrollAnchor | null>(null)
  }
}

type ConversationViewDraft = ReturnType<typeof createViewDraft>

function createViewController(runtime: ConversationRuntime, draft: ConversationViewDraft) {
  const { $chatEditDraft, $text, $pending, $externalPaths, $sending, $scrollAnchor } = draft

  const $retryableAssistantMessage = computed(
    [runtime.$retryableAssistantMessage, $chatEditDraft],
    (message, editing) => (editing ? null : message)
  )

  const $replaceableAssistantMessage = computed(
    [runtime.$replaceableAssistantMessage, $chatEditDraft],
    (message, editing) => (editing ? null : message)
  )

  const startEditingMessage = (messageId: string): void => {
    const sessionId = runtime.$chatSessionId.get()

    const message = runtime.$chatMessageList
      .get()
      .find(item => item.id === messageId && item.role === 'user' && item.subtype === undefined)

    const body = runtime.$chatMessageBodies.get()[messageId]

    if (
      !sessionId ||
      runtime.$chatSessionReadOnly.get() ||
      runtime.$chatTurnInFlight.get() ||
      runtime.$pendingPromptBatch.get().length > 0 ||
      !message?.backendMessageId ||
      !body
    ) {
      return
    }

    // 用户消息按空行拆泡时所有分段共用一个 backend id，附件只挂首个分段；从首段取回原附件供编辑期只读展示。
    const bodies = runtime.$chatMessageBodies.get()

    const attachments = runtime.$chatMessageList
      .get()
      .filter(item => item.backendMessageId === message.backendMessageId)
      .map(item => bodies[item.id]?.attachments)
      .find(Boolean)

    $chatEditDraft.set({
      sessionId,
      sourceMessageId: message.backendMessageId,
      text: stripAttachmentDirectives(body.editableText ?? body.text).trim(),
      attachments
    })
  }

  return {
    ...runtime,
    $chatEditDraft,
    $retryableAssistantMessage,
    $replaceableAssistantMessage,
    $text,
    $pending,
    $externalPaths,
    $sending,
    $scrollAnchor,
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
const mainDrafts = new Map<string | null, ConversationViewDraft>()
export const $conversationViews = atom<ConversationViewState[]>([])
export const $mainConversationDraftRevision = atom(0)

function mainDraftFor(sessionId: string | null, migratePending = true): ConversationViewDraft {
  let draft = mainDrafts.get(sessionId)

  if (!draft) {
    const pendingDraft = sessionId === null || !migratePending ? undefined : mainDrafts.get(null)
    draft = pendingDraft ?? createViewDraft()

    if (pendingDraft) {
      mainDrafts.delete(null)
    } else {
      for (const store of [
        draft.$text,
        draft.$pending,
        draft.$externalPaths,
        draft.$chatEditDraft,
        draft.$scrollAnchor
      ]) {
        store.listen(() => $mainConversationDraftRevision.set($mainConversationDraftRevision.get() + 1))
      }
    }

    mainDrafts.set(sessionId, draft)
  }

  return draft
}

export function exportMainConversationDraftSnapshot(): MainConversationDraftSnapshot {
  return structuredClone({
    selectedSessionId: $chatSessionId.get(),
    drafts: [...mainDrafts].map(([sessionId, draft]) => ({
      sessionId,
      text: draft.$text.get(),
      pending: draft.$pending.get(),
      externalPaths: draft.$externalPaths.get(),
      editing: draft.$chatEditDraft.get(),
      scrollAnchor: draft.$scrollAnchor.get()
    }))
  })
}

export function importMainConversationDraftSnapshot(snapshot: MainConversationDraftSnapshot): void {
  const incomingSessions = new Set(snapshot.drafts.map(entry => entry.sessionId))

  for (const [sessionId, draft] of mainDrafts) {
    if (!incomingSessions.has(sessionId)) {
      mainDrafts.delete(sessionId)
      draft.$text.set('')
      draft.$pending.set(null)
      draft.$externalPaths.set([])
      draft.$chatEditDraft.set(null)
      draft.$scrollAnchor.set(null)
    }
  }

  for (const entry of structuredClone(snapshot.drafts)) {
    const draft = mainDraftFor(entry.sessionId, false)
    draft.$text.set(entry.text)
    draft.$pending.set(
      entry.pending?.type === 'video' && entry.pending.status === 'uploading'
        ? { ...entry.pending, status: 'error', error: getStrings().chat.attachment.uploadInterrupted }
        : entry.pending
    )
    draft.$externalPaths.set(entry.externalPaths)
    draft.$chatEditDraft.set(entry.editing)
    draft.$scrollAnchor.set(entry.scrollAnchor)
  }
}

export function forgetConversationDraft(sessionId: string): void {
  mainDrafts.delete(sessionId)

  for (const key of drafts.keys()) {
    if (key.endsWith(`:${sessionId}`)) {
      drafts.delete(key)
    }
  }

  $mainConversationDraftRevision.set($mainConversationDraftRevision.get() + 1)
}

function controllerFor(viewId: string, runtime: ConversationRuntime, draftScope?: 'main'): ConversationViewController {
  if (draftScope === 'main') {
    return createViewController(runtime, mainDraftFor(runtime.$chatSessionId.get()))
  }

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
  draftScope,
  children
}: {
  viewId: string
  sessionId: string | null
  active?: boolean
  visible?: boolean
  foreground?: boolean
  receiveExternalAttachments?: boolean
  draftScope?: 'main'
  children: ReactNode
}): React.JSX.Element {
  const locked = useStore(presentationPorts().$screenLocked)
  const runtime = getConversationRuntime(sessionId)
  const historyHydrated = useStore(runtime.$historyHydrated)
  const controller = useMemo(() => controllerFor(viewId, runtime, draftScope), [viewId, runtime, draftScope])

  const state = useMemo<ConversationViewState>(
    () => ({
      viewId,
      runtime,
      controller,
      active,
      visible,
      foreground,
      eligible: sessionId !== null && historyHydrated && active && visible && foreground && !locked,
      scoped: true,
      receiveExternalAttachments
    }),
    [
      viewId,
      runtime,
      controller,
      sessionId,
      historyHydrated,
      active,
      visible,
      foreground,
      receiveExternalAttachments,
      locked
    ]
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

export function isConversationActive(sessionId: string | null): boolean {
  return $conversationViews.get().some(view => view.runtime.$chatSessionId.get() === sessionId && view.eligible)
}

registerStorageClearHandler(() => {
  drafts.clear()
  mainDrafts.clear()
  $mainConversationDraftRevision.set(0)
  $conversationViews.set([])
})
