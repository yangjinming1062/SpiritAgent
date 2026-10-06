// 把 CSS blur 烘焙进一次性离屏 canvas，Ken Burns 只变换静态层，避免合成器逐帧重采样被模糊层；分辨率取窗口 CSS 尺寸 1/2。

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

  // 尺寸不参与有效性：拖拽改尺寸期间沿用旧烘焙，避免掉回被模糊层逐帧合成。
  return baked?.url === url && baked.theme === theme ? baked : null
}
