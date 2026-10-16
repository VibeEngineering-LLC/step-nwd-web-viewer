"""Сводка GLB по деталям: имя узла-детали -> габарит мм. Usage: glb_parts.py <glb> [depth=1]"""
import sys, collections
import numpy as np, trimesh

sys.stdout.reconfigure(encoding="utf-8")
s = trimesh.load(sys.argv[1]); depth = int(sys.argv[2]) if len(sys.argv) > 2 else 1
import networkx as nx
G = s.graph.to_networkx(); root = s.graph.base_frame; parts = collections.OrderedDict()
for n in s.graph.nodes_geometry:
    path = nx.shortest_path(G, root, n)
    key = path[min(depth, len(path) - 1)]
    T, g = s.graph[n]; v = trimesh.transformations.transform_points(s.geometry[g].vertices, T)
    parts.setdefault(key, []).append(v)
print(f"meshes {len(s.graph.nodes_geometry)}, part groups {len(parts)}, total bbox mm", np.round((s.bounds[1] - s.bounds[0]) * 1000, 1))
for k, vs in parts.items():
    v = np.vstack(vs); print(f"  {k!r}: {np.round((v.max(0) - v.min(0)) * 1000, 1)}")
