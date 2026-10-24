import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { transport } from '../lib/apiClient.js'

const initialRoles = {
  designer: { state: 'idle', iteration: 0 },
  engineer: { state: 'idle', iteration: 0 },
  machinist: { state: 'idle', iteration: 0 },
}

export function useDesignSession() {
  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  const [activeRole, setActiveRole] = useState(null)
  const [roles, setRoles] = useState(initialRoles)
  const [question, setQuestion] = useState(null)
  const [split, setSplit] = useState(null)
  const [summary, setSummary] = useState(null)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState(null)
  const sessionRef = useRef(null)

  const handleEvent = useCallback((event) => {
    switch (event.type) {
      case 'session.created':
        sessionRef.current = event.session_id
        setSessionId(event.session_id)
        setStatus('working')
        break
      case 'user.message':
        setMessages((items) => [...items, { id: crypto.randomUUID(), kind: 'user', text: event.text }])
        setQuestion(null)
        break
      case 'role.started':
        setActiveRole(event.role)
        setRoles((current) => ({
          ...current,
          [event.role]: { state: 'working', iteration: event.iteration || 1 },
        }))
        break
      case 'role.completed':
        setRoles((current) => ({
          ...current,
          [event.role]: { ...current[event.role], state: 'done' },
        }))
        break
      case 'assistant.thinking':
        setMessages((items) => [
          ...items,
          {
            id: crypto.randomUUID(),
            kind: 'thinking',
            role: event.role,
            text: event.text,
          },
        ])
        break
      case 'assistant.message':
        setMessages((items) => [
          ...items,
          { id: crypto.randomUUID(), kind: 'assistant', role: event.role, text: event.text },
        ])
        break
      case 'question':
        setQuestion(event)
        setMessages((items) => [
          ...items,
          {
            id: crypto.randomUUID(),
            kind: 'question',
            role: event.role,
            text: event.prompt,
            hint: event.hint,
          },
        ])
        break
      case 'engineer.split':
        setSplit(event)
        setMessages((items) => [
          ...items,
          {
            id: crypto.randomUUID(),
            kind: 'split',
            role: 'engineer',
            text: event.reason,
            parts: event.parts,
          },
        ])
        break
      case 'session.complete':
        setSummary(event.summary)
        setStatus('complete')
        setActiveRole(null)
        break
      case 'error':
        setError(event.message || 'Unknown workflow error.')
        setStatus('error')
        break
      default:
        break
    }
  }, [])

  const start = useCallback(async (prompt) => {
    setMessages([])
    setRoles(initialRoles)
    setActiveRole(null)
    setQuestion(null)
    setSplit(null)
    setSummary(null)
    setError(null)
    setStatus('starting')
    try {
      const id = await transport.startSession(prompt, handleEvent)
      sessionRef.current = id
      setSessionId(id)
    } catch (err) {
      setError(err.message)
      setStatus('error')
    }
  }, [handleEvent])

  const reply = useCallback(async (text) => {
    const id = sessionRef.current
    if (!id) return
    setQuestion(null)
    try {
      await transport.sendMessage(id, text, handleEvent)
    } catch (err) {
      setError(err.message)
      setStatus('error')
    }
  }, [handleEvent])

  const reset = useCallback(() => {
    transport.disconnect()
    sessionRef.current = null
    setSessionId(null)
    setMessages([])
    setRoles(initialRoles)
    setActiveRole(null)
    setQuestion(null)
    setSplit(null)
    setSummary(null)
    setError(null)
    setStatus('idle')
  }, [])

  useEffect(() => () => transport.disconnect(), [])

  return useMemo(() => ({
    sessionId,
    messages,
    activeRole,
    roles,
    question,
    split,
    summary,
    status,
    error,
    start,
    reply,
    reset,
  }), [sessionId, messages, activeRole, roles, question, split, summary, status, error, start, reply, reset])
}
