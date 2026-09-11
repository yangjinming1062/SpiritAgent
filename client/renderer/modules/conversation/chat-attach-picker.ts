import { getStrings } from '@/shared/strings'

import { basename } from './chat-path'
import type { PendingAttachment } from './chat-store'
import { ensureChatSession } from './session-list-store'

// 附件扩展名分拣：视频容器与后端白名单一致（mp4/mov，供应商实测 webb 被拒）；
// 图片同步支持 HEIC/HEIF（iPhone 截图）/TIFF/AVIF/JXL（next-gen）。
export const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|heic|heif|tiff?|avif|jxl)$/i
export const VIDEO_EXT = /\.(mp4|mov)$/i

type SetPending = React.Dispatch<React.SetStateAction<PendingAttachment | null>>

function getVideoUploadOptions() {
  const picker = getStrings().chat.picker

  return {
    filters: [{ extensions: ['mp4', 'mov'], name: picker.videoFilterName }],
    multiple: false,
    title: picker.selectVideo
  }
}

function getImagePickOptions() {
  const picker = getStrings().chat.picker

  return {
    filters: [
      {
        extensions: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'heic', 'heif', 'tiff', 'tif', 'avif', 'jxl'],
        name: picker.imageFilterName
      }
    ],
    multiple: false,
    title: picker.selectImage
  }
}

export async function pickFile(setPending: SetPending): Promise<void> {
  try {
    const [path] = await window.spiritagent.selectPaths({ multiple: false, title: getStrings().chat.picker.selectFile })

    if (!path) {
      return
    }

    if (VIDEO_EXT.test(path)) {
      await attachVideoFile(path, setPending)
    } else if (IMAGE_EXT.test(path)) {
      setPending({ type: 'image', value: path, fileName: basename(path) })
    } else {
      setPending({ type: 'file', fileName: basename(path), path })
    }
  } catch {
    /* 用户取消或读取失败 */
  }
}

export async function pickFolder(setPending: SetPending): Promise<void> {
  try {
    const [path] = await window.spiritagent.selectPaths({
      directories: true,
      multiple: false,
      title: getStrings().chat.picker.selectFolder
    })

    if (!path) {
      return
    }

    setPending({ type: 'folder', folderName: basename(path), path })
  } catch {
    /* 用户取消或读取失败 */
  }
}

export async function pickImage(setPending: SetPending): Promise<void> {
  try {
    const [path] = await window.spiritagent.selectPaths(getImagePickOptions())

    if (!path) {
      return
    }

    setPending({ type: 'image', value: path, fileName: basename(path) })
  } catch {
    /* 用户取消或读取失败 */
  }
}

export async function pickVideo(setPending: SetPending): Promise<void> {
  try {
    const [path] = await window.spiritagent.selectPaths(getVideoUploadOptions())

    if (!path) {
      return
    }

    await attachVideoFile(path, setPending)
  } catch {
    /* 用户取消或读取失败 */
  }
}

// 视频附加即上传（本地后端 <1s）：本地模式下超 50MB 会被后端 413 拒绝并在 error 里给出指引。
export async function attachVideoFile(path: string, setPending: SetPending): Promise<void> {
  const fileName = basename(path)

  setPending({ type: 'video', fileName, path, status: 'uploading' })

  try {
    const sessionId = await ensureChatSession()
    const result = await window.spiritagent.uploadVideoForAttach({ path, sessionId })

    setPending(prev => {
      if (prev?.type === 'video' && prev.path === path) {
        return { type: 'video', fileName, path, status: 'ready', url: result.url }
      }

      return prev
    })
  } catch (err) {
    setPending(prev => {
      if (prev?.type === 'video' && prev.path === path) {
        return {
          type: 'video',
          fileName,
          path,
          status: 'error',
          error: err instanceof Error ? err.message : String(err)
        }
      }

      return prev
    })
  }
}
