import { createContext, useContext } from 'react'

export interface ToastItem { id: number; text: string; tone: 'default' | 'danger' }
export type Push = (text: string, tone?: ToastItem['tone']) => void
export const ToastContext = createContext<Push>(() => {})
export const useToast = () => useContext(ToastContext)
