import { useCallback, useRef, useState } from 'react'

import { useGatewayRequest } from '@/shared'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { parseSlashInput } from '@/shared/lib/slash-commands'
import { presentationPorts } from '@/shared/presentation-ports'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { ChatAttachment } from '@/shared/types/spiritagent'

import { basename } from './chat-path'
import { executeSlashCommand, slashPreCheck } from './chat-slash'
import {
  $chatMessageBodies,
  $chatMessageList,
  $chatSessionId,
  $chatTurnInFlight,
  cancelPendingFlush,
  finalizeAssistantMessage,
  markAssistantTerminal,
  type PendingAttachment,
  pushPendingPrompt,
  pushUserMessage,
  schedulePendingFlush,
  submitPendingBatch
} from './chat-store'
import { cancelVoiceBar } from './chat-voice-bar'
import { ensureChatSession } from './session-list-store'

interface UseChatSubmitOptions {
  externalPaths: string[]
  gatewayState: ConnectionState
  isReadOnlySession: boolean
  onClearExternalPaths: () => void
  onPreCheckFail: (message: string) => void
}

export interface ChatSubmit {
  handleStop: () => Promise<void>
  pending: PendingAttachment | null
  sending: boolean
  send: () => Promise<void>
  setPending: React.Dispatch<React.SetStateAction<PendingAttachment | null>>
  setSending: React.Dispatch<React.SetStateAction<boolean>>
  setText: React.Dispatch<React.SetStateAction<string>>
  text: string
}

export function useChatSubmit({
  externalPaths,
  gatewayState,
  isReadOnlySession,
  onClearExternalPaths,
  onPreCheckFail
}: UseChatSubmitOptions): ChatSubmit {
  const { requestGateway } = useGatewayRequest()
  const [text, setText] = useState('')
  const [pending, setPending] = useState<PendingAttachment | null>(null)
  const [sending, setSending] = useState(false)

  // 通过 ref 转发最新值给 send（避免 useCallback 依赖列表频繁变更）。
  const textRef = useRef(text)
  const pendingRef = useRef(pending)
  const sendingRef = useRef(sending)
  // externalPaths 已是值类型，但仍走 ref：send 是异步的，期间用户继续拖入文件
  // 会改变 externalPaths 的引用。回调创建时闭包里的快照已过期，必须读 ref 才能
  // 拿到发送瞬间的最新列表。
  const externalPathsRef = useRef(externalPaths)
  textRef.current = text
  pendingRef.current = pending
  sendingRef.current = sending
  externalPathsRef.current = externalPaths

  const send = useCallback(async () => {
    const currentText = textRef.current
    const currentPending = pendingRef.current
    const currentSending = sendingRef.current

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

    if (trimmed === '/') {
      return
    }

    if (gatewayState !== 'open') {
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
          requestGateway
        })

        return
      }

      onPreCheckFail(getStrings().chat.submit.unknownCommand(parsed.name))

      return
    }

    setSending(true)
    sendingRef.current = true
    cancelVoiceBar()

    try {
      const submit = getStrings().chat.submit
      const id = await ensureChatSession()
      let fullText = trimmed
      let promptText = trimmed
      const attachments: ChatAttachment[] = []
      const displayAttachments: ChatAttachment[] = []

      if (currentPending?.type === 'image') {
        // 本地图片优先以 data URL 附件直发多模态（后端转 input_image parts，视觉链路接手）；
        // 读取失败（不可读/超体量）才降级路径模式：@file: 指令进正文，LLM 走文件工具读取。
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
        fullText = fullText ? `${fullText}\n${fileRef}` : fileRef
        const fileDirective = `@file:${currentPending.path}`
        promptText = promptText ? `${promptText}\n${fileDirective}` : fileDirective
      } else if (currentPending?.type === 'folder') {
        const folderRef = submit.folderInlinePrefix(currentPending.folderName, currentPending.path)
        fullText = fullText ? `${fullText}\n${folderRef}` : folderRef
        const folderDirective = `@folder:${currentPending.path}`
        promptText = promptText ? `${promptText}\n${folderDirective}` : folderDirective
      }

      const extra = externalPathsRef.current

      if (extra.length > 0) {
        const names = extra.map(basename).join(submit.attachmentsJoiner)
        const heading = submit.attachmentsHeading(names)
        fullText = fullText ? `${fullText}\n${heading}` : heading
        const extraDirectives = extra.map(p => `@file:${p}`).join('\n')
        promptText = promptText ? `${promptText}\n${extraDirectives}` : extraDirectives
      }

      externalPathsRef.current = []
      onClearExternalPaths()

      // 展示层：媒体卡即内容，纯附件消息不留占位文案；仅附件与正文全空时兜底。
      const displayPlaceholder = displayAttachments.length
        ? ''
        : currentPending?.type === 'video'
          ? submit.displayVideo
          : currentPending?.type === 'image'
            ? submit.displayImage
            : currentPending?.type === 'file'
              ? submit.displayFile(currentPending.fileName)
              : currentPending?.type === 'folder'
                ? submit.displayFolder(currentPending.folderName)
                : ''

      const promptFallback =
        currentPending?.type === 'video'
          ? submit.promptVideo
          : currentPending?.type === 'image'
            ? submit.promptImage
            : currentPending?.type === 'file'
              ? submit.promptFile(currentPending.path)
              : currentPending?.type === 'folder'
                ? submit.promptFolder(currentPending.path)
                : ''

      pushUserMessage(fullText || displayPlaceholder, displayAttachments.length ? displayAttachments : undefined)
      setText('')
      setPending(null)

      if (!$chatTurnInFlight.get()) {
        presentationPorts().setSpriteState('listening')
      }

      pushPendingPrompt({
        attachments: attachments.length ? attachments : undefined,
        text: promptText || promptFallback
      })
      schedulePendingFlush()
    } catch (err) {
      markAssistantTerminal({ error: err instanceof Error ? err.message : getStrings().chat.sendFailed })
      presentationPorts().setSpriteState('idle')
      setPending(null)
    } finally {
      sendingRef.current = false
      setSending(false)
    }
  }, [externalPathsRef, gatewayState, isReadOnlySession, onClearExternalPaths, onPreCheckFail, requestGateway])

  const handleStop = useCallback(async () => {
    cancelVoiceBar()
    cancelPendingFlush()
    $chatTurnInFlight.set(false)
    const sid = $chatSessionId.get()

    if (sid) {
      try {
        await requestGateway('session.interrupt', { session_id: sid })
      } catch {
        /* 尽力而为 */
      }
    }

    void window.spiritagent?.runnerCancel?.().catch(err => {
      log.warn('use-chat-submit', 'runnerCancel failed', err)
    })

    const lastItem = $chatMessageList.get().at(-1)
    const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined

    if (lastItem?.role === 'assistant' && lastBody?.streaming && lastBody.text.trim()) {
      finalizeAssistantMessage(undefined, undefined, undefined, { synthesize: false })
    } else {
      markAssistantTerminal({ cancelled: true })
    }

    presentationPorts().setSpriteState('idle', { force: true })
    submitPendingBatch()
  }, [requestGateway])

  return {
    handleStop,
    pending,
    send,
    sending,
    setPending,
    setSending,
    setText,
    text
  }
}
