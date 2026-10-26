import type { DesktopMediaBytes } from '@ipc/contracts'

/** 将已有环境与透明造型按完整轮廓适配为静态画面；不产生新的业务素材。 */
export async function prepareStaticPoster(scene: DesktopMediaBytes, outfit: DesktopMediaBytes | null): Promise<string> {
  const urls: string[] = []

  const load = async (media: DesktopMediaBytes): Promise<HTMLImageElement> => {
    const url = URL.createObjectURL(new Blob([new Uint8Array(media.bytes)], { type: media.mime }))
    urls.push(url)
    const image = new Image()
    image.src = url
    await image.decode()

    return image
  }

  try {
    const background = await load(scene)

    if (!outfit) {
      return urls.pop()!
    }

    const figure = await load(outfit)
    const canvas = document.createElement('canvas')
    const scale = Math.min(1, 3840 / background.naturalWidth, 2160 / background.naturalHeight)
    canvas.width = Math.round(background.naturalWidth * scale)
    canvas.height = Math.round(background.naturalHeight * scale)
    const context = canvas.getContext('2d')

    if (!context) {
      throw new Error('Static presentation canvas is unavailable')
    }

    context.drawImage(background, 0, 0, canvas.width, canvas.height)

    const figureScale = Math.min(
      (canvas.height * 0.9) / figure.naturalHeight,
      (canvas.width * 0.5) / figure.naturalWidth
    )

    const width = figure.naturalWidth * figureScale
    const height = figure.naturalHeight * figureScale
    context.drawImage(figure, (canvas.width - width) / 2, canvas.height - height, width, height)

    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(value => (value ? resolve(value) : reject(new Error('Static composition failed'))), 'image/png')
    )

    return URL.createObjectURL(blob)
  } finally {
    urls.forEach(url => URL.revokeObjectURL(url))
  }
}
