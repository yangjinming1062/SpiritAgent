// 面板设计语言的类常量词汇表——两个窗口（精灵窗浮层 / 工具窗）共用的唯一视觉来源。
// 全部消费 styles.css 的语义 token（--ui-*），主题在 html[data-theme] 上换肤。
// 分层：大面板实体表面（阶梯 chrome→panel→card），瞬时浮层走 overlay（可读、不跟窗壳玻璃同一透明度）。

// 表面阶梯（实体档）
export const SURFACE_CHROME = 'bg-surface-chrome'

// 精灵右键等桌面浮层：跟色彩轴走，不继承清透档的低 alpha。
export const SURFACE_OVERLAY = 'liquid-glass-overlay'

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

// 输入与选择
export const INPUT_CLASS =
  'w-full rounded-lg border border-line-standard bg-fill-faint px-3 py-2 text-xs text-strong outline-none placeholder:text-faint focus:border-focus-line'
export const CHIP =
  'liquid-glass-pill inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] text-muted'
export const CHIP_ACTIVE =
  'liquid-glass-pill liquid-glass-pill-active inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-medium text-strong'
export const CHIP_FILTER =
  'liquid-glass-pill inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] text-muted transition hover:text-strong'
export const CHIP_FILTER_ACTIVE =
  'liquid-glass-pill liquid-glass-pill-active inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-medium text-strong'

// 设置侧栏导航
export const NAV_ITEM =
  'group relative flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-left text-xs text-muted transition-all duration-150 hover:bg-fill-hover hover:text-strong'
export const NAV_ITEM_ACTIVE =
  'group relative flex h-8 w-full items-center gap-2 rounded-lg bg-accent-soft px-2.5 text-left text-xs font-medium text-accent shadow-[inset_0_0_0_1px_var(--ui-line-hairline)] before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-0.5 before:rounded-full before:bg-accent'

// 文本层级
export const SECTION_TITLE = 'text-[12px] font-medium tracking-wide text-muted'
export const SETTINGS_INTRO_TITLE = 'text-[15px] font-medium tracking-tight text-strong'
export const SETTINGS_INTRO_HINT = 'mt-1 max-w-xl text-[11px] leading-relaxed text-muted'
export const SETTINGS_ROW_TITLE = 'text-[13px] font-medium text-strong'
export const SETTINGS_ROW_DESC = 'mt-0.5 text-[11px] leading-relaxed text-muted'
export const FIELD_LABEL = 'mb-1 block text-[11px] text-muted font-medium'
export const HINT_TEXT = 'text-[10px] leading-relaxed text-faint'

// 科技面板与卡片类
export const TECH_CARD =
  'relative overflow-hidden rounded-xl border border-line-hairline bg-surface-card transition-all duration-200 hover:border-line-strong'
