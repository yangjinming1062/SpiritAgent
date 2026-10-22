import { useStore } from '@nanostores/react'
import { type Dispatch, type SetStateAction, useCallback, useRef } from 'react'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { parseSlashInput } from '@/shared/lib/slash-commands'
import { currentClearEpoch } from '@/shared/lib/storage'
import { confirmAction } from '@/shared/panel'
import { presentationPorts } from '@/shared/presentation-ports'
import { notify, notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import { errorMessage } from '@runtime'

import { appendLine, prepareChatAttachmentContent } from './chat-attachment-content'
import { basename } from './chat-path'
import { submitConversationTurn } from './chat-requests'
import type { ConversationRuntime } from './chat-runtime'
import { executeSlashCommand, slashPreCheck } from './chat-slash'
import {
  $chatSessionId as $selectedChatSessionId,
  type ChatEditDraft,
  getConversationRuntime,
  type PendingAttachment
} from './chat-store'
import { beginConversationInputActivity } from './conversation-input-busy'
import { useConversationView } from './conversation-view'
import { ensureChatSession } from './session-list-store'
import { conversationVoiceSink } from './voice-link'

interface UseChatSubmitOptions {
  externalPaths: string[]
  isReadOnlySession: boolean
  onClearExternalPaths: () => void
  onPreCheckFail: (message: string) => void
}

export interface ChatSubmit {
  cancelEdit: () => void
  editing: ChatEditDraft | null
  handleStop: () => Promise<void>
  pending: PendingAttachment | null
  sending: boolean
  stopping: boolean
  send: () => Promise<void>
  setPending: Dispatch<SetStateAction<PendingAttachment | null>>
  setText: Dispatch<SetStateAction<string>>
  text: string
}

export function useChatSubmit({
  externalPaths,
  isReadOnlySession,
  onClearExternalPaths,
  onPreCheckFail
}: UseChatSubmitOptions): ChatSubmit {
  const view = useConversationView()
  const controller = view.controller

  const { $chatEditDraft, $chatMessageList, $chatSessionId, $chatTurnInFlight, $pendingPromptBatch } = controller

  const text = useStore(controller.$text)
  const pending = useStore(controller.$pending)
  const sending = useStore(controller.$sending)
  const stoppingRequestId = useStore(controller.$stoppingRequestId)

  const setText: Dispatch<SetStateAction<string>> = useCallback(
    next => {
      controller.$text.set(typeof next === 'function' ? next(controller.$text.get()) : next)
    },
    [controller]
  )

  const setSending = useCallback((value: boolean) => controller.$sending.set(value), [controller])
  const ownerRef = useRef(controller)
  ownerRef.current = controller
  const controllerEpoch = currentClearEpoch()
  const editing = useStore($chatEditDraft)

  const setPending: Dispatch<SetStateAction<PendingAttachment | null>> = useCallback(
    next => {
      if (currentClearEpoch() !== controllerEpoch) {
        return
      }

      controller.$pending.set(typeof next === 'function' ? next(controller.$pending.get()) : next)
    },
    [controller, controllerEpoch]
  )

  // externalPaths 已是值类型，但仍走 ref：send 是异步的，期间用户继续拖入文件会改变 externalPaths 的引用。回调创建时闭包里的快照已过期，必须读 ref 才能拿到发送瞬间的最新列表。
  const externalPathsRef = useRef(externalPaths)
  externalPathsRef.current = externalPaths

  const send = useCallback(async (): Promise<void> => {
    const authScope = captureAuthScope()

    if (!view.eligible || !controller.isCurrent() || !authScope) {
      return
    }

    const requestCurrent = authScope

    const edit = $chatEditDraft.get()
    const currentText = edit?.text ?? controller.$text.get()
    const currentPending = edit ? null : controller.$pending.get()
    const currentSending = controller.$sending.get()

    if (isReadOnlySession) {
      return
    }

    const trimmed = currentText.trim()

    if (currentSending) {
      return
    }

    if (currentPending?.type === 'video' && currentPending.status !== 'ready') {
      const submit = getStrings().chat.submit
      notify({
        durationMs: 3000,
        kind: currentPending.status === 'error' ? 'error' : 'info',
        message: currentPending.status === 'error' ? submit.videoUploadFailed : submit.videoUploading
      })

      return
    }

    if (!trimmed && !currentPending && externalPathsRef.current.length === 0) {
      return
    }

    if (!edit && trimmed === '/') {
      return
    }

    if (edit) {
      // 编辑时效检查：草稿归属、在途回合与源消息须同时成立；确认弹窗等待期间历史可能被事件改写，提交前复用同一检查。
      const editSourceIndex = (): number =>
        $chatMessageList
          .get()
          .findLastIndex(
            item => item.role === 'user' && item.backendMessageId === edit.sourceMessageId && item.subtype === undefined
          )

      const editStale = (sourceIndex: number): boolean =>
        edit.sessionId !== $chatSessionId.get() ||
        $chatTurnInFlight.get() ||
        $pendingPromptBatch.get().length > 0 ||
        sourceIndex < 0

      const sourceIndex = editSourceIndex()

      if (editStale(sourceIndex)) {
        onPreCheckFail(getStrings().chat.edit.stale)

        return
      }

      const hasLaterUserMessage = $chatMessageList
        .get()
        .slice(sourceIndex + 1)
        .some(item => item.role === 'user' && item.backendMessageId !== undefined && item.subtype === undefined)

      if (hasLaterUserMessage) {
        const editStrings = getStrings().chat.edit

        if (
          !(await confirmAction({
            confirmLabel: editStrings.confirmLabel,
            description: editStrings.confirmDescription,
            title: editStrings.confirmTitle
          }))
        ) {
          return
        }
      }

      // 确认期间所属会话或网关已变则静默放弃；历史被改写时提示重新选择。
      if (!requestCurrent() || !controller.isCurrent()) {
        return
      }

      if (editStale(editSourceIndex())) {
        onPreCheckFail(getStrings().chat.edit.stale)

        return
      }

      setSending(true)
      $chatTurnInFlight.set(true)
      conversationVoiceSink().cancel($chatSessionId.get())

      const submission = controller.preparePromptSubmission({
        session_id: edit.sessionId,
        edit_message_id: edit.sourceMessageId,
        response_preference: presentationPorts().getResponsePreference(),
        text: trimmed
      })

      try {
        controller.acceptPromptSubmission(await submitConversationTurn(submission))

        if ($chatEditDraft.get() === edit) {
          $chatEditDraft.set(null)
        }
      } catch (err) {
        controller.submissionFailed(submission, err)

        // 已收到修订事件时，服务端已接受；迟到的请求失败不能中止新回合。
        if (
          requestCurrent() &&
          controller.isCurrent() &&
          $chatSessionId.get() === edit.sessionId &&
          $chatEditDraft.get() === edit &&
          controller.isPromptUnconfirmed(submission.request_id) &&
          !controller.cancellationRequested(submission.request_id)
        ) {
          $chatTurnInFlight.set(false)
          notifyError(err, getStrings().chat.edit.failed)
        }
      } finally {
        setSending(false)
      }

      return
    }

    const parsed = parseSlashInput(trimmed)

    if (parsed) {
      const preCheck = slashPreCheck(currentPending, currentSending)

      if (preCheck) {
        onPreCheckFail(preCheck)

        return
      }

      if (parsed.command) {
        await executeSlashCommand(parsed.command, parsed.args, {
          onFinish: () => setSending(false),
          onStart: () => {
            setSending(true)
            setText('')
          },
          runtime: controller
        })

        return
      }

      onPreCheckFail(getStrings().chat.submit.unknownCommand(parsed.name))

      return
    }

    setSending(true)
    conversationVoiceSink().cancel($chatSessionId.get())

    let id: string | null = null
    const initialSessionId = $chatSessionId.get()
    const epoch = currentClearEpoch()
    let target: ConversationRuntime = view.runtime
    const finishPreparation = currentPending?.type === 'image' ? beginConversationInputActivity('uploading') : undefined

    try {
      const submit = getStrings().chat.submit
      id = await ensureChatSession(controller)
      target = initialSessionId === null ? getConversationRuntime(id) : view.runtime
      const content = await prepareChatAttachmentContent(trimmed, currentPending)
      let fullText = content.displayText
      let promptText = content.promptText

      // 等待期间切走会话时不提交，正文与附件仍归来源会话的草稿。
      if (
        !requestCurrent() ||
        !target.isCurrent() ||
        epoch !== currentClearEpoch() ||
        (ownerRef.current !== controller && ownerRef.current.$text !== controller.$text) ||
        target.$chatSessionId.get() !== id ||
        (initialSessionId === null && !view.scoped && $selectedChatSessionId.get() !== id)
      ) {
        return
      }

      const extra = externalPathsRef.current

      if (extra.length > 0) {
        const names = extra.map(basename).join(submit.attachmentsJoiner)
        const heading = submit.attachmentsHeading(names)
        fullText = appendLine(fullText, heading)
        const extraDirectives = extra.map(p => `@file:${p}`).join('\n')
        promptText = appendLine(promptText, extraDirectives)
      }

      externalPathsRef.current = []
      onClearExternalPaths()

      target.pushUserMessage(fullText || content.displayFallback || '', content.displayAttachments)
      setText('')
      controller.$pending.set(null)

      if (!target.$chatTurnInFlight.get()) {
        presentationPorts().setSpriteState('listening')
      }

      target.pushPendingPrompt({
        attachments: content.attachments,
        text: promptText || content.promptFallback || ''
      })
      target.schedulePendingFlush()
    } catch (err) {
      if (
        requestCurrent() &&
        target.isCurrent() &&
        epoch === currentClearEpoch() &&
        (ownerRef.current === controller || ownerRef.current.$text === controller.$text) &&
        (id === null || target.$chatSessionId.get() === id)
      ) {
        target.markAssistantTerminal({ error: errorMessage(err, getStrings().chat.sendFailed) })
        presentationPorts().setSpriteState('idle')
      }
    } finally {
      finishPreparation?.()
      setSending(false)
    }
  }, [
    controller,
    view.eligible,
    view.scoped,
    view.runtime,
    externalPathsRef,
    isReadOnlySession,
    onClearExternalPaths,
    onPreCheckFail,
    setSending,
    setText,
    $chatEditDraft,
    $chatMessageList,
    $chatSessionId,
    $chatTurnInFlight,
    $pendingPromptBatch
  ])

  const handleStop = useCallback(async () => {
    if (!controller.isCurrent() || !view.eligible) {
      return
    }

    conversationVoiceSink().cancel($chatSessionId.get())
    await controller.stopActiveTurn()
  }, [controller, view.eligible, $chatSessionId])

  return {
    cancelEdit: () => {
      if (!controller.$sending.get()) {
        $chatEditDraft.set(null)
      }
    },
    editing,
    handleStop,
    pending,
    send,
    sending,
    stopping:
      stoppingRequestId !== null &&
      (!controller.activeRequestId() || stoppingRequestId === controller.activeRequestId()),
    setPending,
    setText: next => {
      const edit = $chatEditDraft.get()

      if (edit) {
        $chatEditDraft.set({ ...edit, text: typeof next === 'function' ? next(edit.text) : next })
      } else {
        setText(next)
      }
    },
    text: editing?.text ?? text
  }
}
