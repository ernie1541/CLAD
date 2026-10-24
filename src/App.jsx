import { useLayoutEffect, useRef } from 'react'
import { gsap } from 'gsap'
import { useDesignSession } from './hooks/useDesignSession.js'
import { transportMode } from './lib/apiClient.js'
import { Conversation } from './components/Conversation.jsx'
import { PromptConsole } from './components/PromptConsole.jsx'
import { RoleRail } from './components/RoleRail.jsx'
import { ThinkingScene } from './components/ThinkingScene.jsx'

function App() {
  const appRef = useRef(null)
  const session = useDesignSession()

  useLayoutEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from('.app-header > *', {
        opacity: 0,
        y: -10,
        duration: 0.65,
        stagger: 0.07,
        ease: 'power3.out',
      })
      gsap.from('.workspace-panel', {
        opacity: 0,
        y: 14,
        duration: 0.8,
        stagger: 0.08,
        ease: 'power3.out',
      })
    }, appRef)
    return () => ctx.revert()
  }, [])

  const submit = (text) => {
    if (session.question) session.reply(text)
    else session.start(text)
  }

  return (
    <main className="app-shell" ref={appRef}>
      <header className="app-header">
        <div className="brand-lockup">
          <span className="brand-mark">F</span>
          <div>
            <strong>FORGE</strong>
            <span>Onshape design intelligence</span>
          </div>
        </div>
        <div className="header-meta">
          <span className={`connection-dot is-${transportMode}`} />
          <span>{transportMode === 'mock' ? 'Mock transport' : 'Python API'}</span>
          <span className="header-divider" />
          <span>No login required</span>
        </div>
      </header>

      <section className="workspace-grid">
        <aside className="workspace-panel role-panel">
          <div className="panel-heading">
            <span className="eyebrow">WORKFLOW</span>
            <span>{session.sessionId ? session.sessionId.slice(0, 8) : 'unassigned'}</span>
          </div>
          <RoleRail roles={session.roles} activeRole={session.activeRole} />
          <div className="iteration-note">
            <span>Iteration ceiling</span>
            <strong>3 × role</strong>
          </div>
        </aside>

        <section className="workspace-panel conversation-panel">
          <div className="panel-heading">
            <span className="eyebrow">PROMPT / SPECIFICATION</span>
            <span>{session.status}</span>
          </div>
          <Conversation messages={session.messages} summary={session.summary} />
          {session.error ? <div className="error-banner">{session.error}</div> : null}
        </section>

        <aside className="workspace-panel scene-panel">
          <div className="panel-heading">
            <span className="eyebrow">SYSTEM STATE</span>
            <span>{session.activeRole || 'idle'}</span>
          </div>
          <ThinkingScene role={session.activeRole} split={session.split} />
          <div className="scene-status-copy">
            {session.split ? (
              <>
                <strong>Assembly split active</strong>
                <span>{session.split.parts?.length || 3} interface-first modules</span>
              </>
            ) : (
              <>
                <strong>{session.activeRole ? `${session.activeRole} is working` : 'Awaiting a design'}</strong>
                <span>{session.activeRole ? 'Live agent state visualization' : 'Submit a prompt to begin'}</span>
              </>
            )}
          </div>
        </aside>
      </section>

      <footer className="composer-dock">
        <PromptConsole
          status={session.status}
          question={session.question}
          onSubmit={submit}
          onReset={session.reset}
        />
      </footer>
    </main>
  )
}

export default App
