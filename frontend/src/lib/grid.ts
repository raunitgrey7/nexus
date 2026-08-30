import type { Cell, GridModel, WorldSnapshot, ZoneModel } from "./types";

export const WALKABLE = new Set(["0", "3", "4", "6"]);

export function cellType(grid: GridModel, x: number, y: number): string {
  const row = grid.rows[y];
  return row ? (row[x] ?? "2") : "2";
}

export function cellKey(x: number, y: number): number {
  return y * 4096 + x;
}

export function keyToCell(k: number): Cell {
  return [k % 4096, Math.floor(k / 4096)];
}

/**
 * Zone membership per cell, replicating the layout generator's assignment order: earlier zones are
 * overwritten by later ones, except vertical corridors which only fill cells still unassigned (or in
 * the charging bay).
 */
export function buildZoneIndex(zones: ZoneModel[], grid: GridModel): (string | null)[] {
  const index: (string | null)[] = new Array<string | null>(grid.width * grid.height).fill(null);
  for (const z of zones) {
    const vertical = z.kind === "corridor" && z.y1 - z.y0 > z.x1 - z.x0;
    for (let y = z.y0; y <= z.y1; y++) {
      for (let x = z.x0; x <= z.x1; x++) {
        if (x < 0 || y < 0 || x >= grid.width || y >= grid.height) continue;
        const i = y * grid.width + x;
        if (vertical) {
          const cur = index[i];
          if (cur === null || cur === "CHG") index[i] = z.id;
        } else {
          index[i] = z.id;
        }
      }
    }
  }
  return index;
}

export function zoneOf(index: (string | null)[], width: number, x: number, y: number): string | null {
  return index[y * width + x] ?? null;
}

export function walkableCells(grid: GridModel): Cell[] {
  const blocked = new Set(grid.blocked.map(([x, y]) => cellKey(x, y)));
  const out: Cell[] = [];
  for (let y = 0; y < grid.height; y++) {
    const row = grid.rows[y] ?? "";
    for (let x = 0; x < grid.width; x++) {
      if (WALKABLE.has(row[x] ?? "2") && !blocked.has(cellKey(x, y))) out.push([x, y]);
    }
  }
  return out;
}

export function isWalkable(grid: GridModel, blocked: Set<number>, closed: Set<string>, zoneIdx: (string | null)[], x: number, y: number): boolean {
  if (x < 0 || y < 0 || x >= grid.width || y >= grid.height) return false;
  if (!WALKABLE.has(cellType(grid, x, y))) return false;
  if (blocked.has(cellKey(x, y))) return false;
  const z = zoneIdx[y * grid.width + x];
  return !(z !== null && closed.has(z));
}

/** Breadth-first shortest path on the 4-neighbourhood. Returns cells excluding the start. */
export function bfsPath(
  grid: GridModel,
  blocked: Set<number>,
  closed: Set<string>,
  zoneIdx: (string | null)[],
  from: Cell,
  to: Cell,
  maxNodes = 20000,
): Cell[] | null {
  const start = cellKey(from[0], from[1]);
  const goal = cellKey(to[0], to[1]);
  if (start === goal) return [];
  const prev = new Map<number, number>();
  prev.set(start, -1);
  const queue: number[] = [start];
  let head = 0;
  let explored = 0;
  const dirs: Cell[] = [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
  ];
  while (head < queue.length && explored < maxNodes) {
    const cur = queue[head++];
    explored++;
    const [cx, cy] = keyToCell(cur);
    for (const [dx, dy] of dirs) {
      const nx = cx + dx;
      const ny = cy + dy;
      if (!isWalkable(grid, blocked, closed, zoneIdx, nx, ny)) continue;
      const nk = cellKey(nx, ny);
      if (prev.has(nk)) continue;
      prev.set(nk, cur);
      if (nk === goal) {
        const path: Cell[] = [];
        let k = nk;
        while (k !== start) {
          path.push(keyToCell(k));
          k = prev.get(k) ?? start;
        }
        return path.reverse();
      }
      queue.push(nk);
    }
  }
  return null;
}

export function zoneRatio(world: Pick<WorldSnapshot, "zones">, occupancy: Record<string, number>, zoneId: string): number {
  const z = world.zones.find((zz) => zz.id === zoneId);
  if (!z || z.capacity <= 0) return 0;
  return (occupancy[zoneId] ?? 0) / z.capacity;
}

export function zoneCenter(z: ZoneModel): Cell {
  return [(z.x0 + z.x1) / 2, (z.y0 + z.y1) / 2];
}
