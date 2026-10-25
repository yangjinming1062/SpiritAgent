import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { $regenFeedback, MAX_IMAGE_DESCRIPTION } from '@/modules/character'
import { HistoryGallery, type HistoryGalleryItem } from '@/shared'
import { ImageLightboxButton } from '@/shared/components/image-lightbox-button'
import { usePointerDrag } from '@/shared/hooks/use-pointer-drag'
import { Check } from '@/shared/lib/icons'
import { probeInteractiveRegions, useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { BTN_SUBTLE, CreationFeedback, INPUT_CLASS, SURFACE_OVERLAY } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import styles from './onboarding.module.css'

export const ONBOARDING_PRIMARY_BUTTON =
  'inline-flex min-h-10 items-center justify-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40'

export function OnboardingProgress({ step }: { step: number }): React.JSX.Element {
  const t = useStrings().onboarding

  return (
    <ol aria-label={t.progressAria} className={styles.progress} data-onboarding-drag-handle="">
      {[t.steps.meet, t.steps.confirm, t.steps.voice].map((label, index) => (
        <li
          aria-current={step === index ? 'step' : undefined}
          className={styles.step}
          data-state={step === index ? 'current' : step > index ? 'complete' : 'upcoming'}
          key={label}
        >
          <span aria-hidden="true" className={styles.stepMarker}>
            {step > index ? <Check className="size-3.5" /> : index + 1}
          </span>
          <span className={styles.stepLabel}>{label}</span>
        </li>
      ))}
    </ol>
  )
}

export function PortraitPanel({
  avatarUrl,
  history,
  name,
  onPreviewReady,
  onSelectEntry,
  selectedIdx
}: {
  avatarUrl: string | null
  history?: HistoryGalleryItem[]
  name: string
  onPreviewReady?: (url: string | null) => void
  onSelectEntry?: (idx: number) => void
  selectedIdx?: number
}): React.JSX.Element {
  const t = useStrings().onboarding

  const gallery =
    history && history.length > 1 && onSelectEntry ? (
      <HistoryGallery entries={history} onSelect={onSelectEntry} selectedIdx={selectedIdx ?? history.length - 1} />
    ) : null

  return (
    <div className="flex flex-col items-center gap-2">
      <PortraitThumb label={t.avatarLabel} name={name} onReady={onPreviewReady} url={avatarUrl} />
      {gallery}
    </div>
  )
}

function PortraitThumb({
  label,
  name,
  onReady,
  url
}: {
  label: string
  name: string
  onReady?: (url: string | null) => void
  url: string | null
}): React.JSX.Element {
  const dict = useStrings()
  const t = dict.onboarding
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)

  return (
    <div className="flex flex-col items-center gap-1">
      {url ? (
        <div className="group relative">
          <ImageLightboxButton
            ariaLabel={t.zoomAria}
            className="block cursor-zoom-in overflow-hidden rounded-xl border-0 bg-transparent p-0"
            name={name}
            url={url}
          >
            <img
              alt={name}
              className="h-40 w-40 object-cover shadow-lg"
              key={`${url}:${revision}`}
              onError={() => {
                setFailedUrl(url)
                onReady?.(null)
              }}
              onLoad={() => onReady?.(url)}
              src={url}
            />
          </ImageLightboxButton>
        </div>
      ) : (
        <div className="grid h-40 w-40 place-items-center rounded-xl bg-fill-faint text-center text-[10px] text-faint">
          —
        </div>
      )}
      <span className="text-[10px] text-muted">{label}</span>
      {url && failedUrl === url && (
        <CreationFeedback
          actions={
            <button
              className={BTN_SUBTLE}
              onClick={() => {
                setFailedUrl(null)
                setRevision(value => value + 1)
              }}
              type="button"
            >
              {dict.creation.retryLoad}
            </button>
          }
          state="error"
          title={dict.creation.imageLoadFailed}
        />
      )}
    </div>
  )
}

// 稳定函数引用，避免每次渲染重新登记命中区域。
const interactiveRegionRect = (el: HTMLElement): DOMRect | null => {
  const rect = el.getBoundingClientRect()

  return rect.width === 0 || rect.height === 0 ? null : rect
}

const DRAG_THRESHOLD = 6

// 拖拽只重渲染容器，children 中的表单保持稳定；命中仅覆盖可见卡片。
export function DraggableDialog({ children }: { children: React.ReactNode }): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const [dialogOffset, setDialogOffset] = useState({ dx: 0, dy: 0 })

  useInteractiveRegion('onboarding', containerRef, interactiveRegionRect)

  const constrainOffset = (offset: { dx: number; dy: number }): { dx: number; dy: number } => {
    const rect = containerRef.current?.getBoundingClientRect()

    if (!rect) {
      return offset
    }

    const maxX = Math.max(0, (window.innerWidth - rect.width) / 2 - 24)
    const maxY = Math.max(0, (window.innerHeight - rect.height) / 2 - 24)

    return {
      dx: Math.max(-maxX, Math.min(maxX, offset.dx)),
      dy: Math.max(-maxY, Math.min(maxY, offset.dy))
    }
  }

  useEffect(() => {
    const constrain = (): void => {
      setDialogOffset(previous => {
        const next = constrainOffset(previous)

        return next.dx === previous.dx && next.dy === previous.dy ? previous : next
      })
      probeInteractiveRegions()
    }

    const observer = new ResizeObserver(constrain)

    if (containerRef.current) {
      observer.observe(containerRef.current)
    }

    window.addEventListener('resize', constrain)

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', constrain)
    }
  }, [])

  // 基准偏移只在松手时提交，内容尺寸改变时仍按真实卡片居中。
  const { delta: dialogDragDelta, onPointerDown: onRawDialogPointerDown } = usePointerDrag({
    threshold: DRAG_THRESHOLD,
    onCommit: ({ dx, dy }) => {
      setDialogOffset(previous => constrainOffset({ dx: previous.dx + dx, dy: previous.dy + dy }))
    }
  })

  useEffect(() => {
    probeInteractiveRegions()
  }, [dialogOffset, dialogDragDelta])

  // 只拖动步骤栏，正文滚动、文本选择和表单控件保留原生操作。
  const onDialogPointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    const target = e.target as HTMLElement

    if (!target.closest('[data-onboarding-drag-handle]')) {
      return
    }

    onRawDialogPointerDown(e)
  }

  return (
    <div
      className={`${styles.dialog} ${SURFACE_OVERLAY}`}
      onPointerDown={onDialogPointerDown}
      ref={containerRef}
      style={{
        transform: `translate(-50%, -50%) translate(${dialogOffset.dx + dialogDragDelta.dx}px, ${dialogOffset.dy + dialogDragDelta.dy}px)`
      }}
    >
      {children}
    </div>
  )
}

// 反馈订阅限于输入框，避免每次按键重渲染整个引导。
export function RegenFeedbackInput({
  disabled,
  id,
  initial = false
}: {
  disabled?: boolean
  id?: string
  initial?: boolean
}): React.JSX.Element {
  const t = useStrings().onboarding
  const value = useStore($regenFeedback)

  return (
    <textarea
      className={`${INPUT_CLASS} text-xs`}
      disabled={disabled}
      id={id}
      maxLength={MAX_IMAGE_DESCRIPTION}
      onChange={e => $regenFeedback.set(e.target.value)}
      placeholder={initial ? t.feedbackExample : t.feedbackExampleRefine}
      rows={2}
      value={value}
    />
  )
}

// 按钮独立订阅反馈，避免改变禁用态时重渲染整个引导。
export function EditAvatarButton({
  busy,
  disabledByReference,
  onEdit
}: {
  busy: boolean
  disabledByReference: boolean
  onEdit: () => void
}): React.JSX.Element {
  const t = useStrings().onboarding
  const feedback = useStore($regenFeedback)
  const disabled = busy || disabledByReference || !feedback.trim()

  return (
    <button
      className="text-body transition hover:text-strong disabled:opacity-40"
      disabled={disabled}
      onClick={onEdit}
      title={disabledByReference ? t.refineDisabledByReference : t.refineHint}
      type="button"
    >
      {t.refineAction}
    </button>
  )
}
