from __future__ import annotations

import collections
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = Path(r"C:\Users\wngar\Documents\ComfyUI\user\default\workflows")
REPORT_PATH = ROOT / "backend" / "data" / "logs" / "LONECAT_FLUX_KLEIN_WORKFLOW_INSPECTION.json"

MATCH_PATTERNS = ("Lonecat*Flux*5.0.3.json", "*Flux*2D*Klein*5.0.3*.json")
RELEVANT_TERMS = (
    "unet",
    "clip",
    "vae",
    "sampler",
    "scheduler",
    "noise",
    "latent",
    "save",
    "preview",
    "seed",
    "primitive",
    "lora",
    "upscale",
    "detail",
    "ksampler",
    "flux",
    "model",
    "resolution",
    "face",
    "cache",
    "gpu",
    "sound",
    "bypass",
)


def find_workflow() -> Path:
    for pattern in MATCH_PATTERNS:
        matches = sorted(WORKFLOW_DIR.glob(pattern))
        if matches:
            return matches[0]
    raise FileNotFoundError(f"No Lonecat Flux/Klein workflow found in {WORKFLOW_DIR}")


def summarize_node(node: dict) -> dict:
    node_type = str(node.get("type") or "")
    title = str(node.get("title") or node.get("properties", {}).get("Node name for S&R") or "")
    widgets = node.get("widgets_values")
    return {
        "id": node.get("id"),
        "type": node_type,
        "title": title,
        "mode": node.get("mode"),
        "flags": node.get("flags") or {},
        "widgets_values": widgets,
    }


def main() -> int:
    workflow_path = find_workflow()
    data = json.loads(workflow_path.read_text(encoding="utf-8"))
    nodes = data.get("nodes") or []
    links = data.get("links") or []
    groups = data.get("groups") or []

    type_counts = collections.Counter(str(node.get("type") or "") for node in nodes)
    nodes_by_id = {node.get("id"): node for node in nodes}
    link_sources: dict[int, tuple[int, int]] = {}
    for link in links:
        if isinstance(link, list) and len(link) >= 6:
            link_sources[link[0]] = (link[1], link[2])

    def upstream_ids(node_id: int) -> set[int]:
        seen: set[int] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            node = nodes_by_id.get(current)
            if not node:
                continue
            for input_slot in node.get("inputs") or []:
                link_id = input_slot.get("link")
                if link_id is None:
                    continue
                source = link_sources.get(link_id)
                if source and source[0] not in seen:
                    stack.append(source[0])
        return seen

    output_nodes = [
        node
        for node in nodes
        if "save" in str(node.get("type") or "").lower()
        or "saver" in str(node.get("type") or "").lower()
    ]
    output_ancestry = []
    for node in output_nodes:
        ancestor_ids = upstream_ids(node.get("id"))
        ancestor_nodes = [summarize_node(nodes_by_id[node_id]) for node_id in sorted(ancestor_ids) if node_id in nodes_by_id]
        output_ancestry.append({
            "output": summarize_node(node),
            "ancestor_count": len(ancestor_nodes),
            "ancestor_types": collections.Counter(n["type"] for n in ancestor_nodes).most_common(),
            "ancestors": ancestor_nodes,
        })

    relevant_nodes = []
    bypassed_or_muted = []
    for node in nodes:
        summary = summarize_node(node)
        haystack = f"{summary['type']} {summary['title']}".lower()
        if any(term in haystack for term in RELEVANT_TERMS):
            relevant_nodes.append(summary)
        flags = summary["flags"] or {}
        if node.get("mode") or flags.get("bypassed") or flags.get("muted"):
            bypassed_or_muted.append(summary)

    report = {
        "workflow_path": str(workflow_path),
        "top_level_keys": list(data.keys()),
        "version": data.get("version"),
        "node_count": len(nodes),
        "link_count": len(links),
        "group_count": len(groups),
        "top_node_types": type_counts.most_common(60),
        "groups": [
            {
                "title": group.get("title"),
                "bounding": group.get("bounding"),
                "color": group.get("color"),
            }
            for group in groups
        ],
        "relevant_nodes": relevant_nodes,
        "bypassed_or_muted_nodes": bypassed_or_muted,
        "output_ancestry": output_ancestry,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "workflow_path": str(workflow_path),
        "report": str(REPORT_PATH),
        "node_count": len(nodes),
        "link_count": len(links),
        "group_count": len(groups),
        "relevant_nodes": len(relevant_nodes),
        "bypassed_or_muted_nodes": len(bypassed_or_muted),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
