"use client";

import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import * as THREE from "three";
import { useShallow } from "zustand/react/shallow";
import { C, congestionTint, robotColor } from "@/lib/colors";
import type { Cell, GridModel, WorldSnapshot, ZoneModel } from "@/lib/types";
import { useTwinStore } from "@/store/twinStore";
import { disposeObject, getGlowTexture, getHatchTexture, makeLabelSprite, makeMapper, makeTextTexture, type Mapper } from "./three-helpers";

const MAX_PATH = 64;
const tmpMatrix = new THREE.Matrix4();
const tmpPos = new THREE.Vector3();
const tmpQuat = new THREE.Quaternion();
const tmpScale = new THREE.Vector3(1, 1, 1);

function cellsOfType(grid: GridModel, digit: string): Cell[] {
  const out: Cell[] = [];
  for (let y = 0; y < grid.height; y++) {
    const row = grid.rows[y] ?? "";
    for (let x = 0; x < grid.width; x++) if (row[x] === digit) out.push([x, y]);
  }
  return out;
}

// ------------------------------------------------------------------------------------------------
// static layers
// ------------------------------------------------------------------------------------------------

function Instanced({
  cells,
  map,
  size,
  color,
  emissive,
  emissiveIntensity = 0,
  y,
  opacity,
}: {
  cells: Cell[];
  map: Mapper;
  size: [number, number, number];
  color: string;
  emissive?: string;
  emissiveIntensity?: number;
  y: number;
  opacity?: number;
}) {
  const ref = useRef<THREE.InstancedMesh>(null);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    cells.forEach(([cx, cy], i) => {
      tmpPos.set(map.x(cx), y, map.z(cy));
      tmpMatrix.compose(tmpPos, tmpQuat, tmpScale);
      mesh.setMatrixAt(i, tmpMatrix);
    });
    mesh.count = cells.length;
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [cells, map, y]);
  if (cells.length === 0) return null;
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, Math.max(1, cells.length)]} frustumCulled={false}>
      <boxGeometry args={size} />
      <meshStandardMaterial
        color={color}
        emissive={emissive ?? "#000000"}
        emissiveIntensity={emissiveIntensity}
        roughness={0.75}
        metalness={0.1}
        transparent={opacity !== undefined}
        opacity={opacity ?? 1}
      />
    </instancedMesh>
  );
}

function Pad({
  cell,
  map,
  color,
  label,
  dim,
}: {
  cell: Cell;
  map: Mapper;
  color: string;
  label: string;
  dim?: boolean;
}) {
  const tex = useMemo(() => makeTextTexture(label, { color, size: 34, width: 128, height: 64 }), [label, color]);
  useEffect(() => () => tex.dispose(), [tex]);
  const c = dim ? "#3a1d1d" : color;
  return (
    <group position={[map.x(cell[0]), 0, map.z(cell[1])]}>
      <mesh position={[0, 0.05, 0]}>
        <boxGeometry args={[1.1, 0.1, 1.1]} />
        <meshStandardMaterial color={c} emissive={c} emissiveIntensity={dim ? 0.1 : 0.5} roughness={0.5} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.12, 0]}>
        <planeGeometry args={[1.6, 0.8]} />
        <meshBasicMaterial map={tex} transparent depthWrite={false} opacity={dim ? 0.5 : 1} />
      </mesh>
    </group>
  );
}

function ZoneLayer({ zones, map }: { zones: ZoneModel[]; map: Mapper }) {
  const occupancy = useTwinStore(useShallow((s) => s.zoneOccupancy));
  const closed = useTwinStore(useShallow((s) => s.world?.grid?.closed_zones ?? []));
  const closedSet = useMemo(() => new Set(closed), [closed]);
  return (
    <group>
      {zones.map((z) => (
        <ZonePlane key={z.id} zone={z} map={map} occupancy={occupancy[z.id] ?? 0} closed={closedSet.has(z.id) || z.closed} />
      ))}
    </group>
  );
}

function ZonePlane({ zone, map, occupancy, closed }: { zone: ZoneModel; map: Mapper; occupancy: number; closed: boolean }) {
  const w = zone.x1 - zone.x0 + 1;
  const h = zone.y1 - zone.y0 + 1;
  const cx = (map.x(zone.x0) + map.x(zone.x1)) / 2;
  const cz = (map.z(zone.y0) + map.z(zone.y1)) / 2;
  const ratio = zone.capacity > 0 ? occupancy / zone.capacity : 0;
  const congested = zone.kind === "storage" || zone.kind === "corridor";
  const tint = closed ? C.bad : congested ? congestionTint(ratio) : zone.kind === "dock" ? C.warn : C.good;
  const opacity = closed ? 0.28 : congested ? 0.06 + Math.min(0.3, ratio * 0.2) : 0.05;
  const isBig = zone.kind === "storage";
  const labelTex = useMemo(
    () =>
      makeTextTexture(isBig ? zone.id : zone.kind === "corridor" ? zone.id : zone.name.toUpperCase(), {
        color: isBig ? "rgba(230,237,243,0.55)" : "rgba(139,152,165,0.7)",
        size: isBig ? 64 : 26,
        width: 256,
        height: 96,
        letterSpacing: isBig ? 0 : 3,
      }),
    [zone.id, zone.kind, zone.name, isBig],
  );
  useEffect(() => () => labelTex.dispose(), [labelTex]);
  const hatch = useMemo(() => {
    if (!closed) return null;
    const t = getHatchTexture().clone();
    t.repeat.set(w / 2, h / 2);
    t.needsUpdate = true;
    return t;
  }, [closed, w, h]);
  const labelW = isBig ? Math.min(w, 6) : Math.min(Math.max(w, 3), 6);
  return (
    <group position={[cx, 0, cz]}>
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.03, 0]}>
        <planeGeometry args={[w, h]} />
        <meshBasicMaterial color={tint} transparent opacity={opacity} depthWrite={false} />
      </mesh>
      {hatch && (
        <mesh rotation-x={-Math.PI / 2} position={[0, 0.035, 0]}>
          <planeGeometry args={[w, h]} />
          <meshBasicMaterial map={hatch} transparent opacity={0.5} depthWrite={false} />
        </mesh>
      )}
      {/* storage labels float above the shelf boxes so they stay readable from the default camera */}
      <mesh
        rotation-x={-Math.PI / 2}
        position={[0, isBig ? 1.35 : 0.04, 0]}
        rotation-z={zone.kind === "corridor" && h > w ? Math.PI / 2 : 0}
      >
        <planeGeometry args={[labelW, labelW * 0.375]} />
        <meshBasicMaterial map={labelTex} transparent depthWrite={false} opacity={isBig ? 0.85 : 1} />
      </mesh>
      {congested && ratio >= 1 && (
        <mesh rotation-x={-Math.PI / 2} position={[0, 0.032, 0]}>
          <planeGeometry args={[w, h]} />
          <meshBasicMaterial color={tint} transparent opacity={0.08} depthWrite={false} />
        </mesh>
      )}
    </group>
  );
}

function StaticWorld({ world, map }: { world: WorldSnapshot; map: Mapper }) {
  const grid = world.grid!;
  const shelves = useMemo(() => cellsOfType(grid, "1"), [grid]);
  const walls = useMemo(() => cellsOfType(grid, "2"), [grid]);
  const staging = useMemo(() => cellsOfType(grid, "6"), [grid]);
  const conveyors = useMemo(() => cellsOfType(grid, "5"), [grid]);
  const blocked = useMemo(() => grid.blocked ?? [], [grid]);
  const dockOpen = useTwinStore(useShallow((s) => s.dockOpen));
  const chargerEnabled = useMemo(() => Object.fromEntries(world.chargers.map((c) => [c.id, c.enabled])), [world.chargers]);

  return (
    <group>
      {/* floor */}
      <mesh rotation-x={-Math.PI / 2} position={[0, -0.01, 0]} receiveShadow>
        <planeGeometry args={[grid.width + 6, grid.height + 6]} />
        <meshStandardMaterial color="#0c1016" roughness={1} />
      </mesh>
      <Grid
        position={[0, 0.005, 0]}
        args={[grid.width, grid.height]}
        cellSize={1}
        cellThickness={0.5}
        cellColor="#18202a"
        sectionSize={10}
        sectionThickness={0.9}
        sectionColor="#243040"
        fadeDistance={260}
        fadeStrength={1}
        infiniteGrid={false}
      />
      <Instanced cells={shelves} map={map} size={[0.86, 1.1, 0.86]} color="#334155" y={0.55} />
      <Instanced cells={walls} map={map} size={[1, 1.6, 1]} color="#1f2933" y={0.8} />
      <Instanced cells={staging} map={map} size={[0.96, 0.02, 0.96]} color="#141b24" y={0.01} />
      <Instanced cells={conveyors} map={map} size={[1, 0.25, 1]} color="#2a3644" emissive="#22d3ee" emissiveIntensity={0.08} y={0.125} />
      <Instanced cells={blocked} map={map} size={[0.9, 0.5, 0.9]} color={C.bad} emissive={C.bad} emissiveIntensity={0.7} y={0.25} opacity={0.85} />
      {world.docks.map((d) => (
        <Pad key={d.id} cell={d.cell} map={map} color={C.warn} label={d.id} dim={!(dockOpen[d.id] ?? d.open)} />
      ))}
      {world.chargers.map((c) => (
        <Pad key={c.id} cell={c.cell} map={map} color={C.good} label={c.id} dim={!(chargerEnabled[c.id] ?? true)} />
      ))}
    </group>
  );
}

// ------------------------------------------------------------------------------------------------
// robots
// ------------------------------------------------------------------------------------------------

function RobotEntity({ id, map }: { id: string; map: Mapper }) {
  const group = useRef<THREE.Group>(null);
  const body = useRef<THREE.MeshStandardMaterial>(null);
  const glow = useRef<THREE.SpriteMaterial>(null);
  const ring = useRef<THREE.Mesh>(null);
  const initialised = useRef(false);
  const lastColor = useRef("");
  const target = useMemo(() => new THREE.Vector3(), []);
  const select = useTwinStore((s) => s.selectRobot);

  const label = useMemo(() => makeLabelSprite(id, "#e6edf3"), [id]);
  const line = useMemo(() => {
    const geom = new THREE.BufferGeometry();
    const positions = new Float32Array((MAX_PATH + 1) * 3);
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geom.setDrawRange(0, 0);
    const mat = new THREE.LineBasicMaterial({ color: C.accent, transparent: true, opacity: 0.75 });
    const obj = new THREE.Line(geom, mat);
    obj.frustumCulled = false;
    return obj;
  }, []);
  useEffect(
    () => () => {
      disposeObject(label);
      disposeObject(line);
    },
    [label, line],
  );

  useFrame((state, dt) => {
    const g = group.current;
    if (!g) return;
    const s = useTwinStore.getState();
    const r = s.robots[id];
    if (!r) {
      g.visible = false;
      line.visible = false;
      return;
    }
    g.visible = true;
    target.set(map.x(r.cell[0]), 0, map.z(r.cell[1]));
    if (!initialised.current) {
      g.position.copy(target);
      initialised.current = true;
    } else {
      const k = 1 - Math.exp(-dt * 9);
      g.position.lerp(target, k);
      if (g.position.distanceToSquared(target) > 36) g.position.copy(target); // teleport on reset
    }
    const color = robotColor(r.status);
    if (color !== lastColor.current) {
      lastColor.current = color;
      body.current?.color.set(color);
      body.current?.emissive.set(color);
      glow.current?.color.set(color);
      (line.material as THREE.LineBasicMaterial).color.set(color);
    }
    if (body.current) {
      body.current.emissiveIntensity =
        r.status === "failed" ? 0.5 + 0.6 * Math.abs(Math.sin(state.clock.elapsedTime * 5)) : r.status === "idle" ? 0.15 : 0.45;
    }
    if (glow.current) glow.current.opacity = r.status === "failed" ? 0.25 + 0.35 * Math.abs(Math.sin(state.clock.elapsedTime * 5)) : 0.3;
    // remaining path polyline (world coordinates)
    const attr = line.geometry.getAttribute("position") as THREE.BufferAttribute;
    const arr = attr.array as Float32Array;
    const n = Math.min(r.path.length, MAX_PATH);
    if (n > 0) {
      arr[0] = g.position.x;
      arr[1] = 0.12;
      arr[2] = g.position.z;
      for (let i = 0; i < n; i++) {
        const c = r.path[i];
        arr[(i + 1) * 3] = map.x(c[0]);
        arr[(i + 1) * 3 + 1] = 0.12;
        arr[(i + 1) * 3 + 2] = map.z(c[1]);
      }
      line.geometry.setDrawRange(0, n + 1);
      attr.needsUpdate = true;
      line.visible = true;
    } else {
      line.visible = false;
    }
    if (ring.current) ring.current.visible = s.selectedRobotId === id;
  });

  const onClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    select(useTwinStore.getState().selectedRobotId === id ? null : id);
  };

  return (
    <>
      <group ref={group} onClick={onClick}>
        <mesh position={[0, 0.5, 0]} castShadow>
          <capsuleGeometry args={[0.27, 0.42, 4, 12]} />
          <meshStandardMaterial ref={body} color="#8b98a5" emissive="#8b98a5" emissiveIntensity={0.3} roughness={0.35} metalness={0.25} />
        </mesh>
        <sprite position={[0, 0.2, 0]} scale={[1.9, 1.9, 1]}>
          <spriteMaterial ref={glow} map={getGlowTexture()} transparent opacity={0.3} depthWrite={false} blending={THREE.AdditiveBlending} color="#8b98a5" />
        </sprite>
        <primitive object={label} position={[0, 1.45, 0]} />
        <mesh ref={ring} rotation-x={-Math.PI / 2} position={[0, 0.04, 0]} visible={false}>
          <ringGeometry args={[0.55, 0.72, 40]} />
          <meshBasicMaterial color="#e6edf3" transparent opacity={0.9} depthWrite={false} />
        </mesh>
      </group>
      <primitive object={line} />
    </>
  );
}

function RobotLayer({ map }: { map: Mapper }) {
  const ids = useTwinStore((s) => s.robotIds);
  return (
    <group>
      {ids.map((id) => (
        <RobotEntity key={id} id={id} map={map} />
      ))}
    </group>
  );
}

// ------------------------------------------------------------------------------------------------
// camera
// ------------------------------------------------------------------------------------------------

function CameraRig({ width, height }: { width: number; height: number }) {
  const { camera, controls } = useThree();
  const fitNonce = useTwinStore((s) => s.fitNonce);
  useEffect(() => {
    const persp = camera as THREE.PerspectiveCamera;
    const fov = (persp.fov * Math.PI) / 180;
    const aspect = persp.aspect || 1.6;
    const dist = Math.max(height / 2 / Math.tan(fov / 2), width / 2 / Math.tan(fov / 2) / aspect) * 1.12;
    persp.position.set(0, dist * 0.82, dist * 0.62);
    persp.near = 0.5;
    persp.far = dist * 8;
    persp.updateProjectionMatrix();
    persp.lookAt(0, 0, 0);
    const ctl = controls as OrbitControlsImpl | null;
    if (ctl) {
      ctl.target.set(0, 0, 0);
      ctl.update();
    }
  }, [camera, controls, width, height, fitNonce]);
  return null;
}

// ------------------------------------------------------------------------------------------------
// root
// ------------------------------------------------------------------------------------------------

export default function TwinView3D() {
  const world = useTwinStore((s) => s.world);
  const zones = useTwinStore(useShallow((s) => s.world?.zones ?? []));
  const select = useTwinStore((s) => s.selectRobot);
  const grid = world?.grid;
  const map = useMemo(() => makeMapper(grid?.width ?? 1, grid?.height ?? 1), [grid?.width, grid?.height]);
  if (!world || !grid) return null;
  return (
    <Canvas
      dpr={[1, 1.6]}
      camera={{ fov: 42, near: 0.5, far: 1000, position: [0, 60, 50] }}
      gl={{ antialias: true, powerPreference: "high-performance", alpha: false }}
      onPointerMissed={() => select(null)}
      style={{ background: "#0a0d12" }}
    >
      <color attach="background" args={["#0a0d12"]} />
      <fog attach="fog" args={["#0a0d12", 140, 420]} />
      <ambientLight intensity={0.85} />
      <directionalLight position={[40, 80, 30]} intensity={1.3} />
      <directionalLight position={[-40, 40, -30]} intensity={0.4} color="#22d3ee" />
      <StaticWorld world={world} map={map} />
      <ZoneLayer zones={zones} map={map} />
      <RobotLayer map={map} />
      <CameraRig width={grid.width} height={grid.height} />
      <OrbitControls makeDefault enableDamping dampingFactor={0.08} maxPolarAngle={Math.PI / 2.08} minDistance={6} maxDistance={400} />
    </Canvas>
  );
}
