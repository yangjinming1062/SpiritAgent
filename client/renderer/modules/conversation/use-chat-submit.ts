import { useStore } from '@nanostores/react'
import { type Dispatch, type SetStateAction, useCallback, useEffect, useRef } from 'react'

import { requestGateway } from '@/shared'
import { captureAuthScope } from '@/shared/lib/authed-api'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { errorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { parseSlashInput } from '@/shared/lib/slash-commands'
import { currentClearEpoch } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { $gateway } from '@/shared/store/gateway'
import { notify, notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { ChatAttachment } from '@protocol'
import type { PromptSubmissionResult } from '@protocol'

import { basename } from './chat-path'
import type { ConversationRuntime } from './chat-runtime'
import { executeSlashCommand, slashPreCheck } from './chat-slash'
import {
  $chatSessionId as $selectedChatSessionId,
  type ChatEditDraft,
  getConversationRuntime,
  type PendingAttachment
} from './chat-store'
import { useConversationView } from './conversation-view'
import { ensureChatSession } from './session-list-store'
import { conversationVoiceSink } from './voice-link'

interface UseChatSubmitOptions {
  externalPaths: string[]
  gatewayState: ConnectionState
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
  send: () => Promise<void>
  setPending: Dispatch<SetStateAction<PendingAttachment | null>>
  setText: Dispatch<SetStateAction<string>>
  text: string
}

const appendLine = (text: string, line: string): string => (text ? `${text}\n${line}` : line)

export function useChatSubmit({
  externalPaths,
  gatewayState,
  isReadOnlySession,
  onClearExternalPaths,
  onPreCheckFail
}: UseChatSubmitOptions): ChatSubmit {
  const view = useConversationView()
  const controller = view.controller

  const {
    $chatEditDraft,
    $chatMessageBodies,
    $chatMessageList,
    $chatSessionId,
    $chatTurnInFlight,
    $lastEditableUserMessage,
    cancelPendingFlush,
    finalizeAssistantMessage,
    markAssistantTerminal,
    submitPendingBatch
  } = controller

  const text = useStore(controller.$text)
  const pending = useStore(controller.$pending)
  const sending = useStore(controller.$sending)

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
  const lifecycleRef = useRef({ mounted: true })
  useEffect(() => {
    const lifecycle = lifecycleRef.current
    lifecycle.mounted = true

    return () => {
      lifecycle.mounted = false
    }
  }, [controller])
  const editing = useStore($chatEditDraft)

  const setPending: Dispatch<SetStateAction<PendingAttachment | null>> = useCallback(
    next => {
      if (
        !lifecycleRef.current.mounted ||
        currentClearEpoch() !== controllerEpoch ||
        ownerRef.current.$text !== controller.$text
      ) {
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
    const gateway = $gateway.get()

    if (!view.eligible || !controller.isCurrent() || !authScope || !gateway) {
      return
    }

    const requestCurrent = (): boolean => authScope() && $gateway.get() === gateway

    const requestGateway = async <T = unknown>(method: string, params?: Record<string, unknown>): Promise<T> => {
      if (!requestCurrent()) {
        throw new Error('Gateway request owner changed')
      }

      return gateway.request<T>(method, params)
    }

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

    if (!trimmed && !currentPending) {
      return
    }

    if (!edit && trimmed === '/') {
      return
    }

    if (gatewayState !== 'open') {
      return
    }

    if (edit) {
      if (
        edit.sessionId !== $chatSessionId.get() ||
        edit.sourceMessageId !== $lastEditableUserMessage.get()?.backendMessageId
      ) {
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
        controller.acceptPromptSubmission(await requestGateway<PromptSubmissionResult>('prompt.submit', submission))

        if ($chatEditDraft.get() === edit) {
          $chatEditDraft.set(null)
        }
      } catch (err) {
        // 已收到修订事件时，服务端已接受；迟到的 RPC 失败不能中止新回合。
        if (
          requestCurrent() &&
          controller.isCurrent() &&
          $chatSessionId.get() === edit.sessionId &&
          $chatEditDraft.get() === edit &&
          controller.isPromptUnconfirmed(submission.request_id)
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
          requestGateway,
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

    try {
      const submit = getStrings().chat.submit
      id = await ensureChatSession(controller)
      target = initialSessionId === null ? getConversationRuntime(id) : view.runtime
      let fullText = trimmed
      let promptText = trimmed
      const attachments: ChatAttachment[] = []
      const displayAttachments: ChatAttachment[] = []

      if (currentPending?.type === 'image') {
        // 本地图片优先以 data URL 附件直发多模态（后端转 input_image parts，视觉链路接手）；读取失败（不可读/超体量）才降级路径模式：@file: 指令进正文，LLM 走文件工具读取。
        let dataUrl: string | null = currentPending.value.startsWith('data:') ? currentPending.value : null

        if (!dataUrl) {
          try {
            dataUrl = await window.spiritagent.readImageForAttach(currentPending.value)
          } catch (err) {
            log.warn('use-chat-submit', 'readImageForAttach failed', err)
            /* 降级路径模式 */
          }
        }

        if (dataUrl) {
          attachments.push({ type: 'image', url: dataUrl })
          displayAttachments.push({ type: 'image', url: dataUrl })
        } else {
          const ref = await requestGateway<{ ref_text?: string }>('image.attach', {
            path: currentPending.value,
            session_id: id
          })

          if (ref.ref_text) {
            fullText = `${fullText}\n${ref.ref_text}`.trim()
            promptText = `${promptText}\n${ref.ref_text}`.trim()
            displayAttachments.push({ type: 'image', url: currentPending.value })
          }
        }
      } else if (currentPending?.type === 'video' && currentPending.url) {
        attachments.push({ type: 'video', url: currentPending.url })
        displayAttachments.push({ type: 'video', url: currentPending.url })
      } else if (currentPending?.type === 'file') {
        const fileRef = submit.fileInlinePrefix(currentPending.fileName, currentPending.path)
        fullText = appendLine(fullText, fileRef)
        const fileDirective = `@file:${currentPending.path}`
        promptText = appendLine(promptText, fileDirective)
      } else if (currentPending?.type === 'folder') {
        const folderRef = submit.folderInlinePrefix(currentPending.folderName, currentPending.path)
        fullText = appendLine(fullText, folderRef)
        const folderDirective = `@folder:${currentPending.path}`
        promptText = appendLine(promptText, folderDirective)
      }

      // 等待期间切走的会话不再接收这条消息；附件已随切换丢弃，正文留在输入框。
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

      // 展示层：媒体卡即内容，纯附件消息不留占位文案；仅附件与正文全空时兜底。
      const placeholderFor = (attachment: PendingAttachment | null): { display: string; prompt: string } => {
        if (!attachment) {
          return { display: '', prompt: '' }
        }

        switch (attachment.type) {
          case 'video':
            return { display: submit.displayVideo, prompt: submit.promptVideo }

          case 'image':
            return { display: submit.displayImage, prompt: submit.promptImage }

          case 'file':
            return { display: submit.displayFile(attachment.fileName), prompt: submit.promptFile(attachment.path) }

          case 'folder':
            return {
              display: submit.displayFolder(attachment.folderName),
              prompt: submit.promptFolder(attachment.path)
            }
        }
      }

      const placeholder = placeholderFor(currentPending)
      const displayPlaceholder = displayAttachments.length ? '' : placeholder.display
      const promptFallback = placeholder.prompt

      target.pushUserMessage(fullText || displayPlaceholder, displayAttachments.length ? displayAttachments : undefined)
      setText('')
      controller.$pending.set(null)

      if (!target.$chatTurnInFlight.get()) {
        presentationPorts().setSpriteState('listening')
      }

      target.pushPendingPrompt({
        attachments: attachments.length ? attachments : undefined,
        text: promptText || promptFallback
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
        setPending(null)
      }
    } finally {
      setSending(false)
    }
  }, [
    controller,
    view.eligible,
    view.scoped,
    view.runtime,
    externalPathsRef,
    gatewayState,
    isReadOnlySession,
    onClearExternalPaths,
    onPreCheckFail,
    setPending,
    setSending,
    setText,
    $chatEditDraft,
    $chatSessionId,
    $chatTurnInFlight,
    $lastEditableUserMessage
  ])

  const handleStop = useCallback(async () => {
    if (!controller.isCurrent() || !view.eligible) {
      return
    }

    conversationVoiceSink().cancel($chatSessionId.get())
    cancelPendingFlush()
    $chatTurnInFlight.set(false)
    const sid = $chatSessionId.get()

    // 停止对会话是尽力请求，失败仍在本地收尾；本回合在途的本机调用由后端中断回合时逐个下发 tool.cancel 取消。
    if (sid) {
      try {
        await requestGateway('session.interrupt', { session_id: sid })
      } catch (err) {
        log.warn('use-chat-submit', 'session.interrupt failed', err)
      }
    }

    if (!controller.isCurrent()) {
      return
    }

    presentationPorts().setSpriteState('idle', { force: true })

    // 等待中断期间已切到其他会话：收尾与排队提交不作用于新会话。
    if ($chatSessionId.get() !== sid) {
      return
    }

    const lastItem = $chatMessageList.get().at(-1)
    const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined

    if (lastItem?.role === 'assistant' && lastBody?.streaming && lastBody.text.trim()) {
      finalizeAssistantMessage()
    } else {
      markAssistantTerminal({ cancelled: true })
    }

    submitPendingBatch()
  }, [
    controller,
    view.eligible,
    $chatMessageBodies,
    $chatMessageList,
    $chatSessionId,
    $chatTurnInFlight,
    cancelPendingFlush,
    finalizeAssistantMessage,
    markAssistantTerminal,
    submitPendingBatch
  ])

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
