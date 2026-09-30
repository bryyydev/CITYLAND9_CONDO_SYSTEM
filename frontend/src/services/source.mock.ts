// Data source for `npm run dev:mock` only: in-memory prototype data. Chosen in vite.config.ts,
// so no production build can contain it.
export { mockApi as dataService, setMockRole } from "./mockApi";
export { PERSONAS } from "./personas";
