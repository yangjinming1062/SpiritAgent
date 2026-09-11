import { useStore } from '@nanostores/react'
import {
  type ClipboardEvent,
  type Dispatch,
  type DragEvent,
  type PointerEvent,
  type SetStateAction,
  useState
} from 'react'

import { attachVideoFile } from '@/chat/chat-attach-picker'
import {
  $chatDraftFromUndo,
  $chatSessionId,
  $chatTurnInFlight,
  $lastAssistantStreaming,
  $pendingExternalAttachment,
  $pendingPromptBatch
} from '@/chat/chat-store'
import type { ChatSubmitState, ConversationInputProps } from '@/chat/conversation-input'
import { useChatSubmit } from '@/chat/use-chat-submit'
import { useVoiceRecorder } from '@/chat/use-voice-recorder'
import { useAtomListen } from '@/shared/hooks/use-atom-listen'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

// 生活空间 / 工作台 / 轻语共享的聊天输入态：附件、提交、录音、拖拽、粘贴与撤销草稿。
// 返回的 submitState 直接喂给 ConversationInput；handler 透传到 ConversationInput 同名 prop。
export interface UseChatInputOptions {
  gatewayState: ConnectionState
  isReadOnlySession: boolean
}

export interface UseChatInputResult {
  attachMenuOpen: boolean
  externalPaths: string[]
  handleDrop: (e: DragEvent) => void
  handlePaste: (e: ClipboardEvent) => Promise<void>
  /** 已接好线的 ConversationInput props（不含 variant，由调用方补）。 */
  inputProps: Omit<ConversationInputProps, 'variant'>
  recording: boolean
  setAttachMenuOpen: Dispatch<SetStateAction<boolean>>
  startRecording: () => void
  stopRecording: () => Promise<void>
  submit: ReturnType<typeof useChatSubmit>
  submitState: ChatSubmitState
}

// 已消费过的投喂 nonce + 跨挂载暂存的附件路径。
// StrictMode 会卸载重挂：组件本地 state 会丢，靠模块级 staged 在重挂时还原；
// nonce 防止 listen 对同一份投喂重复 append。监听器内不 clear，由投喂方在下次 push 前清。
let consumedExternalFeedNonce = 0
let stagedExternalPaths: string[] = []

export function useChatInput({ gatewayState, isReadOnlySession }: UseChatInputOptions): UseChatInputResult {
  const [externalPaths, setExternalPaths] = useState<string[]>(stagedExternalPaths)
  const [attachMenuOpen, setAttachMenuOpen] = useState(false)
  const chatSessionId = useStore($chatSessionId)
  const turnInFlight = useStore($chatTurnInFlight)
  const lastStreaming = useStore($lastAssistantStreaming)
  const pendingBatchLen = useStore($pendingPromptBatch).length

  const submit = useChatSubmit({
    externalPaths,
    gatewayState,
    isReadOnlySession,
    onClearExternalPaths: () => {
      setExternalPaths([])
      stagedExternalPaths = []
    },
    onPreCheckFail: msg => notify({ kind: 'warning', message: msg })
  })

  const { recording, start: startRecording, stop: stopRecording } = useVoiceRecorder({ isReadOnlySession })

  // 撤销草稿回填，多窗口按会话过滤
  useAtomListen($chatDraftFromUndo, draft => {
    if (!draft || draft.session_id !== chatSessionId) {
      return
    }

    submit.setText(draft.text)
    $chatDraftFromUndo.set(null)
  })

  // 精灵窗文件投喂（sprite-stage drag/drop）合并到当前会话附件。
  useAtomListen($pendingExternalAttachment, state => {
    if (!state || state.paths.length === 0 || state.nonce <= consumedExternalFeedNonce) {
      return
    }

    consumedExternalFeedNonce = state.nonce
    setExternalPaths(prev => {
      const next = [...prev, ...state.paths]
      stagedExternalPaths = next

      return next
    })
    notify({ kind: 'info', message: getStrings().chat.filesReceived(state.paths.length) })
  })

  const handleDrop = (e: DragEvent): void => {
    const paths = resolveDroppedFiles(e.dataTransfer?.files)

    if (paths.length === 0) {
      return
    }

    e.preventDefault()
    setExternalPaths(prev => {
      const next = [...prev, ...paths]
      stagedExternalPaths = next

      return next
    })
    notify({ kind: 'info', message: getStrings().chat.attachmentsAdded(paths.length) })
  }

  const handlePaste = async (e: ClipboardEvent): Promise<void> => {
    const files = Array.from(e.clipboardData?.files ?? [])

    if (files.length === 0) {
      return
    }

    e.preventDefault()

    for (const file of files) {
      if (file.type.startsWith('image/')) {
        const dataUrl = await new Promise<string | null>(resolve => {
          const reader = new FileReader()

          reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : null)
          reader.onerror = () => resolve(null)
          reader.readAsDataURL(file)
        })

        if (dataUrl) {
          submit.setPending({ type: 'image', value: dataUrl, fileName: file.name })
        }

        continue
      }

      const webUtils = window.spiritagentWebUtils
      let filePath = (file as File & { path?: string }).path

      if (!filePath && webUtils) {
        try {
          filePath = webUtils.getPathForFile(file)
        } catch {
          filePath = undefined
        }
      }

      if (filePath) {
        if (window.spiritagent.registerUserSelectedPaths) {
          await window.spiritagent.registerUserSelectedPaths([filePath]).catch(() => {})
        }

        if (file.type.startsWith('video/')) {
          await attachVideoFile(filePath, submit.setPending)
        } else {
          setExternalPaths(prev => {
            const next = [...prev, filePath as string]
            stagedExternalPaths = next

            return next
          })
        }
      }
    }
  }

  const submitState: ChatSubmitState = {
    externalPaths,
    gatewayState,
    isGenerating: gatewayState === 'open' && (submit.sending || pendingBatchLen > 0 || turnInFlight || lastStreaming),
    isReadOnlySession,
    pending: submit.pending,
    recording,
    sending: submit.sending,
    text: submit.text
  }

  const onRecordingPointerCancel = (e: PointerEvent<HTMLButtonElement>): void => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }

    void stopRecording()
  }

  const onRecordingPointerDown = (e: PointerEvent<HTMLButtonElement>): void => {
    e.currentTarget.setPointerCapture(e.pointerId)
    void startRecording()
  }

  const onRecordingPointerUp = (e: PointerEvent<HTMLButtonElement>): void => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }

    void stopRecording()
  }

  const inputProps: Omit<ConversationInputProps, 'variant'> = {
    attachMenuOpen,
    externalPaths,
    onAttachMenuToggle: setAttachMenuOpen,
    onDrop: handleDrop,
    onPaste: handlePaste,
    onRecordingPointerCancel,
    onRecordingPointerDown,
    onRecordingPointerUp,
    onSend: () => {
      void submit.send()
    },
    onSetPending: submit.setPending,
    onSetText: submit.setText,
    onStop: () => {
      void submit.handleStop()
    },
    submit: submitState
  }

  return {
    attachMenuOpen,
    externalPaths,
    handleDrop,
    handlePaste,
    inputProps,
    recording,
    setAttachMenuOpen,
    startRecording,
    stopRecording,
    submit,
    submitState
  }
}
