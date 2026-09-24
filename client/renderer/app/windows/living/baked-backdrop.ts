// 预模糊的生活空间背景位图：把 CSS blur 烘焙进一次性的离屏 canvas，
// Ken Burns 动画只变换已烘焙的静态层——否则合成器要对被模糊的层做逐帧重采样。
// 烘焙分辨率取窗口 CSS 尺寸的 1/2（模糊后无需全分辨率），缓存当前 URL、尺寸与主题的结果。

import { getUiEffect, getUiPalette, type SpiritAgentUiTheme } from '@ipc/contracts'
import { useEffect, useState } from 'react'

const BAKE_SCALE = 0.5
// 烘焙画布外扩 12%：补偿 Ken Burns 放大与 blur 边缘收缩，避免动画帧露出未覆盖区。
const BAKE_OVERSCAN = 1.12

interface BakedScene {
  dataUrl: string
  url: string
  theme: SpiritAgentUiTheme
  height: number
  width: number
}

async function bakeScene(
  url: string,
  width: number,
  height: number,
  theme: SpiritAgentUiTheme
): Promise<BakedScene | null> {
  const img = new Image()

  // 主进程资源桥返回可读的 data URL，远端签名图不依赖 CDN 的 canvas CORS 配置。
  const source =
    /^https?:/.test(url) && window.spiritagent?.apiAsset
      ? await window.spiritagent.apiAsset({ preferCache: true, url })
      : url

  if (!source) {
    return null
  }

  try {
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error('backdrop decode failed'))
      img.src = source
    })
  } finally {
    img.onload = null
    img.onerror = null
  }

  const w = Math.max(1, Math.round(width * BAKE_SCALE))
  const h = Math.max(1, Math.round(height * BAKE_SCALE))
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h

  const ctx = canvas.getContext('2d')

  if (!ctx) {
    return null
  }

  const solid = getUiEffect(theme) === 'solid'
  const day = getUiPalette(theme) === 'day'
  ctx.filter = `blur(${(solid ? 12 : 18) * BAKE_SCALE}px) saturate(${solid ? 1.06 : day ? 1.08 : 1.22}) brightness(${!solid && day ? 0.97 : 1})`
  // cover 布局外扩并对齐 CSS 的 70% center，补偿 blur 边缘。
  const scale = Math.max((w * BAKE_OVERSCAN) / img.naturalWidth, (h * BAKE_OVERSCAN) / img.naturalHeight)
  const dw = img.naturalWidth * scale
  const dh = img.naturalHeight * scale

  ctx.drawImage(img, (w - dw) * 0.7, (h - dh) / 2, dw, dh)

  return { dataUrl: canvas.toDataURL('image/jpeg', 0.82), url, theme, height, width }
}

/** 返回烘焙位图；url/尺寸变化时重烘焙，失败（解码失败/无 2D 上下文）返回 null 由调用方回退。 */
export function useBakedScene(
  url: string | null,
  width: number,
  height: number,
  theme: SpiritAgentUiTheme
): BakedScene | null {
  const [baked, setBaked] = useState<BakedScene | null>(null)

  useEffect(() => {
    if (!url || width <= 0 || height <= 0) {
      setBaked(null)

      return
    }

    let alive = true

    void bakeScene(url, width, height, theme)
      .then(result => {
        if (alive) {
          setBaked(result)
        }
      })
      .catch(() => {
        if (alive) {
          setBaked(null)
        }
      })

    return () => {
      alive = false
    }
  }, [url, width, height, theme])

  return baked?.url === url && baked.width === width && baked.height === height && baked.theme === theme ? baked : null
}
