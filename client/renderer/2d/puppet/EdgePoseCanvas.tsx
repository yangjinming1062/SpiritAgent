import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'

import { log } from '@/shared/lib/log'

import { createEdgePoseModel, type EdgePosePack } from './edge-pose'
import { ParametricRenderer } from './parametric'

type Side = 'left' | 'right'

export interface EdgePoseCanvasHandle {
  update(side: Side | 'none', dt: number, now: number, peek: number, reduced: boolean): boolean
  hit(clientX: number, clientY: number): { region: string } | null
}

interface Props {
  pack: EdgePosePack
  boundary?: 'window' | 'container'
  onStatus?: (side: Side, status: 'ready' | 'failed') => void
}

export const EdgePoseCanvas = forwardRef<EdgePoseCanvasHandle, Props>(function EdgePoseCanvas(
  { pack, boundary = 'window', onStatus },
  ref
) {
  const mount = useRef<HTMLDivElement>(null)
  const runtimes = useRef<Partial<Record<Side, { canvas: HTMLCanvasElement; runtime: ParametricRenderer }>>>({})
  const active = useRef<Side | 'none'>('none')

  useEffect(() => {
    let disposed = false
    const loaded = runtimes.current
    const retries: ReturnType<typeof setTimeout>[] = []

    for (const side of ['left', 'right'] as const) {
      const load = async (attempt: number): Promise<void> => {
        const images = new Map<string, ImageBitmap>()

        try {
          // 顺序解码便于取消时释放已获得的位图；主进程负责按纹理内容哈希缓存。
          for (const name of ['body', 'closed'] as const) {
            const asset = pack[side].textures[name]
            const bytes = await window.spiritagent.apiAssetBuffer({ url: asset.url, contentHash: asset.hash })

            if (disposed) {
              return
            }

            const bitmap = await createImageBitmap(new Blob([bytes.slice().buffer]), {
              premultiplyAlpha: 'premultiply'
            })

            images.set(name, bitmap)

            if (disposed) {
              return
            }

            if (bitmap.width !== pack[side].width || bitmap.height !== pack[side].height) {
              throw new Error('Pose texture dimensions mismatch')
            }
          }

          const canvas = document.createElement('canvas')
          // 可见像素命中由交互区域精化；允许盒外姿态上的指针事件向舞台冒泡。
          canvas.style.cssText = 'position:absolute;visibility:hidden;pointer-events:auto;max-width:none'
          const runtime = new ParametricRenderer(canvas, createEdgePoseModel(pack[side], side), images)
          loaded[side] = { canvas, runtime }
          mount.current?.append(canvas)
          onStatus?.(side, 'ready')
        } catch (error) {
          if (!disposed) {
            log.warn('edge-pose', `${side} pose loading failed (attempt ${attempt + 1}/3)`, error)

            if (attempt < 2) {
              retries.push(setTimeout(() => void load(attempt + 1), 1000 * 2 ** attempt))
            } else {
              onStatus?.(side, 'failed')
            }
          }
        } finally {
          for (const image of images.values()) {
            image.close()
          }
        }
      }

      void load(0)
    }

    return (): void => {
      disposed = true
      active.current = 'none'

      for (const timer of retries) {
        clearTimeout(timer)
      }

      for (const item of Object.values(loaded)) {
        item.runtime.dispose()
        item.canvas.remove()
      }

      runtimes.current = {}
    }
  }, [pack, onStatus])

  useImperativeHandle(
    ref,
    () => ({
      update(side, dt, now, peek, reduced): boolean {
        const parent = mount.current
        const item = side === 'none' ? undefined : runtimes.current[side]
        active.current = item && parent ? side : 'none'

        for (const [key, value] of Object.entries(runtimes.current)) {
          value.canvas.style.visibility = key === active.current ? 'visible' : 'hidden'
        }

        if (!item || !parent || side === 'none') {
          return false
        }

        const pose = pack[side]
        const box = parent.getBoundingClientRect()

        if (box.width <= 0 || box.height <= 0) {
          return false
        }

        const scale = box.height / parent.clientHeight
        const fit = parent.clientHeight / pose.height
        item.canvas.style.width = `${pose.width * fit}px`
        item.canvas.style.height = `${pose.height * fit}px`

        const edgeX =
          boundary === 'container' ? (side === 'left' ? box.left : box.right) : side === 'left' ? 0 : window.innerWidth

        item.canvas.style.left = `${(edgeX - box.left) / scale - pose.contactX * fit}px`
        item.canvas.style.top = '0px'
        item.runtime.setParameter('peek', reduced ? 0 : peek)
        const phase = now % 3400
        item.runtime.setParameter('blink', reduced ? 0 : phase < 180 ? Math.sin((phase / 180) * Math.PI) : 0)
        item.runtime.frame(dt)

        return true
      },
      hit(clientX, clientY): { region: string } | null {
        const side = active.current
        const item = side === 'none' ? undefined : runtimes.current[side]

        if (!item || side === 'none') {
          return null
        }

        const rect = item.canvas.getBoundingClientRect()
        const x = ((clientX - rect.left) / rect.width) * pack[side].width
        const y = ((clientY - rect.top) / rect.height) * pack[side].height

        return item.runtime.hitTest(x, y) ? { region: y < pack[side].head[3] + 30 ? 'face' : 'body' } : null
      }
    }),
    [pack, boundary]
  )

  return <div ref={mount} style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} />
})
