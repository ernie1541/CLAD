import { useLayoutEffect, useRef } from 'react'
import { gsap } from 'gsap'

const roleMeta = {
  designer: { index: '01', title: 'Designer', subtitle: 'Geometry + intent' },
  engineer: { index: '02', title: 'Engineer', subtitle: 'Function + structure' },
  machinist: { index: '03', title: 'Machinist', subtitle: 'Process + tolerance' },
}

export function RoleRail({ roles, activeRole }) {
  const root = useRef(null)

  useLayoutEffect(() => {
    const ctx = gsap.context(() => {
      gsap.to('.role-card.is-active .role-pulse', {
        scale: 1.8,
        opacity: 0,
        duration: 1.25,
        repeat: -1,
        ease: 'power2.out',
      })
      gsap.fromTo(
        '.role-card.is-active',
        { y: 4 },
        { y: 0, duration: 0.45, ease: 'power3.out' },
      )
    }, root)
    return () => ctx.revert()
  }, [activeRole])

  return (
    <div className="role-rail" ref={root}>
      {Object.entries(roleMeta).map(([key, meta]) => {
        const state = roles[key]?.state || 'idle'
        const isActive = key === activeRole
        return (
          <div className={`role-card ${isActive ? 'is-active' : ''} is-${state}`} key={key}>
            <div className="role-index">{meta.index}</div>
            <div className="role-copy">
              <strong>{meta.title}</strong>
              <span>{meta.subtitle}</span>
            </div>
            <div className="role-state">
              <span className="role-dot">
                <span className="role-pulse" />
              </span>
              <span>{state === 'working' ? `working · ${roles[key].iteration}/3` : state}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
