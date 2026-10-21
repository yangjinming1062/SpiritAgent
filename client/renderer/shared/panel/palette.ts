// 精灵窗、主窗口与桌面共用的面板类常量，消费 styles.css 的 --ui-* token。大面板用 panel→card，瞬时浮层用独立 overlay。

// 独立浮层表面（右键菜单、模态卡、下拉列表）：跟色彩轴走，不继承清透档的低 alpha。模态只铺这张卡，不用全屏暗化遮罩——透明精灵窗上会压黑桌面，日色下还容易配深字看不清。
export const SURFACE_OVERLAY = 'liquid-glass-overlay'

// 表单与确认弹窗使用实底，避免下层页面文字透出叠影。
export const SURFACE_MODAL = `${SURFACE_OVERLAY} solid-modal-surface`

// 按钮
export const BTN_PRIMARY =
  'inline-flex h-8 items-center justify-center gap-1.5 rounded-lg bg-inverse-surface px-3.5 text-xs font-medium text-inverse-fg transition hover:bg-inverse-surface-hover disabled:pointer-events-none disabled:opacity-40'
export const BTN_SUBTLE =
  'liquid-glass-pill inline-flex h-8 items-center justify-center gap-1.5 rounded-lg px-3.5 text-xs font-medium text-body transition hover:text-strong disabled:pointer-events-none disabled:opacity-40'
export const BTN_GHOST =
  'inline-flex h-7 items-center justify-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-muted transition hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40'
export const BTN_DANGER =
  'inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-danger-line bg-danger-bg px-3.5 text-xs font-medium text-danger-fg transition hover:bg-danger-bg-hover disabled:pointer-events-none disabled:opacity-40'
export const BTN_ICON =
  'inline-flex size-7 items-center justify-center rounded-lg text-muted transition hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40 [&_svg]:size-4'
// 媒体卡与复核队列里的次级操作：小号描边按钮，不抢主操作。
export const BTN_COMPACT =
  'inline-flex items-center justify-center gap-1.5 rounded-md border border-line-standard px-3 py-1 text-xs text-body transition hover:border-line-strong hover:text-strong disabled:pointer-events-none disabled:opacity-40'

// 徽标：计数与状态。计数是中性填充，状态是强调色。
export const BADGE = 'inline-flex shrink-0 items-center rounded bg-fill-faint px-1 py-0.2 text-[10px] text-muted'
export const BADGE_COUNT = 'inline-flex shrink-0 items-center rounded bg-fill-faint px-1 py-0.2 text-[9px] text-muted'
export const BADGE_ACCENT =
  'inline-flex shrink-0 items-center rounded bg-accent-soft px-1 py-0.5 text-[10px] text-accent'

// 输入与选择
export const INPUT_CLASS =
  'w-full rounded-lg border border-line-standard bg-fill-faint px-3 py-2 text-xs text-strong outline-none placeholder:text-faint focus:border-focus-line'
export const CHIP =
  'liquid-glass-pill inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] text-muted'
export const CHIP_FILTER =
  'liquid-glass-pill inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] text-muted transition hover:text-strong'
// 激活态 chip：过滤按钮与 Pill 高亮共用。
export const CHIP_FILTER_ACTIVE =
  'liquid-glass-pill liquid-glass-pill-active inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-medium text-strong'

// 文本层级
export const SECTION_TITLE = 'text-[12px] font-medium tracking-wide text-muted'
export const SETTINGS_INTRO_TITLE = 'text-[15px] font-medium tracking-tight text-strong'
export const SETTINGS_INTRO_HINT = 'mt-1 max-w-xl text-[11px] leading-relaxed text-muted'
export const SETTINGS_ROW_TITLE = 'text-[13px] font-medium text-strong'
export const SETTINGS_ROW_DESC = 'mt-0.5 text-[11px] leading-relaxed text-muted'
export const FIELD_LABEL = 'mb-1 block text-[11px] text-muted font-medium'
export const HINT_TEXT = 'text-[10px] leading-relaxed text-faint'
