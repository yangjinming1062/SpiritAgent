import type React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { cn } from '@/shared/lib/utils'

import { INPUT_CLASS } from './palette'

// 自绘日期选择器：INPUT_CLASS 触发钮 + 内嵌折叠月历。不用原生 date 控件，
// 颜色全部走主题 token，与其它面板控件一致；月历在文档流内展开（非浮层），
// 滚动容器中不会被裁剪。value 为 YYYY-MM-DD，空串表示未选择。

const pad2 = (n: number): string => String(n).padStart(2, '0')

// 视图钳制在四位年份区间：0–99 会被 JS Date 重映射到 1900+，更小的年份也
// 写不出 YYYY-MM-DD。导航越界停在边界月份。
const MIN_VIEW_YEAR = 1000
const MAX_VIEW_YEAR = 9999

const toISODate = (y: number, m: number, d: number): string => `${y}-${pad2(m)}-${pad2(d)}`

// 只接受真实存在的 YYYY-MM-DD；解析失败按未选择处理。
function parseISODate(value: string): { m: number; y: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)

  if (!match) {
    return null
  }

  const y = Number(match[1])
  const m = Number(match[2])
  const d = Number(match[3])
  const date = new Date(y, m - 1, d)

  return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d ? { m, y } : null
}

// 周一起始的当月日历格；开头以 null 占位，保持 7 列对齐。
function monthCells(y: number, m: number): Array<number | null> {
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7
  const days = new Date(y, m, 0).getDate()
  const cells: Array<number | null> = Array.from({ length: lead }, () => null)

  for (let d = 1; d <= days; d++) {
    cells.push(d)
  }

  return cells
}

interface DatePickerProps {
  className?: string
  clearLabel: string
  disabled?: boolean
  id?: string
  onChange: (value: string) => void
  placeholder: string
  value: string
  // 周一起始的星期表头文案，随调用方语言给出。
  weekdayLabels: readonly string[]
}

export function DatePicker({
  className,
  clearLabel,
  disabled = false,
  id,
  onChange,
  placeholder,
  value,
  weekdayLabels
}: DatePickerProps): React.JSX.Element {
  const [open, setOpen] = useState(false)
  // 视图年月独立于 value：每次展开吸附到已选日期（无则今天），浏览中随导航漂移。
  const [view, setView] = useState({ m: 1, y: 1970 })
  const rootRef = useRef<HTMLDivElement>(null)

  const openCalendar = (): void => {
    const selected = parseISODate(value)
    const now = new Date()

    setView({ m: selected?.m ?? now.getMonth() + 1, y: selected?.y ?? now.getFullYear() })
    setOpen(true)
  }

  useEffect(() => {
    if (!open) {
      return
    }

    // 捕获阶段监听：组件外的点击（包括会拦截冒泡的拖拽区域）都收起月历。
    const onDocPointerDown = (e: PointerEvent): void => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }

    document.addEventListener('pointerdown', onDocPointerDown, true)

    return () => document.removeEventListener('pointerdown', onDocPointerDown, true)
  }, [open])

  useEffect(() => {
    if (disabled) {
      setOpen(false)
    }
  }, [disabled])

  const shiftMonth = (delta: number): void =>
    setView(v => {
      const total = Math.min(12 * MAX_VIEW_YEAR + 11, Math.max(12 * MIN_VIEW_YEAR, v.y * 12 + v.m - 1 + delta))

      return { m: (total % 12) + 1, y: Math.floor(total / 12) }
    })

  const cells = useMemo(() => monthCells(view.y, view.m), [view])
  const now = new Date()
  const today = toISODate(now.getFullYear(), now.getMonth() + 1, now.getDate())

  const navBtnClass =
    'flex size-6 items-center justify-center rounded-md text-muted transition hover:bg-fill-faint hover:text-strong'

  return (
    <div className={className} ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="dialog"
        className={cn(
          INPUT_CLASS,
          'text-left disabled:pointer-events-none disabled:opacity-40',
          !value && 'text-faint'
        )}
        disabled={disabled}
        id={id}
        onClick={() => (open ? setOpen(false) : openCalendar())}
        type="button"
      >
        {value || placeholder}
      </button>
      {open && (
        <div
          aria-label={placeholder}
          className="mt-1.5 rounded-xl border border-line-hairline bg-surface-card p-3"
          role="dialog"
        >
          <div className="mb-2 flex items-center justify-between">
            <div className="flex gap-0.5">
              <button aria-label="-12" className={navBtnClass} onClick={() => shiftMonth(-12)} type="button">
                «
              </button>
              <button aria-label="-1" className={navBtnClass} onClick={() => shiftMonth(-1)} type="button">
                ‹
              </button>
            </div>
            <p className="text-[13px] font-semibold text-strong">
              {view.y} / {pad2(view.m)}
            </p>
            <div className="flex gap-0.5">
              <button aria-label="+1" className={navBtnClass} onClick={() => shiftMonth(1)} type="button">
                ›
              </button>
              <button aria-label="+12" className={navBtnClass} onClick={() => shiftMonth(12)} type="button">
                »
              </button>
            </div>
          </div>
          <div className="mb-1 grid grid-cols-7 gap-1">
            {weekdayLabels.map(w => (
              <span className="text-center text-[10px] text-faint" key={w}>
                {w}
              </span>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-1">
            {cells.map((d, i) => {
              if (d === null) {
                return <span key={`blank-${i}`} />
              }

              const iso = toISODate(view.y, view.m, d)
              const selected = iso === value

              return (
                <button
                  className={cn(
                    'flex h-7 items-center justify-center rounded-md text-[11px] transition',
                    selected
                      ? 'bg-accent-soft font-semibold text-strong'
                      : cn(
                          'text-muted hover:bg-fill-hover hover:text-strong',
                          iso === today && 'font-medium text-accent'
                        )
                  )}
                  key={d}
                  onClick={() => {
                    onChange(iso)
                    setOpen(false)
                  }}
                  type="button"
                >
                  {d}
                </button>
              )
            })}
          </div>
          <div className="mt-2">
            <button
              className="text-[10px] text-muted transition hover:text-strong"
              onClick={() => {
                onChange('')
                setOpen(false)
              }}
              type="button"
            >
              {clearLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
