import type { ButtonHTMLAttributes, ReactNode } from 'react'

export default function ActionButton({
  children,
  className = '',
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode }) {
  return (
    <button type="button" className={`wx-action-btn ${className}`.trim()} {...rest}>
      {children}
    </button>
  )
}
