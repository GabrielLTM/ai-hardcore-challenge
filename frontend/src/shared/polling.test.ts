import { afterEach, describe, expect, it, vi } from 'vitest'
import { startPolling } from './hooks'

afterEach(() => { vi.useRealTimers() })

describe('startPolling (base de usePolling)', () => {
  it('chama a cada intervalo e para quando desligado', () => {
    vi.useFakeTimers()
    const fn = vi.fn()
    const stop = startPolling(fn, 3000)
    vi.advanceTimersByTime(2999)
    expect(fn).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fn).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(6000)
    expect(fn).toHaveBeenCalledTimes(3)
    stop()
    vi.advanceTimersByTime(30_000)
    expect(fn).toHaveBeenCalledTimes(3)
  })
})
