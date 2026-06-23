import { describe, expect, it } from 'vitest'

import { CHAT_WIDTH_OPTIONS, chatWidthCssValue, normalizeChatWidthMode, type ChatWidthMode } from './chat-width'

describe('chat width display preference', () => {
  it('offers comfortable reading choices with a doubled wide width', () => {
    expect(CHAT_WIDTH_OPTIONS.map(option => option.id)).toEqual(['default', 'comfortable', 'wide', 'full'])
    expect(chatWidthCssValue('default')).toBe('48.75rem')
    expect(chatWidthCssValue('comfortable')).toBe('64rem')
    expect(chatWidthCssValue('wide')).toBe('97.5rem')
    expect(chatWidthCssValue('full')).toBe('calc(100vw - 2rem)')
  })

  it('normalizes unknown stored values back to default', () => {
    expect(normalizeChatWidthMode('wide')).toBe<ChatWidthMode>('wide')
    expect(normalizeChatWidthMode('not-real')).toBe<ChatWidthMode>('default')
  })
})
