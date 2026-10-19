import fs from 'node:fs'

import type { BrowserWindow, Clipboard, Dialog, NativeImage } from 'electron'

interface ImageOperationsDeps {
  clipboard: Pick<Clipboard, 'writeImage'>
  dialog: Pick<Dialog, 'showSaveDialog'>
  nativeImage: { createFromBuffer: (buffer: Buffer) => NativeImage }
}

export function createImageOperations({ clipboard, dialog, nativeImage }: ImageOperationsDeps) {
  function copyImage(buffer: Buffer, mimeType: string): void {
    // 保留原字节；格式转换由渲染层 image-clipboard 负责。
    const image = nativeImage.createFromBuffer(buffer)

    if (image.isEmpty()) {
      throw new Error(`Could not read image (${mimeType})`)
    }

    clipboard.writeImage(image)
  }

  async function saveImage(buffer: Buffer, defaultPath: string, parentWindow: BrowserWindow | null): Promise<boolean> {
    const options = { defaultPath, title: 'Save Image' }

    const result = parentWindow
      ? await dialog.showSaveDialog(parentWindow, options)
      : await dialog.showSaveDialog(options)

    if (result.canceled || !result.filePath) {
      return false
    }

    await fs.promises.writeFile(result.filePath, buffer)

    return true
  }

  return { copyImage, saveImage }
}
