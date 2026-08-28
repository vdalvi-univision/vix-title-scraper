"""Pretty-print saved module inventories."""

from __future__ import annotations

import json
from pathlib import Path

for name in ("ondemandplus", "ondemandpluswc"):
    path = Path(f"output/layout_compare/{name}_modules_inventory.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    print("====", name, "total", data.get("totalCount"), "platform", data.get("platform"))
    for i, mod in enumerate(data.get("modules") or [], 1):
        print(
            f"{i:02d} {mod.get('__typename', ''):32} "
            f"{(mod.get('moduleType') or '-'):28} {mod.get('title')}"
        )
    print()
