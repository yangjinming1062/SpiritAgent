// 把 CSS blur 烘焙进离屏 canvas，保留整张图片的比例；分辨率取容器内等比显示尺寸的 1/2。

import { useEffect, useState } from 'react'

import { getUiEffect, getUiPalette, type SpiritAgentUiTheme } from '@ipc/contracts'

const BAKE_SCALE = 0.5

interface BakedScene {
  dataUrl: string
  url: string
  theme: SpiritAgentUiTheme
  height: number
  width: number
}

// 烘焙源按 URL 单条记忆：拖拽改尺寸的多次重烘焙不重走资源桥。
let lastResolvedSource: { source: string; url: string } | null = null

async function resolveBakeSource(url: string): Promise<string | null> {
  // 主进程资源桥返回可读的 data URL，远端签名图不依赖 CDN 的 canvas CORS 配置。
  if (!/^https?:/.test(url) || !window.spiritagent?.apiAsset) {
    return url
  }

  if (lastResolvedSource?.url === url) {
    return lastResolvedSource.source
  }

  const source = await window.spiritagent.apiAsset({ preferCache: true, url })

  if (!source) {
    return null
  }

  lastResolvedSource = { source, url }

  return source
}

async function bakeScene(
  url: string,
  width: number,
  height: number,
  theme: SpiritAgentUiTheme
): Promise<BakedScene | null> {
  const img = new Image()
  const source = await resolveBakeSource(url)

  if (!source) {
    return null
  }

  img.src = source
  await img.decode()

  const scale = Math.min(width / img.naturalWidth, height / img.naturalHeight) * BAKE_SCALE
  const w = Math.max(1, Math.round(img.naturalWidth * scale))
  const h = Math.max(1, Math.round(img.naturalHeight * scale))
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
  ctx.drawImage(img, 0, 0, w, h)

  return { dataUrl: canvas.toDataURL('image/png'), url, theme, height, width }
}

/** 返回烘焙位图；url/主题变化精确重烘焙，尺寸变化的重烘焙结果到达前沿用旧图，失败（解码失败/无 2D 上下文）清空并返回 null 由调用方回退 CSS blur。 */
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

  // 位图保留源图比例，改尺寸期间继续等比显示旧烘焙，不会裁切图片。
  return baked?.url === url && baked.theme === theme ? baked : null
}
