import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { type ClipboardEvent, type DragEvent, type PointerEvent, useEffect, useState } from 'react'

import { useAtomListen } from '@/shared/hooks/use-atom-listen'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

import { blobToDataUrl } from './blob-data-url'
import { attachVideoFile } from './chat-attach-picker'
import {
  $chatDraftFromUndo,
  $chatSessionId,
  $chatTurnInFlight,
  $lastAssistantStreaming,
  $pendingExternalAttachment,
  $pendingPromptBatch
} from './chat-store'
import type { ChatSubmitState, ConversationInputProps } from './conversation-input'
import { useChatSubmit } from './use-chat-submit'
import { useVoiceRecorder } from './use-voice-recorder'

// 生活空间 / 工作台 / 轻语共享的聊天输入态：附件、提交、录音、拖拽、粘贴与撤销草稿。返回的 inputProps 直接喂给 ConversationInput；handleDrop 供外层容器接收整层拖入。
export interface UseChatInputOptions {
  gatewayState: ConnectionState
  isReadOnlySession: boolean
}

export interface UseChatInputResult {
  handleDrop: (e: DragEvent) => void
  /** 已接好线的 ConversationInput props（不含 variant，由调用方补）。 */
  inputProps: Omit<ConversationInputProps, 'variant'>
}

// 已消费过的投喂 nonce + 跨挂载暂存的附件路径（模块级 atom，StrictMode 卸载重挂不丢）。nonce 防止 listen 对同一份投喂重复 append。监听器内不 clear，由投喂方在下次 push 前清。
let consumedExternalFeedNonce = 0

// 暂存路径绑定加入时的会话；sessionId 为 null 时归入本窗口随后确定的会话。
interface StagedExternalPaths {
  paths: string[]
  sessionId: string | null
}

const EMPTY_STAGED: StagedExternalPaths = { paths: [], sessionId: null }
const $stagedExternalPaths = atom<StagedExternalPaths>(EMPTY_STAGED)

function stageExternalPaths(paths: string[], sessionId: string | null): void {
  const staged = $stagedExternalPaths.get()
  $stagedExternalPaths.set({ paths: [...staged.paths, ...paths], sessionId: staged.sessionId ?? sessionId })
}

// 切到其他会话或清理账户后丢弃旧附件路径（README 会话与草稿）。
$chatSessionId.listen(sessionId => {
  const staged = $stagedExternalPaths.get()

  if (staged.paths.length === 0 || staged.sessionId === sessionId) {
    return
  }

  $stagedExternalPaths.set(staged.sessionId === null ? { ...staged, sessionId } : EMPTY_STAGED)
})

registerStorageClearHandler(() => {
  $stagedExternalPaths.set(EMPTY_STAGED)
})

export function useChatInput({ gatewayState, isReadOnlySession }: UseChatInputOptions): UseChatInputResult {
  const externalPaths = useStore($stagedExternalPaths).paths
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
      $stagedExternalPaths.set(EMPTY_STAGED)
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

  // 精灵窗文件投喂（sprite-stage drag/drop）合并到附件；来源窗口不知道目标会话，由本窗口随后确定。投喂可能先于输入挂载（精灵窗先推附件再打开轻语），订阅时先消费当前值，nonce 防止重复并入。
  useEffect(
    () =>
      $pendingExternalAttachment.subscribe(state => {
        if (!state || state.paths.length === 0 || state.nonce <= consumedExternalFeedNonce) {
          return
        }

        consumedExternalFeedNonce = state.nonce
        stageExternalPaths(state.paths, null)
        notify({ kind: 'info', message: getStrings().chat.filesReceived(state.paths.length) })
      }),
    []
  )

  const handleDrop = (e: DragEvent): void => {
    if (submit.editing) {
      e.preventDefault()

      return
    }

    const paths = resolveDroppedFiles(e.dataTransfer?.files)

    if (paths.length === 0) {
      return
    }

    e.preventDefault()
    stageExternalPaths(paths, $chatSessionId.get())
    notify({ kind: 'info', message: getStrings().chat.attachmentsAdded(paths.length) })
  }

  const handlePaste = async (e: ClipboardEvent): Promise<void> => {
    const files = Array.from(e.clipboardData?.files ?? [])

    if (files.length === 0) {
      return
    }

    e.preventDefault()

    if (submit.editing) {
      return
    }

    // 读取与上传期间切走会话时，其余文件不再加入新会话。
    const sessionId = $chatSessionId.get()

    for (const file of files) {
      if ($chatSessionId.get() !== sessionId) {
        return
      }

      if (file.type.startsWith('image/')) {
        const dataUrl = await blobToDataUrl(file).catch(() => null)

        if (dataUrl && $chatSessionId.get() === sessionId) {
          submit.setPending({ type: 'image', value: dataUrl, fileName: file.name })
        }

        continue
      }

      const [filePath] = resolveDroppedFiles([file])

      if (filePath) {
        if (file.type.startsWith('video/')) {
          await attachVideoFile(filePath, submit.setPending)
        } else {
          stageExternalPaths([filePath], sessionId)
        }
      }
    }
  }

  const submitState: ChatSubmitState = {
    editMessageId: submit.editing?.sourceMessageId,
    gatewayState,
    isGenerating: gatewayState === 'open' && (submit.sending || pendingBatchLen > 0 || turnInFlight || lastStreaming),
    isReadOnlySession,
    pending: submit.editing ? null : submit.pending,
    recording,
    sending: submit.sending,
    text: submit.text
  }

  const onRecordingPointerDown = (e: PointerEvent<HTMLButtonElement>): void => {
    e.currentTarget.setPointerCapture(e.pointerId)
    void startRecording()
  }

  const onRecordingPointerEnd = (e: PointerEvent<HTMLButtonElement>): void => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }

    void stopRecording()
  }

  const inputProps: Omit<ConversationInputProps, 'variant'> = {
    attachMenuOpen,
    externalPaths: submit.editing ? [] : externalPaths,
    onAttachMenuToggle: setAttachMenuOpen,
    onCancelEdit: submit.cancelEdit,
    onDrop: handleDrop,
    onPaste: handlePaste,
    onRecordingPointerCancel: onRecordingPointerEnd,
    onRecordingPointerDown,
    onRecordingPointerUp: onRecordingPointerEnd,
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

  return { handleDrop, inputProps }
}
