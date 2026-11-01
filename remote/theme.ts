const storageKey = 'remote:theme'
const colors: Record<'dark' | 'light', string> = { dark: '#0d141c', light: '#f2f5f9' }

export type ThemeChoice = 'dark' | 'light' | 'system'

function resolve(choice: ThemeChoice): 'dark' | 'light' {
  return choice === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : choice
}

function apply(resolved: 'dark' | 'light'): void {
  document.documentElement.dataset.theme = resolved
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
  if (meta) {
    meta.content = colors[resolved]
  }
}

export function readTheme(): ThemeChoice {
  try {
    const value = localStorage.getItem(storageKey)
    return value === 'dark' || value === 'light' ? value : 'system'
  } catch {
    return 'system'
  }
}

/** 首帧前由 index.html 内联脚本先行解析，这里补齐系统主题的实时跟随。 */
export function initTheme(): void {
  apply(resolve(readTheme()))
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (readTheme() === 'system') {
      apply(resolve('system'))
    }
  })
}

export function saveTheme(choice: ThemeChoice): void {
  try {
    if (choice === 'system') {
      localStorage.removeItem(storageKey)
    } else {
      localStorage.setItem(storageKey, choice)
    }
  } catch {
    // 浏览器存储不可用时仅本次连接生效。
  }
  apply(resolve(choice))
}
