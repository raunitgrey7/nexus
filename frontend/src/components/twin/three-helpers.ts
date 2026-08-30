import * as THREE from "three";

/** world cell → three.js coordinates (world centred at the origin, y grows "north" = -z). */
export function makeMapper(width: number, height: number) {
  const hx = width / 2;
  const hz = height / 2;
  return {
    x: (cx: number) => cx + 0.5 - hx,
    z: (cy: number) => -(cy + 0.5 - hz),
  };
}

export type Mapper = ReturnType<typeof makeMapper>;

interface TextOptions {
  color?: string;
  bg?: string | null;
  size?: number;
  weight?: number;
  width?: number;
  height?: number;
  letterSpacing?: number;
}

export function makeTextTexture(text: string, opts: TextOptions = {}): THREE.CanvasTexture {
  const dpr = 2;
  const width = opts.width ?? 256;
  const height = opts.height ?? 96;
  const canvas = document.createElement("canvas");
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.scale(dpr, dpr);
    if (opts.bg) {
      ctx.fillStyle = opts.bg;
      const r = 8;
      ctx.beginPath();
      ctx.roundRect(0, 0, width, height, r);
      ctx.fill();
    }
    ctx.fillStyle = opts.color ?? "#e6edf3";
    ctx.font = `${opts.weight ?? 600} ${opts.size ?? 40}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    if (opts.letterSpacing) ctx.letterSpacing = `${opts.letterSpacing}px`;
    ctx.fillText(text, width / 2, height / 2 + 1);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  return tex;
}

export function makeLabelSprite(text: string, color: string): THREE.Sprite {
  const tex = makeTextTexture(text, { color, bg: "rgba(10,13,18,0.78)", size: 44, width: 160, height: 64 });
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false, depthWrite: false });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(1.5, 0.6, 1);
  sprite.renderOrder = 10;
  return sprite;
}

let glowTex: THREE.Texture | null = null;
export function getGlowTexture(): THREE.Texture {
  if (glowTex) return glowTex;
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    g.addColorStop(0, "rgba(255,255,255,0.9)");
    g.addColorStop(0.35, "rgba(255,255,255,0.35)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
  }
  glowTex = new THREE.CanvasTexture(canvas);
  glowTex.colorSpace = THREE.SRGBColorSpace;
  return glowTex;
}

let hatchTex: THREE.Texture | null = null;
export function getHatchTexture(): THREE.Texture {
  if (hatchTex) return hatchTex;
  const size = 32;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.clearRect(0, 0, size, size);
    ctx.strokeStyle = "rgba(239,68,68,0.85)";
    ctx.lineWidth = 4;
    for (let i = -size; i < size * 2; i += 12) {
      ctx.beginPath();
      ctx.moveTo(i, 0);
      ctx.lineTo(i + size, size);
      ctx.stroke();
    }
  }
  hatchTex = new THREE.CanvasTexture(canvas);
  hatchTex.wrapS = THREE.RepeatWrapping;
  hatchTex.wrapT = THREE.RepeatWrapping;
  hatchTex.colorSpace = THREE.SRGBColorSpace;
  return hatchTex;
}

export function disposeObject(obj: THREE.Object3D): void {
  obj.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (mesh.geometry) mesh.geometry.dispose();
    const mat = (o as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
    else mat?.dispose();
    const sprite = o as THREE.Sprite;
    if (sprite.material && (sprite.material as THREE.SpriteMaterial).map) {
      (sprite.material as THREE.SpriteMaterial).map?.dispose();
    }
  });
}
