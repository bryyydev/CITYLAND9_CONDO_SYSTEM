// Data source for `npm run build` / `npm run dev`: the local Flask server. Chosen in vite.config.ts.
import type { Persona } from "./personas";
import type { Role } from "./types";

export { liveApi as dataService } from "./liveApi";
export const setMockRole = (_role: Role | null) => undefined;
export const PERSONAS: Record<Role, Persona> | null = null;
