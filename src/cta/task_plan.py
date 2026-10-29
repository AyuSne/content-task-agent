"""Durable, task-scoped plans proposed by the main agent."""

import json

from cta.config import utc_now_iso
from cta.repository import plan_view


def update_plan(conn, task_id: str, parsed: dict) -> dict:
    def invalid(message):
        return {"ok": False, "error": "invalid_plan", "message": message}

    steps = parsed.get("steps")
    if set(parsed) != {"steps"} or not isinstance(steps, list) or not steps:
        return invalid("steps 必须是非空数组。")
    ids = set()
    for index, step in enumerate(steps):
        location = f"steps[{index}]"
        if not isinstance(step, dict) or set(step) != {"id", "title", "status", "depends_on", "references"}:
            return invalid(f"{location} 必须包含 id、title、status、depends_on、references。")
        identifier = step["id"]
        if not isinstance(identifier, str) or not identifier.strip() or identifier in ids:
            return invalid(f"{location}.id 必须非空且唯一。")
        if not isinstance(step["title"], str) or not step["title"].strip():
            return invalid(f"{location}.title 必须非空。")
        if step["status"] not in ("pending", "current", "done"):
            return invalid(f"{location}.status 无效。")
        if not isinstance(step["depends_on"], list) or not all(isinstance(dep, str) for dep in step["depends_on"]):
            return invalid(f"{location}.depends_on 必须是步骤 ID 数组。")
        if not isinstance(step["references"], list):
            return invalid(f"{location}.references 必须是数组。")
        ids.add(identifier)
    queries = {
        "brief": "SELECT 1 FROM briefs WHERE task_id = ? AND CAST(version AS TEXT) = ?",
        "output": "SELECT 1 FROM outputs WHERE task_id = ? AND id = ?",
        "review": "SELECT 1 FROM reviews WHERE task_id = ? AND id = ?",
        "tool_result": "SELECT 1 FROM messages WHERE task_id = ? AND agent = 'main' AND role = 'tool' AND tool_call_id = ?",
    }
    graph = {}
    for index, step in enumerate(steps):
        location = f"steps[{index}]"
        deps = step["depends_on"]
        if len(deps) != len(set(deps)) or any(dep not in ids for dep in deps):
            return invalid(f"{location}.depends_on 包含重复或不存在的步骤。")
        graph[step["id"]] = deps
        for ref_index, ref in enumerate(step["references"]):
            if (not isinstance(ref, dict) or set(ref) != {"kind", "id"}
                    or not isinstance(ref["kind"], str) or ref["kind"] not in queries
                    or not isinstance(ref["id"], str) or not ref["id"].strip()):
                return invalid(f"{location}.references[{ref_index}] 必须包含有效的 kind 和 id。")
            if conn.execute(queries[ref["kind"]], (task_id, ref["id"])).fetchone() is None:
                return invalid(f"{location}.references[{ref_index}] 不存在于本任务：{ref['kind']}:{ref['id']}。")
    visiting, visited = set(), set()

    def cycle(identifier):
        if identifier in visiting:
            return True
        if identifier in visited:
            return False
        visiting.add(identifier)
        if any(cycle(dep) for dep in graph[identifier]):
            return True
        visiting.remove(identifier)
        visited.add(identifier)
        return False

    if any(cycle(identifier) for identifier in graph):
        return invalid("步骤依赖不得形成循环。")
    previous = plan_view(conn, task_id)
    if previous["steps"] == steps:
        return {"ok": True, "plan": previous, "changed": False}
    version = previous["version"] + 1
    created_at = utc_now_iso()
    conn.execute("INSERT INTO task_plans (task_id, version, steps_json, created_at) VALUES (?, ?, ?, ?)",
                 (task_id, version, json.dumps(steps, ensure_ascii=False), created_at))
    return {"ok": True, "plan": {"version": version, "steps": steps, "created_at": created_at}, "changed": True}
