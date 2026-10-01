import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** shadcn 组件的类名合并工具：条件类 + Tailwind 冲突去重。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
