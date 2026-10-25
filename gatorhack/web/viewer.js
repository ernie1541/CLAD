// STL assembly viewer (three.js r160). Z-up to match the CAD convention.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

const PALETTE = [0x7389ad, 0xd9a933, 0x7e9e80, 0xb3735f, 0x9a9ca8, 0x8c73a6, 0x5f9ea0];

export function mountViewer(el, parts) {
  const scene = new THREE.Scene();
  const w = el.clientWidth, h = el.clientHeight;
  const camera = new THREE.PerspectiveCamera(35, w / h, 0.1, 100000);
  camera.up.set(0, 0, 1);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(w, h);
  el.appendChild(renderer.domElement);

  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a9096, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 1.4);
  key.position.set(1, -1.4, 2);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xffffff, 0.5);
  rim.position.set(-2, 1, 0.5);
  scene.add(rim);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  const group = new THREE.Group();
  scene.add(group);
  const loader = new STLLoader();
  let loaded = 0;
  const meshes = {};

  const fit = () => {
    const box = new THREE.Box3().setFromObject(group);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const r = size.length() * 0.5;
    const dist = r / Math.sin((camera.fov * Math.PI) / 360) * 1.1;
    camera.position.copy(center).add(new THREE.Vector3(1.0, 1.35, 0.85).normalize().multiplyScalar(dist));
    camera.near = dist / 100; camera.far = dist * 100; camera.updateProjectionMatrix();
    controls.target.copy(center);
    const grid = new THREE.GridHelper(Math.ceil(r * 4 / 10) * 10, 20, 0x8f979c, 0xb8bfbc);
    grid.rotation.x = Math.PI / 2;
    grid.position.set(center.x, center.y, box.min.z - 0.01);
    grid.material.transparent = true; grid.material.opacity = 0.5;
    scene.add(grid);
  };

  parts.forEach((p, i) => {
    loader.load(p.url, (geo) => {
      geo.computeVertexNormals();
      const mat = new THREE.MeshStandardMaterial({ color: PALETTE[i % PALETTE.length], metalness: 0.25, roughness: 0.55, flatShading: false });
      const mesh = new THREE.Mesh(geo, mat);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 28), new THREE.LineBasicMaterial({ color: 0x1f2529, transparent: true, opacity: 0.35 }));
      mesh.add(edges);
      mesh.userData.id = p.id;
      meshes[p.id] = mesh;
      group.add(mesh);
      if (++loaded === parts.length) fit();
    }, undefined, () => { if (++loaded === parts.length) fit(); });
  });

  let raf;
  const tick = () => { controls.update(); renderer.render(scene, camera); raf = requestAnimationFrame(tick); };
  tick();
  const ro = new ResizeObserver(() => {
    const W = el.clientWidth, H = el.clientHeight;
    camera.aspect = W / H; camera.updateProjectionMatrix(); renderer.setSize(W, H);
  });
  ro.observe(el);

  return {
    highlight(id) {
      Object.entries(meshes).forEach(([k, m]) => { m.material.transparent = !!id && k !== id; m.material.opacity = !id || k === id ? 1 : 0.18; });
    },
    colorOf(i) { return "#" + PALETTE[i % PALETTE.length].toString(16).padStart(6, "0"); },
    dispose() { cancelAnimationFrame(raf); ro.disconnect(); controls.dispose(); renderer.dispose(); el.innerHTML = ""; },
  };
}
export const partColor = (i) => "#" + PALETTE[i % PALETTE.length].toString(16).padStart(6, "0");
