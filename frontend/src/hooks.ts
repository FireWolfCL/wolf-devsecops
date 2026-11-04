// Hooks de datos. Sin librería externa: fetch + estado, suficiente para el MVP.
import { useCallback, useEffect, useState } from "react";

import { fetchMeta, fetchPosture, fetchProject, fetchProjects, fetchReport, fetchSpark, fetchTool } from "./api/client";
import type { Meta, ToolName } from "./api/client";
import type { PostureOverview, PostureSparklines, ProjectListItem, ProjectReport, ProjectSummary, ToolDashboard } from "./api/types";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(() => {
    let active = true;
    setLoading(true);
    setError(null);
    loader()
      .then((d) => active && setData(d))
      .catch((e: unknown) => active && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => run(), [run]);
  return { data, loading, error, reload: run };
}

export function useProjects(): AsyncState<ProjectListItem[]> {
  return useAsync(fetchProjects, []);
}

export function useProjectSummary(key: string): AsyncState<ProjectSummary> {
  return useAsync(() => fetchProject(key), [key]);
}

export function useReport(key: string): AsyncState<ProjectReport> {
  return useAsync(() => fetchReport(key), [key]);
}

export function useMeta(): AsyncState<Meta> {
  return useAsync(fetchMeta, []);
}

/** ¿La feature premium (edición) está activa? El backend es la autoridad; esto
 *  es para mostrar/ocultar y poner "Pro"/"Ver planes" en la UI. */
export function useFeature(feature: string): boolean {
  const { data } = useMeta();
  return data?.features?.includes(feature) ?? false;
}

export function usePosture(): AsyncState<PostureOverview> {
  return useAsync(fetchPosture, []);
}

export function useTool(tool: ToolName): AsyncState<ToolDashboard> {
  return useAsync(() => fetchTool(tool), [tool]);
}

/** Sparklines de tendencia para las tarjetas del dashboard. Es decorativo: si
 *  falla no rompe la lista, solo no se dibuja la mini-línea. */
export function useSpark(days?: number): AsyncState<PostureSparklines> {
  return useAsync(() => fetchSpark(days), [days]);
}
