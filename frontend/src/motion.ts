// Sistema de movimiento (wolf-motion-design). CSS-first: casi todo vive en
// styles.css con tokens --motion-* . Este módulo solo aporta lo que CSS no
// puede: el count-up numérico y el helper de stagger.
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Índice de stagger para CSS: el delay real sale de --i en styles.css y se
 *  acota (tope 12) para que la secuencia entera no pase de ~400 ms. */
export function stagger(i: number): CSSProperties {
  return { ["--i" as string]: Math.min(i, 12) } as CSSProperties;
}

const easeOutCubic = (t: number): number => 1 - Math.pow(1 - t, 3);

/**
 * Count-up de 0 → target. Con prefers-reduced-motion devuelve el valor final
 * de inmediato (la información no se retrasa). El valor final SIEMPRE es el
 * dato real: la animación nunca muestra un número que no sea el del backend
 * al terminar.
 */
export function useCountUp(target: number | null, duration = 700): number | null {
  const [value, setValue] = useState<number | null>(target);
  const shown = useRef<number>(0);

  useEffect(() => {
    if (target === null) {
      setValue(null);
      return;
    }
    if (prefersReducedMotion() || duration <= 0) {
      shown.current = target;
      setValue(target);
      return;
    }
    const from = shown.current;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const v = Math.round(from + (target - from) * easeOutCubic(t));
      shown.current = v;
      setValue(v);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);

  return value;
}
