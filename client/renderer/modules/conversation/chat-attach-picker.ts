import { errorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { SpiritAgentSelectPathsOptions } from '@ipc/contracts'

import { basename } from './chat-path'
import type { ConversationRuntime } from './chat-runtime'
import type { PendingAttachment } from './chat-store'
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
  apply: (path: string) => Promise<void> | void
): Promise<void> {
  try {
    const [path] = await window.spiritagent.selectPaths(options)

    if (!path) {
      return
    }

    await apply(path)
  } catch (err) {
    reportPickerError(err)
  }
}

export function pickFile(setPending: SetPending, runtime?: ConversationRuntime): Promise<void> {
  return pickPath({ multiple: false, title: getStrings().chat.picker.selectFile }, async path => {
    if (VIDEO_EXT.test(path)) {
      await attachVideoFile(path, setPending, runtime)
    } else if (IMAGE_EXT.test(path)) {
      setPending({ type: 'image', value: path, fileName: basename(path) })
    } else {
      setPending({ type: 'file', fileName: basename(path), path })
    }
  })
}

export function pickFolder(setPending: SetPending, _runtime?: ConversationRuntime): Promise<void> {
  return pickPath({ directories: true, multiple: false, title: getStrings().chat.picker.selectFolder }, path =>
    setPending({ type: 'folder', folderName: basename(path), path })
  )
}

export function pickImage(setPending: SetPending, _runtime?: ConversationRuntime): Promise<void> {
  return pickPath(getImagePickOptions(), path => setPending({ type: 'image', value: path, fileName: basename(path) }))
}

export function pickVideo(setPending: SetPending, runtime?: ConversationRuntime): Promise<void> {
  return pickPath(getVideoUploadOptions(), path => attachVideoFile(path, setPending, runtime))
}

// 视频附加即上传（本地后端 <1s）：本地模式下超 50MB 会被后端 413 拒绝并在 error 里给出指引。结果只回填本次加入的附件对象：切换会话、移除或重新选择后，迟到结果作废。
export async function attachVideoFile(
  path: string,
  setPending: SetPending,
  runtime?: ConversationRuntime
): Promise<void> {
  const fileName = basename(path)
  const uploading: PendingAttachment = { type: 'video', fileName, path, status: 'uploading' }

  setPending(uploading)

  try {
    const sessionId = await ensureChatSession(runtime)
    const result = await window.spiritagent.uploadVideoForAttach({ path, sessionId })

    setPending(prev =>
      prev === uploading ? { type: 'video', fileName, path, status: 'ready', url: result.url } : prev
    )
  } catch (err) {
    setPending(prev =>
      prev === uploading ? { type: 'video', fileName, path, status: 'error', error: errorMessage(err) } : prev
    )
  }
}
