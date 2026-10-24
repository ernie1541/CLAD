import { useEffect, useRef, useState } from 'react'
import { gsap } from 'gsap'

const starterPrompts = [
  'Design a compact wall mount for a 65 mm cylindrical sensor.',
  'Create an electronics enclosure with a removable lid and four M3 mounting points.',
  'Design a small gearbox housing that may need to be split into modules.',
]

export function PromptConsole({ status, question, onSubmit, onReset }) {
  const [value, setValue] = useState('')
  const shellRef = useRef(null)
  const inputRef = useRef(null)

  const isInitial = status === 'idle' || status === 'starting'
  const canSubmit = status === 'idle' || Boolean(question)

  useEffect(() => {
    if (!question) return
    const ctx = gsap.context(() => {
      gsap.fromTo(shellRef.current, { y: 7, opacity: 0.7 }, { y: 0, opacity: 1, duration: 0.45, ease: 'power3.out' })
    }, shellRef)
    inputRef.current?.focus()
    return () => ctx.revert()
  }, [question])

  const submit = (event) => {
    event.preventDefault()
    const text = value.trim()
    if (!text || !canSubmit) return
    onSubmit(text)
    setValue('')
  }

  const placeholder = question
    ? `Reply to ${question.role}…`
    : 'Describe something you want to design in Onshape…'

  return (
    <div className="prompt-area">
      {isInitial && status === 'idle' ? (
        <div className="starter-row">
          {starterPrompts.map((prompt) => (
            <button key={prompt} type="button" onClick={() => setValue(prompt)}>{prompt}</button>
          ))}
        </div>
      ) : null}

      <form className={`prompt-shell ${question ? 'has-question' : ''}`} ref={shellRef} onSubmit={submit}>
        <div className="prompt-context">
          <span>{question ? `${question.role} needs input` : 'new design prompt'}</span>
          {question?.options ? <span>{question.options.join(' · ')}</span> : <span>Onshape-ready specifications</span>}
        </div>
        <div className="prompt-line">
          <span className="prompt-chevron">›</span>
          <textarea
            ref={inputRef}
            rows="1"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                event.currentTarget.form?.requestSubmit()
              }
            }}
            placeholder={placeholder}
            disabled={!canSubmit}
          />
          <button className="submit-button" type="submit" disabled={!canSubmit || !value.trim()}>
            {question ? 'Reply' : 'Begin'}
          </button>
        </div>
      </form>

      {status !== 'idle' ? (
        <button className="reset-button" type="button" onClick={onReset}>Start over</button>
      ) : null}
    </div>
  )
}
