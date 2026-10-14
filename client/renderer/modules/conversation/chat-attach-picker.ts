import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { fileApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { SpiritAgentSelectPathsOptions } from '@ipc/contracts'
import { errorMessage } from '@runtime'

import { basename } from './chat-path'
import type { ConversationRuntime } from './chat-runtime'
import { type PendingAttachment, retainConversationRuntime } from './chat-store'
import { beginConversationInputActivity } from './conversation-input-busy'
import { ensureChatSession } from './session-list-store'

// 附件扩展名分拣：视频容器与后端白名单一致（mp4/mov，供应商实测 webb 被拒）；图片同步支持 HEIC/HEIF（iPhone 截图）/TIFF/AVIF/JXL（next-gen）。
const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'heic', 'heif', 'tiff', 'tif', 'avif', 'jxl']
const VIDEO_EXTENSIONS = ['mp4', 'mov']

const extensionPattern = (extensions: string[]): RegExp => new RegExp(`\\.(${extensions.join('|')})$`, 'i')
const IMAGE_EXT = extensionPattern(IMAGE_EXTENSIONS)
const VIDEO_EXT = extensionPattern(VIDEO_EXTENSIONS)

type SetPending = React.Dispatch<React.SetStateAction<PendingAttachment | null>>

// 取消选择返回空列表，不会走到这里；这里只处理打开选择框本身的失败。
function reportPickerError(err: unknown): void {
  log.warn('chat-attach-picker', 'selectPaths failed', err)
  notifyError(err, getStrings().chat.picker.openFailed)
}

function getVideoUploadOptions() {
  const picker = getStrings().chat.picker

  return {
    filters: [{ extensions: VIDEO_EXTENSIONS, name: picker.videoFilterName }],
    multiple: false,
    title: picker.selectVideo
  }
}

function getImagePickOptions() {
  const picker = getStrings().chat.picker

  return {
    filters: [{ extensions: IMAGE_EXTENSIONS, name: picker.imageFilterName }],
    multiple: false,
    title: picker.selectImage
  }
}

async function pickPath(
  options: SpiritAgentSelectPathsOptions,
  apply: (path: string) => Promise<void> | void,
  runtime?: ConversationRuntime
): Promise<void> {
  const authCurrent = captureAuthScope()

  if (!authCurrent || (runtime && !runtime.isCurrent())) {
    return
  }

  const releaseRuntime = runtime ? retainConversationRuntime(runtime) : undefined

  try {
    const [path] = await fileApi().selectPaths(options)

    if (!path || !authCurrent()) {
      return
    }

    await apply(path)
  } catch (err) {
    if (authCurrent()) {
      reportPickerError(err)
    }
  } finally {
    releaseRuntime?.()
  }
}

export function pickFile(setPending: SetPending, runtime?: ConversationRuntime): Promise<void> {
  return pickPath(
    { multiple: false, title: getStrings().chat.picker.selectFile },
    async path => {
      if (VIDEO_EXT.test(path)) {
        await attachVideoFile(path, setPending, runtime)
      } else if (IMAGE_EXT.test(path)) {
        setPending({ type: 'image', value: path, fileName: basename(path) })
      } else {
        setPending({ type: 'file', fileName: basename(path), path })
      }
    },
    runtime
  )
}

export function pickFolder(setPending: SetPending, runtime?: ConversationRuntime): Promise<void> {
  return pickPath(
    { directories: true, multiple: false, title: getStrings().chat.picker.selectFolder },
    path => setPending({ type: 'folder', folderName: basename(path), path }),
    runtime
  )
}

export function pickImage(setPending: SetPending, runtime?: ConversationRuntime): Promise<void> {
  return pickPath(
    getImagePickOptions(),
    path => setPending({ type: 'image', value: path, fileName: basename(path) }),
    runtime
  )
}

export function pickVideo(setPending: SetPending, runtime?: ConversationRuntime): Promise<void> {
  return pickPath(getVideoUploadOptions(), path => attachVideoFile(path, setPending, runtime), runtime)
}

// 上传结果仅回填来源草稿中本次加入的附件；移除或重新选择后不再覆盖。
export async function attachVideoFile(
  path: string,
  setPending: SetPending,
  runtime?: ConversationRuntime
): Promise<void> {
  const auth = $auth.get()
  const authCurrent = captureAuthScope()

  if (auth.kind !== 'authenticated' || !authCurrent || (runtime && !runtime.isCurrent())) {
    return
  }

  const fileName = basename(path)
  const uploading: PendingAttachment = { type: 'video', fileName, path, status: 'uploading' }

  setPending(uploading)
  const finishUpload = beginConversationInputActivity('uploading')
  const releaseRuntime = runtime ? retainConversationRuntime(runtime) : undefined

  try {
    const sessionId = await ensureChatSession(runtime)

    if (!authCurrent()) {
      return
    }

    const result = await fileApi().uploadVideoForAttach({ path, sessionId, authSessionId: auth.snapshot.sessionId })

    if (!authCurrent()) {
      return
    }

    setPending(prev =>
      prev === uploading ? { type: 'video', fileName, path, status: 'ready', url: result.url } : prev
    )
  } catch (err) {
    if (authCurrent()) {
      setPending(prev =>
        prev === uploading ? { type: 'video', fileName, path, status: 'error', error: errorMessage(err) } : prev
      )
    }
  } finally {
    finishUpload()
    releaseRuntime?.()
  }
}
