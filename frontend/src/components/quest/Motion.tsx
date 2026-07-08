import type { CSSProperties, PropsWithChildren } from 'react'

/**
 * Subtle entrance animation wrapper. Implemented with CSS keyframes so the app
 * stays dependency-free and instantly deployable. To upgrade to spring-based
 * physics later, swap the inner element for a framer-motion `motion.div`
 * (`initial`/`animate`/`transition`) without changing call sites.
 */
type RevealProps = PropsWithChildren<{
  /** Stagger index — each step delays the reveal by ~60ms. */
  index?: number
  className?: string
  style?: CSSProperties
}>

export function Reveal({ index = 0, className = '', style, children }: RevealProps) {
  return (
    <div className={`quest-rise ${className}`} style={{ animationDelay: `${Math.min(index, 12) * 60}ms`, ...style }}>
      {children}
    </div>
  )
}
