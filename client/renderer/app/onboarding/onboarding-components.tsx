import { useStore } from '@nanostores/react'
import { useRef, useState } from 'react'

import { $regenFeedback, MAX_IMAGE_DESCRIPTION } from '@/modules/character'
import { HistoryGallery, type HistoryGalleryItem, PortraitLightbox } from '@/shared'
import { usePointerDrag } from '@/shared/hooks/use-pointer-drag'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { BTN_SUBTLE, CreationFeedback, INPUT_CLASS } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

export const ONBOARDING_PRIMARY_BUTTON =
  'inline-flex h-8 items-center justify-center rounded-lg bg-accent px-4 text-xs font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40'

export function PortraitPanel({
  avatarUrl,
  hint,
  history,
  introHint,
  name,
  onPreviewReady,
  onSelectEntry,
  selectedIdx
}: {
  avatarUrl: string | null
  hint: string | null
  history?: HistoryGalleryItem[]
  introHint?: string | null
  name: string
  onPreviewReady?: (url: string | null) => void
  onSelectEntry?: (idx: number) => void
  selectedIdx?: number
}): React.JSX.Element {
  const [zoomedUrl, setZoomedUrl] = useState<string | null>(null)

  const gallery =
    history && history.length > 1 && onSelectEntry ? (
      <HistoryGallery entries={history} onSelect={onSelectEntry} selectedIdx={selectedIdx ?? history.length - 1} />
    ) : null

  return (
    <div className="flex flex-col items-center gap-2">
      {introHint && <p className="text-center text-[10px] leading-relaxed text-muted">{introHint}</p>}
      <PortraitThumb
        label="头像"
        name={name}
        onReady={onPreviewReady}
        onZoom={avatarUrl ? () => setZoomedUrl(avatarUrl) : undefined}
        url={avatarUrl}
      />
      {gallery}
      {hint && <p className="text-xs text-danger-fg">{hint}</p>}
      {zoomedUrl && <PortraitLightbox name={name} onClose={() => setZoomedUrl(null)} url={zoomedUrl} />}
    </div>
  )
}

function PortraitThumb({
  label,
  name,
  onZoom,
  onReady,
  url
}: {
  label: string
  name: string
  onZoom: (() => void) | undefined
  onReady?: (url: string | null) => void
  url: string | null
}): React.JSX.Element {
  const dict = useStrings()
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)

  return (
    <div className="flex flex-col items-center gap-1">
      {url ? (
        <div className="group relative">
          <button
            aria-label="放大查看"
            className="block cursor-zoom-in overflow-hidden rounded-xl border-0 bg-transparent p-0"
            onClick={onZoom}
            type="button"
          >
            <img
              alt={name}
              className="h-28 w-28 object-cover shadow-lg"
              key={`${url}:${revision}`}
              onError={() => {
                setFailedUrl(url)
                onReady?.(null)
              }}
              onLoad={() => onReady?.(url)}
              src={url}
            />
          </button>
        </div>
      ) : (
        <div className="grid h-28 w-28 place-items-center rounded-xl bg-fill-faint text-center text-[10px] text-faint">
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

// 提到顶层：否则 useInteractiveRegion 的 effect 会在每次渲染时重新注册。
const interactiveRegionRect = (el: HTMLElement): DOMRect | null => {
  const rect = el.getBoundingClientRect()

  return rect.width === 0 || rect.height === 0 ? null : rect
}

const DRAG_THRESHOLD = 6

// 可拖拽对话框容器：位置状态与拖拽监听收敛在此，指针移动只重渲染本容器，表单内容经 children 传入不随拖拽重渲染。
// 注册可见矩形到 interactive-regions，SpriteStage 命中测试只在表单卡片上捕获；卸载时恢复穿透。
export function DraggableDialog({ children }: { children: React.ReactNode }): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)

  const [dialogPos, setDialogPos] = useState<{ x: number; y: number }>(() => {
    const width = 448
    const height = 600

    return {
      x: Math.max(0, Math.round((window.innerWidth - width) / 2)),
      y: Math.max(0, Math.round((window.innerHeight - height) / 2))
    }
  })

  useInteractiveRegion('onboarding', containerRef, interactiveRegionRect)

  // document 级监听让指针离开对话框后仍可拖拽；基准位与实时位移分离，dialogPos 仅松手时提交，避免位移叠到不断重设的 origin 上加速漂移。
  const { delta: dialogDragDelta, onPointerDown: onRawDialogPointerDown } = usePointerDrag({
    threshold: DRAG_THRESHOLD,
    onCommit: ({ dx, dy }) => {
      setDialogPos(prev => ({ x: prev.x + dx, y: prev.y + dy }))
    }
  })

  // 表单控件交由浏览器原生——若起点是按钮/输入框/可编辑元素则不进入拖拽。
  const onDialogPointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    const target = e.target as HTMLElement

    if (target.closest('button, input, textarea, select, summary, a, [contenteditable="true"]')) {
      return
    }

    onRawDialogPointerDown(e)
  }

  return (
    <div
      className="absolute flex max-h-[90vh] w-full max-w-md flex-col items-center gap-4"
      onPointerDown={onDialogPointerDown}
      ref={containerRef}
      style={{
        left: dialogPos.x + dialogDragDelta.dx,
        padding: '0 1.5rem',
        pointerEvents: 'auto',
        position: 'absolute',
        top: dialogPos.y + dialogDragDelta.dy,
        touchAction: 'none'
      }}
    >
      {children}
    </div>
  )
}

// 放到 OnboardingFlow 外面：否则它订阅的 $regenFeedback 会在每次按键时让整个对话框重渲染。
export function RegenFeedbackInput({
  disabled,
  id,
  initial = false
}: {
  disabled?: boolean
  id?: string
  initial?: boolean
}): React.JSX.Element {
  const value = useStore($regenFeedback)

  return (
    <textarea
      className={`${INPUT_CLASS} text-xs`}
      disabled={disabled}
      id={id}
      maxLength={MAX_IMAGE_DESCRIPTION}
      onChange={e => $regenFeedback.set(e.target.value)}
      placeholder={
        initial
          ? '比如：金发绿眼、额间一道疤、机械义眼…（可留空）'
          : '哪里不满意？比如：头发再短一点、眼睛再大一点、表情更温和…（可留空直接重新生成）'
      }
      rows={2}
      value={value}
    />
  )
}

// 同 RegenFeedbackInput：微调按钮的 disabled 依赖反馈非空，订阅放独立小组件避免整框重渲染。
export function EditAvatarButton({
  busy,
  disabledByReference,
  onEdit
}: {
  busy: boolean
  disabledByReference: boolean
  onEdit: () => void
}): React.JSX.Element {
  const feedback = useStore($regenFeedback)
  const disabled = busy || disabledByReference || !feedback.trim()

  return (
    <button
      className="text-body transition hover:text-strong disabled:opacity-40"
      disabled={disabled}
      onClick={onEdit}
      title={
        disabledByReference ? '附参考图时不可微调，请先移除参考图' : '在当前头像上修改，其余保持不变（需先填写反馈）'
      }
      type="button"
    >
      微调
    </button>
  )
}
