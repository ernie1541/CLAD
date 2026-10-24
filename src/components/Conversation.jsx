import { useEffect, useRef } from 'react'
import { ThinkingText } from './ThinkingText.jsx'

const roleLabel = (role) => role ? role.charAt(0).toUpperCase() + role.slice(1) : 'System'

export function Conversation({ messages, summary }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, summary])

  return (
    <div className="conversation" aria-live="polite">
      {messages.length === 0 ? (
        <div className="empty-state">
          <span className="eyebrow">DESIGN SESSION / NEW</span>
          <h2>Describe the part.<br />The system will build it.</h2>
          <p>Designer → Engineer → Machinist, with a maximum of three passes per role.</p>
        </div>
      ) : null}

      {messages.map((message) => (
        <article className={`message message-${message.kind}`} key={message.id}>
          {message.kind !== 'user' && (
            <header>
              <span>{roleLabel(message.role)}</span>
              <span className="message-kind">{message.kind}</span>
            </header>
          )}

          {message.kind === 'thinking' ? (
            <p className="thinking-line"><ThinkingText text={message.text} /></p>
          ) : (
            <p>{message.text}</p>
          )}

          {message.hint ? <small>{message.hint}</small> : null}

          {message.kind === 'split' && message.parts ? (
            <div className="split-parts">
              {message.parts.map((part, index) => (
                <div key={part}>
                  <span>0{index + 1}</span>
                  <strong>{part}</strong>
                </div>
              ))}
            </div>
          ) : null}
        </article>
      ))}

      {summary ? (
        <article className="summary-card">
          <span className="eyebrow">HANDOFF / READY</span>
          <h3>{summary.title}</h3>
          <div className="summary-grid">
            <span>Status</span><strong>{summary.status}</strong>
            <span>Module count</span><strong>{summary.parts}</strong>
            <span>Manufacturing</span><strong>{summary.manufacturing}</strong>
          </div>
          <p>{summary.note}</p>
        </article>
      ) : null}
      <div ref={bottomRef} />
    </div>
  )
}
