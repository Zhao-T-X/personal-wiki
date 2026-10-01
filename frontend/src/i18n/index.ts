/* 多语言入口：zh-CN（默认/兜底）+ en。
 *
 * 设计原则：语言包按「界面区域」分块，键名跟随组件而不是页面，
 * 这样迁移哪个组件就搬哪个块，不必一次性翻译全站。
 * 切换语言持久化到 localStorage，并同步 <html lang>。 */
import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN'
import en from './locales/en'

export type AppLocale = 'zh-CN' | 'en'

const STORAGE_KEY = 'app-locale'

export function detectLocale(): AppLocale {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'en' || saved === 'zh-CN') return saved
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
}

export const i18n = createI18n({
  legacy: false,
  locale: detectLocale(),
  fallbackLocale: 'zh-CN',
  missingWarn: false,
  fallbackWarn: false,
  messages: { 'zh-CN': zhCN, en },
})

export function setLocale(loc: AppLocale) {
  i18n.global.locale.value = loc
  localStorage.setItem(STORAGE_KEY, loc)
  document.documentElement.lang = loc
}

// 初始挂载时同步一次 <html lang>
document.documentElement.lang = i18n.global.locale.value
