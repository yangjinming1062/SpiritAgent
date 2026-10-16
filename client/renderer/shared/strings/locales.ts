// 与后端 infrastructure/constants.py 的 SUPPORTED_LANGUAGES 对齐；新增语言须同步双方枚举和全部字典键。
const SUPPORTED_LOCALES = ['zh', 'en'] as const

export type Locale = (typeof SUPPORTED_LOCALES)[number]

const DEFAULT_LOCALE: Locale = 'zh'

export function normalizeLocale(raw: unknown): Locale {
  if (typeof raw === 'string') {
    const lower = raw.trim().toLowerCase()

    if ((SUPPORTED_LOCALES as readonly string[]).includes(lower)) {
      return lower as Locale
    }
  }

  return DEFAULT_LOCALE
}
