// i18n casera: contexto de idioma + hook useT(). El idioma se persiste en
// localStorage (conveniencia por-visor, envuelto en try/catch). `t(key)` cae a
// `es` y luego a la propia clave si falta la traducción.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { DICTS, LANGS, type Lang } from "./translations";

const STORAGE_KEY = "wolf.lang";

function readLang(): Lang {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v && (LANGS as string[]).includes(v)) return v as Lang;
  } catch {
    /* localStorage no disponible: usa el default */
  }
  return "es";
}

export type TFn = (key: string, params?: Record<string, string | number>) => string;

interface I18nCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: TFn;
}

/** Sustituye {nombre} por params.nombre; sin params devuelve el texto tal cual. */
function interpolate(text: string, params?: Record<string, string | number>): string {
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (m, k: string) => (k in params ? String(params[k]) : m));
}

/** Traducción fuera de componentes React (p. ej. la capa HTTP): lee el idioma persistido. */
export function tNow(key: string, params?: Record<string, string | number>): string {
  const lang = readLang();
  return interpolate(DICTS[lang][key] ?? DICTS.es[key] ?? key, params);
}

const Ctx = createContext<I18nCtx | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(readLang);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      /* ignore */
    }
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((l: Lang) => setLangState(l), []);

  const t = useCallback<TFn>(
    (key, params) => interpolate(DICTS[lang][key] ?? DICTS.es[key] ?? key, params),
    [lang],
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18nCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useI18n fuera de I18nProvider");
  return ctx;
}

/** Atajo: solo la función de traducción. */
export function useT(): TFn {
  return useI18n().t;
}
