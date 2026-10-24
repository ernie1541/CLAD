const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const LARGE_PART_WORDS = [
  'engine',
  'vehicle',
  'aircraft',
  'turbine',
  'robot',
  'gearbox',
  'machine',
  'drone',
  'assembly',
]

const roleCopy = {
  designer: [
    'Reading the geometry as constraints, not just a description.',
    'Mapping dimensions, interfaces, and the first Onshape feature sequence.',
  ],
  engineer: [
    'Checking structure, interfaces, clearances, and whether this should be decomposed.',
    'Testing the design envelope before we spend features on the wrong geometry.',
  ],
  machinist: [
    'Translating the validated geometry into a manufacturable process plan.',
    'Checking tolerances, tool access, material use, and build orientation.',
  ],
}

export function createMockTransport() {
  const sessions = new Map()
  let activeRun = 0

  const emitAfter = async (runId, onEvent, event, ms = 450) => {
    await sleep(ms)
    if (runId === activeRun) onEvent(event)
  }

  async function beginDesigner(session, onEvent, runId) {
    session.role = 'designer'
    await emitAfter(runId, onEvent, { type: 'role.started', role: 'designer', iteration: 1 }, 300)
    await emitAfter(runId, onEvent, {
      type: 'assistant.thinking',
      role: 'designer',
      text: roleCopy.designer[0],
    }, 250)
    await emitAfter(runId, onEvent, {
      type: 'question',
      role: 'designer',
      id: 'design-envelope',
      prompt: 'Give the critical overall dimensions and any dimensions that must stay exact.',
      hint: 'Example: 120 × 80 × 30 mm; hole spacing 64 mm exact.',
    }, 1250)
    session.step = 'designer-question'
  }

  async function beginEngineer(session, onEvent, runId) {
    session.role = 'engineer'
    await emitAfter(runId, onEvent, { type: 'role.completed', role: 'designer' }, 180)
    await emitAfter(runId, onEvent, { type: 'role.started', role: 'engineer', iteration: 1 }, 250)
    await emitAfter(runId, onEvent, {
      type: 'assistant.thinking',
      role: 'engineer',
      text: roleCopy.engineer[0],
    }, 220)

    const large = LARGE_PART_WORDS.some((word) => session.prompt.toLowerCase().includes(word))
    if (large) {
      session.split = true
      await emitAfter(runId, onEvent, {
        type: 'engineer.split',
        role: 'engineer',
        reason: 'The request crosses the single-part complexity envelope, so I am splitting it into three interface-first modules.',
        parts: ['Primary structure', 'Functional mechanism', 'Interfaces + service features'],
      }, 1050)
    }

    await emitAfter(runId, onEvent, {
      type: 'question',
      role: 'engineer',
      id: 'engineering-priority',
      prompt: 'What matters most mechanically: load capacity, low mass, compact size, or another constraint?',
      options: ['Load capacity', 'Low mass', 'Compact size', 'Balanced'],
    }, session.split ? 900 : 1200)
    session.step = 'engineer-question'
  }

  async function beginMachinist(session, onEvent, runId) {
    session.role = 'machinist'
    await emitAfter(runId, onEvent, { type: 'role.completed', role: 'engineer' }, 180)
    await emitAfter(runId, onEvent, { type: 'role.started', role: 'machinist', iteration: 1 }, 260)
    await emitAfter(runId, onEvent, {
      type: 'assistant.thinking',
      role: 'machinist',
      text: roleCopy.machinist[0],
    }, 250)
    await emitAfter(runId, onEvent, {
      type: 'question',
      role: 'machinist',
      id: 'manufacturing-process',
      prompt: 'Choose the intended process so I can set realistic material and tolerance targets.',
      options: ['FDM 3D print', 'SLA/SLS', 'CNC mill', 'CNC turn', 'Auto-select'],
    }, 1250)
    session.step = 'machinist-question'
  }

  async function finish(session, onEvent, runId) {
    await emitAfter(runId, onEvent, { type: 'role.completed', role: 'machinist' }, 220)
    await emitAfter(runId, onEvent, {
      type: 'assistant.thinking',
      role: 'machinist',
      text: 'Locking the manufacturing constraints into an Onshape-ready handoff.',
    }, 260)
    await emitAfter(runId, onEvent, {
      type: 'session.complete',
      summary: {
        title: 'Ready for Onshape build',
        status: 'READY WITH MOCK DATA',
        parts: session.split ? 3 : 1,
        manufacturing: session.answers['manufacturing-process'] || 'Unspecified',
        note: 'Connect the Python backend to replace this mock event stream with Gemma + Onshape MCP events.',
      },
    }, 1400)
    session.step = 'complete'
  }

  return {
    async startSession(prompt, onEvent) {
      const runId = ++activeRun
      const id = crypto.randomUUID()
      const session = { id, runId, prompt, answers: {}, role: null, step: 'starting', split: false }
      sessions.set(id, session)
      onEvent({ type: 'session.created', session_id: id })
      onEvent({ type: 'user.message', text: prompt })
      void beginDesigner(session, onEvent, runId)
      return id
    },

    async sendMessage(sessionId, text, onEvent) {
      const session = sessions.get(sessionId)
      if (!session) throw new Error('Mock session not found.')
      onEvent({ type: 'user.message', text })

      if (session.step === 'designer-question') {
        session.answers['design-envelope'] = text
        session.step = 'designer-resolved'
        void beginEngineer(session, onEvent, session.runId)
        return
      }

      if (session.step === 'engineer-question') {
        session.answers['engineering-priority'] = text
        session.step = 'engineer-resolved'
        void beginMachinist(session, onEvent, session.runId)
        return
      }

      if (session.step === 'machinist-question') {
        session.answers['manufacturing-process'] = text
        session.step = 'machinist-resolved'
        void finish(session, onEvent, session.runId)
      }
    },

    disconnect() {
      activeRun += 1
    },
  }
}
