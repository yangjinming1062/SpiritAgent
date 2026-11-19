import { type RefObject, useEffect } from 'react'

export function useDockMagnification(ref: RefObject<HTMLElement | null>, enabled: boolean): void {
  useEffect(() => {
    const bar = ref.current

    if (!bar || !enabled) {
      return
    }

    const media = window.matchMedia(
      '(any-hover: hover) and (any-pointer: fine) and (prefers-reduced-motion: no-preference)'
    )

    const viewportSelector = '[data-dock-viewport]'
    const liftProperty = '--desktop-magnification'
    const scaleProperty = '--desktop-magnification-scale'
    type Magnification = { lift: number; scale: number }
    let active = new Map<HTMLButtonElement, Magnification>()
    let frame = 0
    let pointer: { x: number; y: number; group: HTMLElement } | undefined

    const apply = (next: Map<HTMLButtonElement, Magnification>): void => {
      for (const button of active.keys()) {
        if (!next.has(button)) {
          button.style.removeProperty(liftProperty)
          button.style.removeProperty(scaleProperty)
        }
      }

      for (const [button, { lift, scale }] of next) {
        const previous = active.get(button)

        if (previous?.lift !== lift) {
          button.style.setProperty(liftProperty, String(lift))
        }

        if (previous?.scale !== scale) {
          button.style.setProperty(scaleProperty, String(scale))
        }
      }

      active = next
    }

    const reset = (): void => {
      cancelAnimationFrame(frame)
      frame = 0
      pointer = undefined
      apply(new Map())
    }

    const update = (): void => {
      frame = 0

      if (!pointer || !bar.contains(pointer.group)) {
        reset()

        return
      }

      const { x, y, group } = pointer
      const gap = Number.parseFloat(getComputedStyle(bar).getPropertyValue('--desktop-item-gap'))

      const viewport =
        group.closest<HTMLElement>(viewportSelector) ?? group.querySelector<HTMLElement>(viewportSelector)

      const clipLeft = viewport ? viewport.getBoundingClientRect().left + viewport.clientLeft : 0
      const clipRight = clipLeft + (viewport?.clientWidth ?? 0)
      const next = new Map<HTMLButtonElement, Magnification>()

      // 用固定按钮的中心计算距离，图标的变换不会改变后续帧的输入。
      for (const button of group.querySelectorAll<HTMLButtonElement>('button[data-dock-motion]:enabled')) {
        const rect = button.getBoundingClientRect()
        const center = rect.left + rect.width / 2
        const radius = 1.5 * (rect.width + gap)
        const distance = Math.abs(x - center)

        if (distance >= radius || y < rect.top || y >= rect.bottom) {
          continue
        }

        const lift = 1 - distance / radius
        let scale = 1 + lift * 0.25

        if (viewport?.contains(button)) {
          if (rect.right <= clipLeft || rect.left >= clipRight) {
            continue
          }

          const glyphWidth = button.querySelector<HTMLElement>('[data-dock-glyph]')?.offsetWidth

          if (glyphWidth) {
            // 边缘图标仍能抬升，放大只使用滚动视口内剩余的空间。
            const room = Math.min(center - clipLeft, clipRight - center)
            scale = Math.max(1, Math.min(scale, (2 * room) / glyphWidth))
          }
        }

        next.set(button, { lift, scale })
      }

      apply(next)
    }

    const move = (event: PointerEvent): void => {
      const group = event.target instanceof Element && event.target.closest<HTMLElement>('[data-dock-motion-group]')

      if (!media.matches || event.pointerType !== 'mouse' || event.buttons !== 0 || !group) {
        reset()

        return
      }

      pointer = { x: event.clientX, y: event.clientY, group }
      frame ||= requestAnimationFrame(update)
    }

    const clearEvents = ['pointerleave', 'pointerdown', 'pointercancel', 'dragstart', 'dragenter'] as const
    bar.addEventListener('pointermove', move)

    for (const event of clearEvents) {
      bar.addEventListener(event, reset)
    }

    window.addEventListener('blur', reset)
    window.addEventListener('resize', reset)
    bar.addEventListener('scroll', reset, true)
    media.addEventListener('change', reset)

    return () => {
      bar.removeEventListener('pointermove', move)

      for (const event of clearEvents) {
        bar.removeEventListener(event, reset)
      }

      window.removeEventListener('blur', reset)
      window.removeEventListener('resize', reset)
      bar.removeEventListener('scroll', reset, true)
      media.removeEventListener('change', reset)
      reset()
    }
  }, [enabled, ref])
}
