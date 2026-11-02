// Token de sesión en localStorage (patrón WIF, ADR-013). Todo acceso va en
// try/catch: modo privado o storage bloqueado no debe romper la app.
const KEY = "wolf_devsecops_token";

export const tokenStorage = {
  get(): string | null {
    try {
      return window.localStorage.getItem(KEY);
    } catch {
      return null;
    }
  },
  set(token: string): void {
    try {
      window.localStorage.setItem(KEY, token);
    } catch {
      /* sin storage: la sesión vive solo mientras dure el estado en memoria */
    }
  },
  clear(): void {
    try {
      window.localStorage.removeItem(KEY);
    } catch {
      /* nada que limpiar */
    }
  },
};
