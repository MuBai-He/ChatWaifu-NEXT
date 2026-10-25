import { useEffect, useRef } from "react";
import cytoscape, { type Core } from "cytoscape";
import { predicateLabels, type KnowledgeGraph } from "./knowledgeClient";

export function KnowledgeCanvas({
  graph,
  onSelect,
}: {
  graph: KnowledgeGraph;
  onSelect: (id: string, edge: boolean) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const core = useRef<Core | null>(null);
  const select = useRef(onSelect);
  const view = useRef<{
    signature: string;
    zoom: number;
    pan: cytoscape.Position;
    positions: Map<string, cytoscape.Position>;
  } | null>(null);
  useEffect(() => {
    select.current = onSelect;
  }, [onSelect]);
  useEffect(() => {
    if (!host.current) return;
    const signature = graph.nodes
      .map((n) => n.node_id)
      .sort()
      .join("|");
    const previous =
      view.current?.signature === signature ? view.current : null;
    const cy = cytoscape({
      container: host.current,
      minZoom: 0.15,
      maxZoom: 3,
      boxSelectionEnabled: false,
      elements: [
        ...graph.nodes.map((n) => ({
          data: {
            id: n.node_id,
            label:
              Array.from(n.label).length > 32
                ? Array.from(n.label).slice(0, 32).join("") + "…"
                : n.label,
            kind: n.kind,
          },
          position: previous?.positions.get(n.node_id),
        })),
        ...graph.edges.map((e) => ({
          data: {
            id: e.relation_id,
            source: e.source,
            target: e.target,
            label: predicateLabels[e.predicate] ?? e.predicate,
            status: e.change_proposed ? "conflict" : e.status,
          },
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#8da5c8",
            label: "data(label)",
            color: "#edf3fc",
            "font-size": 12,
            "text-wrap": "wrap",
            "text-max-width": "110px",
            "text-valign": "bottom",
            "text-margin-y": 8,
            width: 32,
            height: 32,
            "border-width": 2,
            "border-color": "#d0dff2",
          },
        },
        {
          selector: 'node[kind = "person"]',
          style: { "background-color": "#b999df" },
        },
        {
          selector: 'node[kind = "project"]',
          style: {
            "background-color": "#55c9ae",
            shape: "round-rectangle",
            width: 42,
          },
        },
        {
          selector: 'node[kind = "todo"], node[kind = "event"]',
          style: { "background-color": "#efbe74", shape: "diamond" },
        },
        {
          selector: "edge",
          style: {
            width: 1.6,
            "line-color": "#687f9d",
            "target-arrow-color": "#687f9d",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": 10,
            color: "#a3b7d0",
            "text-rotation": "autorotate",
            "text-background-color": "#111f32",
            "text-background-opacity": 0.85,
            "text-background-padding": "3px",
          },
        },
        {
          selector: 'edge[status = "proposed"], edge[status = "superseded"]',
          style: { "line-style": "dashed" },
        },
        {
          selector: 'edge[status = "conflict"]',
          style: {
            "line-style": "dashed",
            "line-color": "#efbe74",
            "target-arrow-color": "#efbe74",
          },
        },
        {
          selector: ":selected",
          style: {
            "border-color": "#fff",
            "line-color": "#fff",
            "target-arrow-color": "#fff",
          },
        },
      ],
      layout: previous
        ? { name: "preset", fit: false }
        : {
            name: "cose",
            animate: false,
            nodeRepulsion: () => 8000,
            idealEdgeLength: () => 110,
            padding: 48,
          },
    });
    if (previous) {
      cy.zoom(previous.zoom);
      cy.pan(previous.pan);
    }
    core.current = cy;
    cy.on("tap", "node, edge", (event) => {
      const element = event.target as cytoscape.SingularElementReturnValue;
      select.current(element.id(), element.isEdge());
    });
    const resize = new ResizeObserver(() => cy.resize());
    resize.observe(host.current);
    return () => {
      resize.disconnect();
      const positions = new Map<string, cytoscape.Position>();
      cy.nodes().forEach((n) => {
        positions.set(n.id(), { ...n.position() });
      });
      view.current = {
        signature,
        zoom: cy.zoom(),
        pan: { ...cy.pan() },
        positions,
      };
      cy.destroy();
      core.current = null;
    };
  }, [graph]);
  return (
    <div className="km-canvas-wrap">
      <div
        className="km-canvas"
        ref={host}
        role="img"
        aria-label={`知识关系图，${graph.nodes.length} 个节点。也可使用下方节点列表操作。`}
      />
      <div className="km-zoom" aria-label="关系图视图操作">
        <button
          type="button"
          onClick={() => core.current?.zoom((core.current?.zoom() ?? 1) * 1.3)}
          aria-label="放大"
        >
          ＋
        </button>
        <button
          type="button"
          onClick={() => core.current?.zoom((core.current?.zoom() ?? 1) / 1.3)}
          aria-label="缩小"
        >
          −
        </button>
        <button type="button" onClick={() => core.current?.fit(undefined, 48)}>
          适应画布
        </button>
      </div>
      {!graph.nodes.length && (
        <div className="km-empty">
          还没有可显示的关系
          <br />
          <small>同步资料后建立索引，或调整搜索和筛选。</small>
        </div>
      )}
    </div>
  );
}
