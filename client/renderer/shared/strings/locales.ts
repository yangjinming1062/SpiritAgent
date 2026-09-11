// 与后端 components/constants.py 的 SUPPORTED_LANGUAGES 对齐（zh | en）。
// 新增语言需同步：后端 SUPPORTED_LANGUAGES + 本文件 SUPPORTED_LOCALES + 全部双语 dict 键集（PROTOCOL §1.4）。
export const SUPPORTED_LOCALES = ['zh', 'en'] as const

export type Locale = (typeof SUPPORTED_LOCALES)[number]

export const DEFAULT_LOCALE: Locale = 'zh'

export function normalizeLocale(raw: unknown): Locale {
  if (typeof raw === 'string') {
    const lower = raw.trim().toLowerCase()

    if ((SUPPORTED_LOCALES as readonly string[]).includes(lower)) {
      return lower as Locale
    }
  }

  return DEFAULT_LOCALE
}
