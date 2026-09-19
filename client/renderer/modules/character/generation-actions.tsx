import type React from 'react'

import { cn } from '@/shared/lib/utils'
import { BTN_SUBTLE, FIELD_LABEL, HINT_TEXT } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

interface GenerationActionsGroupProps {
  /** 重新生成：忽略当前版本，按参考与要求整体重绘。 */
  onRegenerate: () => void
  regenerateDisabled?: boolean
  /** 无上一版（首次生成）时可改按钮文案，缺省「重新生成」。 */
  regenerateLabel?: string
  /** 微调：在当前图上只改要求的部分；省略表示该场景不可微调。 */
  onEdit?: () => void
  editDisabled?: boolean
  /** 微调被禁用的具体原因，随 title 展示。 */
  editReason?: string
  /** 自备图入口（外部工具生成后回传）；dense 形态下省略，由宿主自带入口。 */
  onSelfSource?: () => void
  selfSourceDisabled?: boolean
  /** 紧凑输入条形态：不渲染分组标题与自备图分组。 */
  dense?: boolean
}

// 微调与重新生成是两个显式操作（DESIGN §5.4），自备图是不调用 AI 的独立路径。
export function GenerationActionsGroup({
  onRegenerate,
  regenerateDisabled = false,
  regenerateLabel,
  onEdit,
  editDisabled = false,
  editReason,
  onSelfSource,
  selfSourceDisabled = false,
  dense = false
}: GenerationActionsGroupProps): React.JSX.Element {
  const t = useStrings().generationActions
  const selfSource = useStrings().selfSource

  const editButton = onEdit && (
    <button
      className={BTN_SUBTLE}
      disabled={editDisabled}
      onClick={onEdit}
      title={editDisabled && editReason ? editReason : t.editHint}
      type="button"
    >
      {t.edit}
    </button>
  )

  const regenerateButton = (
    <button
      className={BTN_SUBTLE}
      disabled={regenerateDisabled}
      onClick={onRegenerate}
      title={t.regenerateHint}
      type="button"
    >
      {regenerateLabel ?? t.regenerate}
    </button>
  )

  if (dense) {
    return (
      <div className="flex min-w-0 items-center gap-2">
        {editButton}
        {regenerateButton}
        <span className={cn(HINT_TEXT, 'min-w-0 truncate')}>{t.groupHint}</span>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <p className={FIELD_LABEL}>{t.aiTitle}</p>
        <div className="flex flex-wrap items-center gap-2">
          {editButton}
          {regenerateButton}
        </div>
        <p className={HINT_TEXT}>{t.groupHint}</p>
      </div>
      {onSelfSource && (
        <div className="space-y-1">
          <p className={FIELD_LABEL}>{t.selfTitle}</p>
          <button
            className={BTN_SUBTLE}
            disabled={selfSourceDisabled}
            onClick={onSelfSource}
            title={selfSource.openTitle}
            type="button"
          >
            {selfSource.open}
          </button>
          <p className={HINT_TEXT}>{t.selfHint}</p>
        </div>
      )}
    </div>
  )
}
