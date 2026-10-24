import { Canvas, useFrame } from '@react-three/fiber'
import { useMemo, useRef } from 'react'
import * as THREE from 'three'

const roleColors = {
  designer: '#8be9fd',
  engineer: '#bd93f9',
  machinist: '#ffb86c',
  idle: '#7f8a94',
}

function Module({ index, role, split }) {
  const ref = useRef(null)
  const targetX = split ? (index - 1) * 1.55 : (index - 1) * 0.18
  const targetScale = split ? 0.78 : index === 1 ? 1 : 0.86

  useFrame((state, delta) => {
    if (!ref.current) return
    ref.current.position.x = THREE.MathUtils.damp(ref.current.position.x, targetX, 4.6, delta)
    ref.current.position.y = THREE.MathUtils.damp(
      ref.current.position.y,
      Math.sin(state.clock.elapsedTime * 0.9 + index) * 0.08,
      3,
      delta,
    )
    const nextScale = THREE.MathUtils.damp(ref.current.scale.x, targetScale, 4.2, delta)
    ref.current.scale.setScalar(nextScale)
    ref.current.rotation.x += delta * (0.1 + index * 0.025)
    ref.current.rotation.y += delta * (0.18 + index * 0.03)
  })

  return (
    <mesh ref={ref} position={[(index - 1) * 0.18, 0, 0]}>
      <boxGeometry args={[1.1, 0.72, 0.72]} />
      <meshStandardMaterial
        color={roleColors[role] || roleColors.idle}
        metalness={0.56}
        roughness={0.28}
        wireframe={index !== 1}
        transparent
        opacity={index === 1 ? 0.88 : 0.42}
      />
    </mesh>
  )
}

function BlueprintField({ role, split }) {
  const group = useRef(null)
  const points = useMemo(() => {
    const list = []
    for (let i = 0; i < 90; i += 1) {
      const angle = i * 2.399
      const radius = 1.8 + (i % 9) * 0.08
      list.push(Math.cos(angle) * radius, ((i % 15) - 7) * 0.09, Math.sin(angle) * radius)
    }
    return new Float32Array(list)
  }, [])

  useFrame((state, delta) => {
    if (!group.current) return
    group.current.rotation.y += delta * (split ? 0.11 : 0.055)
    group.current.rotation.z = Math.sin(state.clock.elapsedTime * 0.25) * 0.06
  })

  return (
    <group ref={group}>
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[points, 3]} />
        </bufferGeometry>
        <pointsMaterial size={0.018} color={roleColors[role] || roleColors.idle} transparent opacity={0.34} />
      </points>
      {[0, 1, 2].map((index) => (
        <Module key={index} index={index} role={role || 'idle'} split={split} />
      ))}
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[2.2, 0.008, 8, 140]} />
        <meshBasicMaterial color={roleColors[role] || roleColors.idle} transparent opacity={0.25} />
      </mesh>
    </group>
  )
}

export function ThinkingScene({ role, split }) {
  return (
    <div className="thinking-scene" aria-hidden="true">
      <Canvas camera={{ position: [0, 0.25, 5.6], fov: 38 }} dpr={[1, 1.5]}>
        <ambientLight intensity={0.7} />
        <pointLight position={[3, 2, 4]} intensity={6} color="#ffffff" />
        <pointLight position={[-3, -1, 2]} intensity={3} color={roleColors[role] || roleColors.idle} />
        <BlueprintField role={role} split={Boolean(split)} />
      </Canvas>
      <div className="scene-label">
        <span>{split ? 'decomposing assembly' : role ? `${role} workspace` : 'design envelope'}</span>
        <span className="scene-coordinates">XYZ / PARAMETRIC</span>
      </div>
    </div>
  )
}
