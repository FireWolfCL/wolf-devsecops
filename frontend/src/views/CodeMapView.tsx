// Mapa de código: árbol de módulos del repo del proyecto (GitHub). MVP =
// árbol indentado reconstruido desde `nodes` (paths relativos con "/") con
// filtro por texto y tamaño por archivo. Si la reconstrucción falla se cae a
// lista plana (patrón de la casa). 404 = el proyecto no tiene GitHub: es un
// estado esperado, no un error roto. Todo es DATO: texto plano, sin HTML.
import { useEffect, useMemo, useState } from "react";

import { fetchCodemap } from "../api/client";
import { ApiError } from "../api/http";
import type { ModuleNode, ModuleTree } from "../api/types";
import { fmtDate } from "../components/util";
import { useT } from "../i18n";

const MAX_ROWS = 2000;

interface TNode {
  name: string;
  path: string;
  type: "file" | "dir";
  size: number | null; // en dirs: suma de archivos descendientes
  children: Map<string, TNode>;
}

function fmtSize(n: number | null): string {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Reconstruye la jerarquía; crea dirs implícitos si el backend solo manda archivos. */
function buildTree(nodes: ModuleNode[]): TNode {
  const root: TNode = { name: "", path: "", type: "dir", size: 0, children: new Map() };
  for (const n of nodes) {
    const parts = n.path.split("/").filter(Boolean);
    if (parts.length === 0) continue;
    let cur = root;
    parts.forEach((part, i) => {
      const last = i === parts.length - 1;
      const path = parts.slice(0, i + 1).join("/");
      let child = cur.children.get(part);
      if (!child) {
        child = { name: part, path, type: last ? n.type : "dir", size: null, children: new Map() };
        cur.children.set(part, child);
      }
      if (last) {
        child.type = n.type;
        if (n.type === "file") child.size = n.size;
      }
      cur = child;
    });
  }
  const total = (node: TNode): number => {
    if (node.type === "file") return node.size ?? 0;
    let s = 0;
    node.children.forEach((c) => (s += total(c)));
    node.size = s;
    return s;
  };
  total(root);
  return root;
}

interface Row {
  node: TNode;
  depth: number;
}

function sortedChildren(n: TNode): TNode[] {
  // Directorios primero, luego alfabético.
  return [...n.children.values()].sort(
    (a, b) => Number(b.type === "dir") - Number(a.type === "dir") || a.name.localeCompare(b.name),
  );
}

function flatten(root: TNode, collapsed: Set<string>, q: string): Row[] {
  const out: Row[] = [];
  const matches = (n: TNode): boolean => n.path.toLowerCase().includes(q);
  // Con filtro: se muestran coincidencias y sus ancestros; el colapso se ignora.
  const hasMatch = (n: TNode): boolean => matches(n) || [...n.children.values()].some(hasMatch);
  const walk = (n: TNode, depth: number) => {
    for (const c of sortedChildren(n)) {
      if (q && !hasMatch(c)) continue;
      out.push({ node: c, depth });
      if (c.type === "dir" && (q || !collapsed.has(c.path))) walk(c, depth + 1);
    }
  };
  walk(root, 0);
  return out;
}

export function CodeMapView({ projectKey, onBack }: { projectKey: string; onBack: () => void }) {
  const t = useT();
  const [tree, setTree] = useState<ModuleTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [noGithub, setNoGithub] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setNoGithub(false);
    fetchCodemap(projectKey)
      .then((d) => active && setTree(d))
      .catch((e: unknown) => {
        if (!active) return;
        if (e instanceof ApiError && e.status === 404) setNoGithub(true);
        else setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [projectKey, nonce]);

  const nodes = useMemo(() => tree?.nodes ?? [], [tree]);
  const built = useMemo<TNode | null>(() => {
    try {
      return buildTree(nodes);
    } catch {
      return null; // fallback a lista plana
    }
  }, [nodes]);

  const q = query.trim().toLowerCase();
  const rows = useMemo<Row[]>(() => {
    if (built) return flatten(built, collapsed, q);
    return nodes
      .filter((n) => !q || n.path.toLowerCase().includes(q))
      .sort((a, b) => a.path.localeCompare(b.path))
      .map((n) => ({
        node: { name: n.path, path: n.path, type: n.type, size: n.size, children: new Map() } as TNode,
        depth: 0,
      }));
  }, [built, nodes, collapsed, q]);

  const files = nodes.filter((n) => n.type === "file");
  const dirCount = built ? countDirs(built) : nodes.length - files.length;
  const totalSize = files.reduce((s, n) => s + (n.size ?? 0), 0);
  const shown = rows.slice(0, MAX_ROWS);

  const toggle = (path: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  const collapseAll = () => {
    if (!built) return;
    const all = new Set<string>();
    const walk = (n: TNode) => n.children.forEach((c) => c.type === "dir" && (all.add(c.path), walk(c)));
    walk(built);
    setCollapsed(all);
  };

  return (
    <div className="detail codemap-view">
      <button type="button" className="btn-back" onClick={onBack}>{t("common.backToProject")}</button>
      <div className="detail-title-row">
        <h2 className="detail-title">{t("codemap.title")}</h2>
      </div>
      <p className="muted">{t("codemap.subtitle", { name: projectKey })}</p>

      {loading && !tree && <p className="state" aria-busy="true">{t("codemap.loadingAria")}…</p>}
      {noGithub && (
        <div className="alert info" role="status">
          <strong>{t("codemap.noGithub")}</strong>
          <div className="muted">{t("codemap.noGithubHint")}</div>
        </div>
      )}
      {error && (
        <div className="alert err" role="alert">
          {t("codemap.loadError", { key: projectKey, error })}{" "}
          <button type="button" className="btn sm" onClick={() => setNonce((n) => n + 1)}>{t("common.retry")}</button>
        </div>
      )}

      {tree && (
        <>
          {tree.truncated && <div className="alert warn" role="status">{t("codemap.truncated")}</div>}
          {!built && <div className="alert info" role="status">{t("codemap.flatFallback")}</div>}
          <p className="muted mono deps-meta">{t("deps.generated", { date: fmtDate(tree.generated_at) })}</p>

          <div className="list-bar">
            <p className="mono muted">
              {t("codemap.summary", { files: files.length, dirs: dirCount, size: fmtSize(totalSize) })}
            </p>
            <div className="list-tools">
              {built && !q && (
                <>
                  <button type="button" className="btn sm" onClick={() => setCollapsed(new Set())}>{t("codemap.expandAll")}</button>
                  <button type="button" className="btn sm" onClick={collapseAll}>{t("codemap.collapseAll")}</button>
                </>
              )}
              <input
                type="search"
                className="search"
                placeholder={t("codemap.filter")}
                aria-label={t("codemap.filter")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          </div>

          {nodes.length === 0 ? (
            <p className="empty">{t("codemap.empty")}</p>
          ) : rows.length === 0 ? (
            <p className="empty">{t("codemap.noMatch")}</p>
          ) : (
            <ul className="cmap-tree" aria-label={t("codemap.treeAria")}>
              {shown.map(({ node, depth }) => {
                const isDir = node.type === "dir";
                const open = !collapsed.has(node.path) || !!q;
                return (
                  <li key={node.path} className={`cmap-row ${isDir ? "dir" : "file"}`} style={{ paddingLeft: depth * 18 }}>
                    {isDir && built ? (
                      <button
                        type="button"
                        className="cmap-toggle"
                        aria-expanded={open}
                        disabled={!!q}
                        onClick={() => toggle(node.path)}
                      >
                        <span aria-hidden="true">{open ? "▾" : "▸"}</span> {node.name}/
                      </button>
                    ) : (
                      <span className="cmap-name mono">
                        <span aria-hidden="true" className="cmap-ico">{isDir ? "▸" : "·"}</span> {node.name}
                        <span className="sr-only"> ({isDir ? t("codemap.dir") : t("codemap.file")})</span>
                      </span>
                    )}
                    <span className="cmap-size mono muted">{fmtSize(node.size)}</span>
                  </li>
                );
              })}
            </ul>
          )}
          {rows.length > MAX_ROWS && <p className="muted">{t("codemap.capped", { n: MAX_ROWS })}</p>}
        </>
      )}
    </div>
  );
}

function countDirs(n: TNode): number {
  let c = 0;
  n.children.forEach((ch) => {
    if (ch.type === "dir") c += 1 + countDirs(ch);
  });
  return c;
}
