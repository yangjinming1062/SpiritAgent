import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

// 合并 Tailwind 类：clsx 处理条件类，twMerge 解决同效工具类冲突。
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
