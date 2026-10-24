import { createMockTransport } from './mockTransport.js'

const useMock = import.meta.env.VITE_USE_MOCK !== 'false'
const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'

function createRealTransport() {
  let source = null

  const subscribe = (sessionId, onEvent) => {
    source?.close()
    source = new EventSource(`${apiBase}/sessions/${sessionId}/events`)
    source.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data))
      } catch (error) {
        onEvent({ type: 'error', message: 'Malformed event from backend.' })
        console.error(error)
      }
    }
    source.onerror = () => {
      onEvent({ type: 'connection.status', status: 'reconnecting' })
    }
  }

  return {
    async startSession(prompt, onEvent) {
      const response = await fetch(`${apiBase}/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      })
      if (!response.ok) throw new Error(`Unable to start design session (${response.status}).`)
      const payload = await response.json()
      subscribe(payload.session_id, onEvent)
      return payload.session_id
    },

    async sendMessage(sessionId, text) {
      const response = await fetch(`${apiBase}/sessions/${sessionId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      if (!response.ok) throw new Error(`Unable to send message (${response.status}).`)
    },

    disconnect() {
      source?.close()
      source = null
    },
  }
}

export const transport = useMock ? createMockTransport() : createRealTransport()
export const transportMode = useMock ? 'mock' : 'api'
