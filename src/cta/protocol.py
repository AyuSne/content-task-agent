"""Identify the frozen model contract and the handler that can execute it."""

import json

from cta.prompts import TOOL_DEFINITIONS

# Bump when handler semantics change, even if the JSON schema stays the same.
HANDLER_VERSION = "direct-script-recovery-v1"


def _without_descriptions(value):
    if isinstance(value, dict):
        return {key: _without_descriptions(item) for key, item in value.items() if key != "description"}
    if isinstance(value, list):
        return [_without_descriptions(item) for item in value]
    return value


def _contract(tools):
    return sorted((_without_descriptions(tool) for tool in tools), key=lambda tool: tool["function"]["name"])


def frozen_protocol(conn, task_id):
    for row in conn.execute("SELECT meta_json FROM messages WHERE task_id = ? AND agent = 'main' ORDER BY seq", (task_id,)):
        meta = json.loads(row["meta_json"])
        if "tool_protocol" in meta:
            return meta["tool_protocol"]
        if meta.get("kind") == "system":
            return {"prompt_version": meta.get("prompt_version"), "tools": [], "handler_version": None}
    return None


def incompatibility(conn, task_id):
    protocol = frozen_protocol(conn, task_id)
    if protocol is None:
        return None
    if protocol.get("handler_version") == HANDLER_VERSION and _contract(protocol["tools"]) == _contract(TOOL_DEFINITIONS):
        return None
    version = protocol.get("prompt_version")
    handler = protocol.get("handler_version")
    if handler:
        return {"error": "incompatible_protocol", "message":
                f"任务采用处理版本 {handler}，与当前程序不兼容；请使用 git log --all -S {handler} -- src/cta/protocol.py 查找其固定代码提交并用原采用资料恢复。"}
    names = {tool["function"]["name"] for tool in protocol.get("tools", [])}
    if version == "main-2026-10-30.dispositions-v1":
        recovery = "c5df5f9"
    elif version == "main-2026-10-30.joint-brief-v1":
        recovery = "822ce68"
    elif version == "main-2026-10-30.direct-script-v1":
        recovery = "bfa35bf" if "submit_campaign" in names else "1cd7340"
    else:
        recovery = "与原采用处理版本对应的代码提交"
    return {"error": "incompatible_protocol", "message":
            f"任务冻结的工具或处理语义不适用于当前程序；请使用 {recovery} 和原采用资料恢复。"}
