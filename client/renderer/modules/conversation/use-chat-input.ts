import { useStore } from '@nanostores/react'
import { type ClipboardEvent, type DragEvent, type PointerEvent, useCallback, useEffect, useRef, useState } from 'react'

import { useAtomListen } from '@/shared/hooks/use-atom-listen'
import { blobToDataUrl } from '@/shared/lib/blob-data-url'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { currentClearEpoch } from '@/shared/lib/storage'
import { notify, notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

import { attachVideoFile } from './chat-attach-picker'
import { $chatDraftFromUndo, $pendingExternalAttachment } from './chat-store'
import type { ChatSubmitState, ConversationInputProps } from './conversation-input'
import { beginConversationInputActivity } from './conversation-input-busy'
import { useConversationView } from './conversation-view'
import { useChatSubmit } from './use-chat-submit'
import { useVoiceRecorder } from './use-voice-recorder'

// 主对话与轻语共享输入逻辑，草稿和附件归所属视图。
export interface UseChatInputOptions {
  gatewayState: ConnectionState
  isReadOnlySession: boolean
}

export interface UseChatInputResult {
  handleDrop: (e: DragEvent) => void
  /** 已接好线的 ConversationInput props（不含 variant，由调用方补）。 */
  inputProps: Omit<ConversationInputProps, 'variant'>
}

// 跨挂载去重同一批外部投喂，附件归接收视图。
let consumedExternalFeedNonce = 0

export function useChatInput({ gatewayState, isReadOnlySession }: UseChatInputOptions): UseChatInputResult {
  const view = useConversationView()
  const { controller } = view
  const ownerRef = useRef(controller)
  ownerRef.current = controller
  const isCurrent = useCallback(() => ownerRef.current === controller && controller.isCurrent(), [controller])
  const { $chatSessionId, $chatTurnInFlight, $lastAssistantStreaming, $pendingPromptBatch, $externalPaths } = controller
  const externalPaths = useStore($externalPaths)

  const stageExternalPaths = useCallback(
    (paths: string[]): void => {
      if (view.eligible && !isReadOnlySession && isCurrent()) {
        $externalPaths.set([...$externalPaths.get(), ...paths])
      }
    },
    [view.eligible, isReadOnlySession, isCurrent, $externalPaths]
  )

  const [attachMenuOpen, setAttachMenuOpen] = useState(false)
  const chatSessionId = useStore($chatSessionId)
  const turnInFlight = useStore($chatTurnInFlight)
  const lastStreaming = useStore($lastAssistantStreaming)
  const pendingBatchLen = useStore($pendingPromptBatch).length

  const submit = useChatSubmit({
    externalPaths,
    isReadOnlySession,
    onClearExternalPaths: () => {
      $externalPaths.set([])
    },
    onPreCheckFail: msg => notifyError(msg, getStrings().common.operationFailed)
  })

  const {
    recording,
    start: startRecording,
    stop: stopRecording
  } = useVoiceRecorder({ isReadOnlySession, runtime: controller, eligible: view.eligible })

  // 撤销草稿回填，多窗口按会话过滤
  useAtomListen($chatDraftFromUndo, draft => {
    if (!view.eligible || !draft || draft.session_id !== chatSessionId) {
      return
    }

    submit.setText(draft.text)
    const [image] = draft.attachments ?? []

    // 待发送附件是单槽，多张图片只回填第一张。
    if (image) {
      submit.setPending({ type: 'image', value: image.url })
    }

    $chatDraftFromUndo.set(null)
  })

  // 精灵窗文件投喂（sprite-stage drag/drop）合并到附件；来源窗口不知道目标会话，由本窗口随后确定。投喂可能先于输入挂载（精灵窗先推附件再打开轻语），订阅时先消费当前值，nonce 防止重复并入。
  useEffect(
    () =>
      $pendingExternalAttachment.subscribe(state => {
        if (
          !view.eligible ||
          isReadOnlySession ||
          !view.receiveExternalAttachments ||
          !view.visible ||
          !state ||
          state.paths.length === 0 ||
          state.nonce <= consumedExternalFeedNonce
        ) {
          return
        }

        consumedExternalFeedNonce = state.nonce
        stageExternalPaths(state.paths)
        notify({ kind: 'info', message: getStrings().chat.filesReceived(state.paths.length) })
      }),
    [controller, isReadOnlySession, view.receiveExternalAttachments, view.visible, view.eligible, stageExternalPaths]
  )

  const handleDrop = (e: DragEvent): void => {
    if (!view.eligible || submit.editing) {
      e.preventDefault()

      return
    }

    const paths = resolveDroppedFiles(e.dataTransfer?.files)

    if (paths.length === 0) {
      return
    }

    e.preventDefault()
    stageExternalPaths(paths)
    notify({ kind: 'info', message: getStrings().chat.attachmentsAdded(paths.length) })
  }

  const handlePaste = async (e: ClipboardEvent): Promise<void> => {
    const files = Array.from(e.clipboardData?.files ?? [])

    if (files.length === 0) {
      return
    }

    e.preventDefault()

    if (!view.eligible || submit.editing) {
      return
    }

    // 读取与上传期间切走会话时，其余文件不再加入新会话。
    const sessionId = $chatSessionId.get()
    const epoch = currentClearEpoch()

    for (const file of files) {
      if (!isCurrent() || $chatSessionId.get() !== sessionId) {
        return
      }

      if (file.type.startsWith('image/')) {
        const finishPreparation = beginConversationInputActivity('uploading')

        try {
          const dataUrl = await blobToDataUrl(file).catch(() => null)

          if (dataUrl && currentClearEpoch() === epoch) {
            submit.setPending({ type: 'image', value: dataUrl, fileName: file.name })
          }
        } finally {
          finishPreparation()
        }

        continue
      }

      const [filePath] = resolveDroppedFiles([file])

      if (filePath) {
        if (file.type.startsWith('video/')) {
          await attachVideoFile(filePath, submit.setPending, controller)
        } else {
          stageExternalPaths([filePath])
        }
      }
    }
  }

  const submitState: ChatSubmitState = {
    editAttachments: submit.editing?.attachments,
    editMessageId: submit.editing?.sourceMessageId,
    gatewayState,
    isGenerating: submit.sending || submit.stopping || pendingBatchLen > 0 || turnInFlight || lastStreaming,
    isReadOnlySession,
    pending: submit.editing ? null : submit.pending,
    recording,
    sending: submit.sending,
    stopping: submit.stopping,
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
