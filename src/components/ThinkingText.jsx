import { useEffect, useMemo, useRef, useState } from 'react'
import { gsap } from 'gsap'

function seededTiming(index) {
  const value = Math.abs(Math.sin(index * 13.371 + 0.83))
  if (value > 0.94) return 0.52
  if (value > 0.78) return 0.24
  return 0.075
}

export function ThinkingText({ text, onComplete }) {
  const words = useMemo(() => text.trim().split(/\s+/), [text])
  const [visibleCount, setVisibleCount] = useState(0)
  const [waiting, setWaiting] = useState(false)
  const cursorRef = useRef(null)

  useEffect(() => {
    setVisibleCount(0)
    setWaiting(false)

    const timeline = gsap.timeline({
      onComplete: () => {
        setWaiting(false)
        onComplete?.()
      },
    })

    words.forEach((_, index) => {
      const delay = seededTiming(index)
      if (delay > 0.2) {
        timeline.call(() => setWaiting(true), [], `+=${Math.max(0, delay - 0.12)}`)
        timeline.call(() => {
          setWaiting(false)
          setVisibleCount(index + 1)
        }, [], '+=0.12')
      } else {
        timeline.call(() => setVisibleCount(index + 1), [], `+=${delay}`)
      }
    })

    return () => timeline.kill()
  }, [text, words, onComplete])

  useEffect(() => {
    if (!cursorRef.current) return
    const tween = gsap.to(cursorRef.current, {
      opacity: waiting ? 0.12 : 0.42,
      duration: waiting ? 0.34 : 0.7,
      repeat: -1,
      yoyo: true,
      ease: 'sine.inOut',
    })
    return () => tween.kill()
  }, [waiting])

  return (
    <span className="thinking-text" aria-label={text}>
      {words.slice(0, visibleCount).map((word, index) => (
        <span className="thinking-word" key={`${word}-${index}`}>
          {word}{index < visibleCount - 1 ? ' ' : ''}
        </span>
      ))}
      <span ref={cursorRef} className={`thinking-cursor ${waiting ? 'is-waiting' : ''}`} aria-hidden="true" />
    </span>
  )
}
