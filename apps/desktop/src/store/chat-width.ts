import { atom } from 'nanostores'

import { persistString, storedString } from '@/lib/storage'

export type ChatWidthMode = 'comfortable' | 'default' | 'full' | 'wide'

export const CHAT_WIDTH_OPTIONS: ReadonlyArray<{ id: ChatWidthMode }> = [
  { id: 'default' },
  { id: 'comfortable' },
  { id: 'wide' },
  { id: 'full' }
]

const CHAT_WIDTH_STORAGE_KEY = 'hermes.desktop.chatWidth.mode'
const DEFAULT_CHAT_WIDTH_MODE: ChatWidthMode = 'default'

const CHAT_WIDTH_VALUES: Record<ChatWidthMode, string> = {
  default: '48.75rem',
  comfortable: '64rem',
  wide: '97.5rem',
  full: 'calc(100vw - 2rem)'
}

export function normalizeChatWidthMode(value: null | string): ChatWidthMode {
  return CHAT_WIDTH_OPTIONS.some(option => option.id === value) ? (value as ChatWidthMode) : DEFAULT_CHAT_WIDTH_MODE
}

export function chatWidthCssValue(mode: ChatWidthMode): string {
  return CHAT_WIDTH_VALUES[mode]
}

function applyChatWidthMode(mode: ChatWidthMode) {
  if (typeof document === 'undefined') {
    return
  }

  document.documentElement.style.setProperty('--composer-width', chatWidthCssValue(mode))
}

export const $chatWidthMode = atom<ChatWidthMode>(normalizeChatWidthMode(storedString(CHAT_WIDTH_STORAGE_KEY)))

$chatWidthMode.subscribe(mode => {
  persistString(CHAT_WIDTH_STORAGE_KEY, mode)
  applyChatWidthMode(mode)
})

export function setChatWidthMode(mode: ChatWidthMode) {
  $chatWidthMode.set(normalizeChatWidthMode(mode))
}
