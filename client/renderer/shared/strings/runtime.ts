import { useStore } from '@nanostores/react'

import { $locale } from '@/shared/store/locale'

import { dict as en } from './dictionaries/en'
import { type Dictionary, dict as zh } from './dictionaries/zh'
import { type Locale } from './locales'

export type { Dictionary }

const DICTS: Record<Locale, Dictionary> = { en, zh }

export function useStrings(): Dictionary {
  return DICTS[useStore($locale)]
}

// 同步读，用于 store / 命令式 API 等非组件环境——不会触发 React 订阅。
export function getStrings(): Dictionary {
  return DICTS[$locale.get()]
}
