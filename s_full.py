#!/usr/bin/env python3
# Harness: full capstone -- combines all mechanisms s01-s11.
"""
s_full.py - Complete Reference Harness (Capstone)

Combines all mechanisms from sessions s01-s11:
  - s02: Base tools (bash, read_file, write_file, edit_file)
  - s03: Todos (create, complete, list, nag)
  - s04: Subagent spawning (spawn, status, kill)
  - s05: Skill modules (load, call, cache)
  - s06: Conversation compression (microcompact, auto-compact)
  - s07: File-based tasks (read, write, edit with validation)
  - s08: Background jobs (start, status, cancel)
  - s09: Messaging (send, receive, broadcast)
  - s10: Team protocols (shutdown, plan approval)
  - s11: Team management (spawn, task claiming, idle auto-claim)

Note: s12 (worktree isolation) is kept separate by design.
"""

import os
import sys
import json
import time
import uuid
import shutil
import hashlib
import subprocess
from datetime import datetime
from typing import Optional, List, Dict, Any

# ─── Configuration ───────────────────────────────────────────────────────────

COMPACT_THRESHOLD = int(os.environ.get("COMPACT_THRESHOLD", 100000))  # tokens
TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", ".transcripts")
AUTO_COMPACT_ENABLED = os.environ.get("AUTO_COMPACT", "true").lower() == "true"

# ─── s02: Base Tools ─────────────────────────────────────────────────────────

def bash(command: str, timeout: int = 60) -> dict:
    """Execute a shell command with timeout."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "TIMEOUT", "exit_code": -1, "success": False}

def read_file(path: str, limit: Optional[int] = None) -> str:
    """Read file contents, optionally limited."""
    try:
        with open(path, 'r') as f:
            if limit:
                return f.read(limit)
            return f.read()
    except Exception as e:
        return f"ERROR: {e}"

def write_file(path: str, content: str) -> dict:
    """Write content to file, creating directories as needed."""
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)
        return {"success": True, "path": path, "bytes": len(content)}
    except Exception as e:
        return {"success": False, "error": str(e)}

def edit_file(path: str, old_text: str, new_text: str) -> dict:
    """Replace old_text with new_text in file (first occurrence)."""
    try:
        with open(path, 'r') as f:
            content = f.read()
        if old_text not in content:
            return {"success": False, "error": "old_text not found"}
        content = content.replace(old_text, new_text, 1)
        with open(path, 'w') as f:
            f.write(content)
        return {"success": True, "path": path}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ─── s03: Todo System ────────────────────────────────────────────────────────

TODOS = {}  # todo_id -> {text, done, created_at, completed_at}
LAST_TODO_ROUND = 0

def todo_create(text: str) -> dict:
    """Create a new todo item."""
    todo_id = f"todo_{uuid.uuid4().hex[:12]}"
    TODOS[todo_id] = {
        "id": todo_id,
        "text": text,
        "done": False,
        "created_at": datetime.utcnow().isoformat(),
        "completed_at": None
    }
    return {"success": True, "todo_id": todo_id}

def todo_complete(todo_id: str) -> dict:
    """Mark a todo as completed."""
    if todo_id not in TODOS:
        return {"success": False, "error": "Todo not found"}
    TODOS[todo_id]["done"] = True
    TODOS[todo_id]["completed_at"] = datetime.utcnow().isoformat()
    return {"success": True, "todo_id": todo_id}

def todo_list() -> list:
    """List all todos."""
    return list(TODOS.values())

def todo_nag(current_round: int) -> Optional[str]:
    """Nag about pending todos if 3+ rounds without TodoWrite."""
    global LAST_TODO_ROUND
    pending = [t for t in TODOS.values() if not t["done"]]
    if not pending:
        return None
    if current_round - LAST_TODO_ROUND >= 3:
        todo_texts = "\n".join(f"  - {t['text']}" for t in pending)
        return f"\n[TODO NAG] {len(pending)} pending todos:\n{todo_texts}\n"
    return None

def todo_update_round(round_num: int):
    """Update last todo round tracker."""
    global LAST_TODO_ROUND
    LAST_TODO_ROUND = round_num

# ─── s04: Subagent Spawning ──────────────────────────────────────────────────

SUBAGENTS = {}  # agent_id -> {pid, status, role, created_at}

def spawn_subagent(role: str, system_prompt: str) -> dict:
    """Spawn a subagent process."""
    agent_id = f"agent_{role}_{uuid.uuid4().hex[:8]}"
    # Simulated PID for harness
    pid = int(hashlib.md5(agent_id.encode()).hexdigest()[:8], 16)

    SUBAGENTS[agent_id] = {
        "id": agent_id,
        "role": role,
        "pid": pid,
        "status": "running",
        "system_prompt_hash": hashlib.sha256(system_prompt.encode()).hexdigest()[:16],
        "created_at": datetime.utcnow().isoformat()
    }
    return {"success": True, "agent_id": agent_id, "pid": pid}

def subagent_status(agent_id: str) -> dict:
    """Get subagent status."""
    if agent_id not in SUBAGENTS:
        return {"success": False, "error": "Agent not found"}
    return {"success": True, **SUBAGENTS[agent_id]}

def kill_subagent(agent_id: str) -> dict:
    """Kill a subagent."""
    if agent_id not in SUBAGENTS:
        return {"success": False, "error": "Agent not found"}
    SUBAGENTS[agent_id]["status"] = "terminated"
    return {"success": True, "agent_id": agent_id}

# ─── s05: Skill Modules ──────────────────────────────────────────────────────

SKILLS = {}  # skill_name -> {code, signature, cache}

def load_skill(name: str, code: str, signature: dict) -> dict:
    """Load a skill module."""
    SKILLS[name] = {
        "name": name,
        "code": code,
        "signature": signature,
        "loaded_at": datetime.utcnow().isoformat(),
        "cache": {}
    }
    return {"success": True, "skill": name}

def call_skill(name: str, args: dict) -> dict:
    """Call a loaded skill with arguments."""
    if name not in SKILLS:
        return {"success": False, "error": f"Skill '{name}' not loaded"}

    skill = SKILLS[name]
    cache_key = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]

    if cache_key in skill["cache"]:
        return {"success": True, "cached": True, "result": skill["cache"][cache_key]}

    # In real implementation, would exec skill code
    result = {"skill": name, "args": args, "executed": True}
    skill["cache"][cache_key] = result
    return {"success": True, "cached": False, "result": result}

def list_skills() -> list:
    """List loaded skills."""
    return [{"name": k, "signature": v["signature"]} for k, v in SKILLS.items()]

# ─── s06: Conversation Compression ───────────────────────────────────────────

CONVERSATION_HISTORY = []
COMPACTED_ARCHIVES = []

def estimate_tokens(text: str) -> int:
    """Rough token estimation (1 token ≈ 4 chars)."""
    return len(text) // 4

def microcompact() -> dict:
    """Clear old tool results to save tokens."""
    cleared = 0
    for item in CONVERSATION_HISTORY:
        if isinstance(item, dict) and item.get("type") == "tool_result":
            content = str(item.get("content", ""))
            if len(content) > 200:
                item["content"] = content[:100] + "...[truncated]..."
                cleared += 1
    return {"success": True, "cleared_results": cleared}

def auto_compact(force: bool = False) -> dict:
    """Auto-archive conversation if over threshold."""
    total_text = json.dumps(CONVERSATION_HISTORY)
    tokens = estimate_tokens(total_text)

    if not force and tokens < COMPACT_THRESHOLD:
        return {"success": True, "action": "none", "tokens": tokens}

    # Archive to transcript
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    archive_id = f"archive_{int(time.time())}"
    archive_path = os.path.join(TRANSCRIPT_DIR, f"{archive_id}.jsonl")

    with open(archive_path, 'w') as f:
        for item in CONVERSATION_HISTORY:
            f.write(json.dumps(item) + "\n")

    COMPACTED_ARCHIVES.append({
        "id": archive_id,
        "path": archive_path,
        "tokens": tokens,
        "items": len(CONVERSATION_HISTORY),
        "archived_at": datetime.utcnow().isoformat()
    })

    # Summarize and reset
    summary = {
        "type": "compression_summary",
        "archives": len(COMPACTED_ARCHIVES),
        "total_items_archived": sum(a["items"] for a in COMPACTED_ARCHIVES),
        "latest_archive": archive_id
    }
    CONVERSATION_HISTORY.clear()
    CONVERSATION_HISTORY.append(summary)

    return {
        "success": True,
        "action": "archived",
        "archive_id": archive_id,
        "tokens": tokens,
        "items": len(CONVERSATION_HISTORY)
    }

def append_history(item: dict):
    """Append item to conversation history."""
    CONVERSATION_HISTORY.append(item)
    if AUTO_COMPACT_ENABLED:
        total_text = json.dumps(CONVERSATION_HISTORY)
        if estimate_tokens(total_text) >= COMPACT_THRESHOLD:
            auto_compact()

# ─── s07: File-Based Tasks ───────────────────────────────────────────────────

def file_task_read(path: str) -> dict:
    """Read a file as a task input."""
    content = read_file(path)
    return {
        "success": not content.startswith("ERROR"),
        "path": path,
        "content": content,
        "size": len(content)
    }

def file_task_write(path: str, content: str, validate: bool = True) -> dict:
    """Write a file with optional validation."""
    result = write_file(path, content)
    if not result["success"]:
        return result

    if validate:
        # Verify written content
        verify = read_file(path)
        if verify != content:
            return {"success": False, "error": "Validation failed: content mismatch"}

    return {"success": True, "path": path, "bytes": len(content)}

def file_task_edit(path: str, old_text: str, new_text: str, validate: bool = True) -> dict:
    """Edit a file with optional validation."""
    result = edit_file(path, old_text, new_text)
    if not result["success"]:
        return result

    if validate:
        content = read_file(path)
        if old_text in content:
            return {"success": False, "error": "Validation failed: old_text still present"}

    return {"success": True, "path": path}

# ─── s08: Background Jobs ────────────────────────────────────────────────────

JOBS = {}  # job_id -> {command, status, result, started_at, completed_at}

def start_job(command: str) -> dict:
    """Start a background job."""
    job_id = f"job_{uuid.uuid4().hex[:12]}"

    # Start process
    process = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    JOBS[job_id] = {
        "id": job_id,
        "command": command,
        "status": "running",
        "process": process,
        "result": None,
        "started_at": datetime.utcnow().isoformat(),
        "completed_at": None
    }

    return {"success": True, "job_id": job_id, "pid": process.pid}

def job_status(job_id: str) -> dict:
    """Check background job status."""
    if job_id not in JOBS:
        return {"success": False, "error": "Job not found"}

    job = JOBS[job_id]
    if job["status"] == "running":
        ret = job["process"].poll()
        if ret is not None:
            stdout, stderr = job["process"].communicate()
            job["status"] = "completed" if ret == 0 else "failed"
            job["result"] = {
                "exit_code": ret,
                "stdout": stdout,
                "stderr": stderr
            }
            job["completed_at"] = datetime.utcnow().isoformat()

    return {
        "success": True,
        "job_id": job_id,
        **{k: v for k, v in job.items() if k != "process"}
    }

def cancel_job(job_id: str) -> dict:
    """Cancel a running job."""
    if job_id not in JOBS:
        return {"success": False, "error": "Job not found"}

    job = JOBS[job_id]
    if job["status"] == "running":
        job["process"].terminate()
        job["status"] = "cancelled"
        job["completed_at"] = datetime.utcnow().isoformat()

    return {"success": True, "job_id": job_id, "status": job["status"]}

# ─── s09: Messaging ──────────────────────────────────────────────────────────

INBOXES = {}  # agent_id -> [{message}]

def send_message(to_id: str, message_type: str, payload: dict, from_id: str = "lead") -> dict:
    """Send a message to an agent's inbox."""
    if to_id not in INBOXES:
        INBOXES[to_id] = []

    msg = {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "from": from_id,
        "to": to_id,
        "type": message_type,
        "payload": payload,
        "timestamp": datetime.utcnow().isoformat(),
        "read": False
    }

    INBOXES[to_id].append(msg)
    return {"success": True, "message_id": msg["id"]}

def read_messages(agent_id: str, mark_read: bool = True) -> list:
    """Read messages from an agent's inbox."""
    if agent_id not in INBOXES:
        return []

    messages = [m for m in INBOXES[agent_id] if not m.get("read")]
    if mark_read:
        for m in INBOXES[agent_id]:
            m["read"] = True

    return messages

def broadcast(message_type: str, payload: dict, from_id: str = "lead") -> dict:
    """Broadcast message to all agents."""
    sent = 0
    for agent_id in list(SUBAGENTS.keys()):
        send_message(agent_id, message_type, payload, from_id)
        sent += 1
    return {"success": True, "sent": sent}

# ─── s10: Team Protocols ─────────────────────────────────────────────────────

SHUTDOWN_REQUESTS = {}
PLAN_REQUESTS = {}

def request_shutdown(reason: str, timeout_sec: int = 30) -> dict:
    """Initiate shutdown protocol."""
    req_id = f"shutdown_{uuid.uuid4().hex[:12]}"
    SHUTDOWN_REQUESTS[req_id] = {
        "status": "pending",
        "reason": reason,
        "votes": {},
        "deadline": time.time() + timeout_sec
    }
    broadcast("shutdown_request", {"request_id": req_id, "reason": reason})
    return {"success": True, "request_id": req_id}

def vote_shutdown(req_id: str, agent_id: str, approve: bool) -> dict:
    """Vote on shutdown request."""
    if req_id not in SHUTDOWN_REQUESTS:
        return {"success": False, "error": "Unknown request"}

    req = SHUTDOWN_REQUESTS[req_id]
    req["votes"][agent_id] = approve

    all_voted = all(aid in req["votes"] for aid in SUBAGENTS)
    if all_voted:
        approvals = sum(1 for v in req["votes"].values() if v)
        req["status"] = "approved" if approvals == len(SUBAGENTS) else "rejected"

    return {"success": True, "status": req["status"]}

def submit_plan(description: str, steps: list) -> dict:
    """Submit plan for approval."""
    req_id = f"plan_{uuid.uuid4().hex[:12]}"
    PLAN_REQUESTS[req_id] = {
        "status": "pending_review",
        "description": description,
        "steps": steps,
        "feedback": {}
    }
    broadcast("plan_review", {"request_id": req_id, "description": description, "steps": steps})
    return {"success": True, "request_id": req_id}

def review_plan(req_id: str, agent_id: str, approve: bool) -> dict:
    """Review a plan."""
    if req_id not in PLAN_REQUESTS:
        return {"success": False, "error": "Unknown plan"}

    req = PLAN_REQUESTS[req_id]
    req["feedback"][agent_id] = approve

    approvals = sum(1 for v in req["feedback"].values() if v)
    if approvals > len(SUBAGENTS) / 2:
        req["status"] = "approved"
    elif len(req["feedback"]) == len(SUBAGENTS):
        req["status"] = "rejected"

    return {"success": True, "status": req["status"]}

# ─── s11: Team Management ────────────────────────────────────────────────────

TEAM_TASKS = {}  # task_id -> {description, status, assignee, dependencies}

def create_team_task(description: str, dependencies: list = None) -> dict:
    """Create a team task."""
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    TEAM_TASKS[task_id] = {
        "id": task_id,
        "description": description,
        "status": "pending",
        "assignee": None,
        "dependencies": dependencies or [],
        "created_at": datetime.utcnow().isoformat()
    }
    return {"success": True, "task_id": task_id}

def claim_team_task(task_id: str, agent_id: str) -> dict:
    """Claim a task for an agent."""
    if task_id not in TEAM_TASKS:
        return {"success": False, "error": "Task not found"}

    task = TEAM_TASKS[task_id]
    if task["status"] != "pending":
        return {"success": False, "error": f"Task already {task['status']}"}

    # Check dependencies
    for dep in task["dependencies"]:
        if dep in TEAM_TASKS and TEAM_TASKS[dep]["status"] != "completed":
            return {"success": False, "error": f"Dependency {dep} not completed"}

    task["status"] = "claimed"
    task["assignee"] = agent_id
    task["claimed_at"] = datetime.utcnow().isoformat()

    if agent_id in SUBAGENTS:
        SUBAGENTS[agent_id]["status"] = "busy"

    return {"success": True, "task_id": task_id}

def complete_team_task(task_id: str, result: dict = None) -> dict:
    """Complete a team task."""
    if task_id not in TEAM_TASKS:
        return {"success": False, "error": "Task not found"}

    task = TEAM_TASKS[task_id]
    task["status"] = "completed"
    task["completed_at"] = datetime.utcnow().isoformat()
    task["result"] = result or {}

    if task["assignee"] and task["assignee"] in SUBAGENTS:
        SUBAGENTS[task["assignee"]]["status"] = "idle"

    return {"success": True, "task_id": task_id}

def auto_claim_idle(max_wait_sec: int = 60, poll_interval: int = 5) -> list:
    """Auto-claim tasks for idle agents."""
    claimed = []
    start = time.time()

    while time.time() - start < max_wait_sec:
        for agent_id, agent in SUBAGENTS.items():
            if agent["status"] != "idle":
                continue

            # Check inbox
            msgs = read_messages(agent_id, mark_read=False)
            if msgs:
                continue

            # Find unclaimed, unblocked task
            for task_id, task in TEAM_TASKS.items():
                if task["status"] != "pending":
                    continue

                deps_ok = all(
                    TEAM_TASKS.get(d, {}).get("status") == "completed"
                    for d in task["dependencies"]
                )
                if not deps_ok:
                    continue

                result = claim_team_task(task_id, agent_id)
                if result["success"]:
                    send_message(agent_id, "task_assignment", {
                        "task_id": task_id,
                        "description": task["description"],
                        "identity": {
                            "role": agent["role"],
                            "agent_id": agent_id,
                            "context": "You are an autonomous agent. Execute the assigned task."
                        }
                    })
                    claimed.append(result)
                    break

        if claimed:
            break
        time.sleep(poll_interval)

    return claimed

def team_status() -> dict:
    """Get full team status."""
    return {
        "agents": list(SUBAGENTS.values()),
        "tasks": list(TEAM_TASKS.values()),
        "pending_tasks": len([t for t in TEAM_TASKS.values() if t["status"] == "pending"]),
        "claimed_tasks": len([t for t in TEAM_TASKS.values() if t["status"] == "claimed"]),
        "completed_tasks": len([t for t in TEAM_TASKS.values() if t["status"] == "completed"]),
        "idle_agents": len([a for a in SUBAGENTS.values() if a["status"] == "idle"]),
        "busy_agents": len([a for a in SUBAGENTS.values() if a["status"] == "busy"])
    }

# ─── Demo / Self-Test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("S_FULL：完整综合测试框架（总章）")
    print("=" * 60)

    # s02: 基础工具
    print("\n[s02] 基础工具")
    result = bash("echo '来自 s_full 的问候'")
    print(f"  bash 输出：{result['stdout'].strip()}")

    # s03: 待办事项
    print("\n[s03] 待办事项")
    t1 = todo_create("实现功能 X")
    t2 = todo_create("编写测试")
    print(f"  已创建 {len(TODOS)} 条待办")
    todo_complete(t1["todo_id"])
    print(f"  待处理：{len([t for t in TODOS.values() if not t['done']])}")

    # s04: 子 Agent
    print("\n[s04] 子 Agent")
    a1 = spawn_subagent("backend", "你是一名后端开发工程师。")
    a2 = spawn_subagent("frontend", "你是一名前端开发工程师。")
    print(f"  已生成 {len(SUBAGENTS)} 个 Agent")

    # s05: 技能模块
    print("\n[s05] 技能模块")
    load_skill("calculator", "def run(a, b): return a + b", {"params": ["a", "b"]})
    skill_result = call_skill("calculator", {"a": 5, "b": 3})
    print(f"  技能执行结果：{skill_result['result']}")

    # s06: 对话压缩
    print("\n[s06] 对话压缩")
    append_history({"type": "user", "content": "测试消息"})
    compact = auto_compact(force=True)
    print(f"  归档 ID：{compact['archive_id']}")

    # s07: 文件任务
    print("\n[s07] 文件任务")
    write_file(".test_output.txt", "你好，世界")
    ft = file_task_read(".test_output.txt")
    print(f"  读取内容：{ft['content'][:20]}")

    # s08: 后台任务
    print("\n[s08] 后台任务")
    job = start_job("sleep 0.1 && echo 完成")
    time.sleep(0.2)
    status = job_status(job["job_id"])
    print(f"  任务状态：{status['status']}")

    # s09: 消息传递
    print("\n[s09] 消息传递")
    send_message(a1["agent_id"], "ping", {"data": "你好"})
    msgs = read_messages(a1["agent_id"])
    print(f"  消息数量：{len(msgs)}")

    # s10: 团队协议
    print("\n[s10] 团队协议")
    shutdown = request_shutdown("测试关闭", timeout_sec=60)
    vote_shutdown(shutdown["request_id"], a1["agent_id"], True)
    vote_shutdown(shutdown["request_id"], a2["agent_id"], True)
    print(f"  关闭状态：{SHUTDOWN_REQUESTS[shutdown['request_id']]['status']}")

    plan = submit_plan("部署 v2", ["构建", "测试", "部署"])
    review_plan(plan["request_id"], a1["agent_id"], True)
    review_plan(plan["request_id"], a2["agent_id"], True)
    print(f"  计划状态：{PLAN_REQUESTS[plan['request_id']]['status']}")

    # s11: 团队管理
    print("\n[s11] 团队管理")
    create_team_task("初始化数据库")
    create_team_task("构建 API", dependencies=[list(TEAM_TASKS.keys())[0]])
    create_team_task("构建 UI")

    # 自动认领
    claimed = auto_claim_idle(max_wait_sec=1)
    print(f"  自动认领：{len(claimed)} 个任务")

    status = team_status()
    print(f"  Agent 数：{len(status['agents'])}，任务数：{len(status['tasks'])}")
    print(f"  空闲：{status['idle_agents']}，忙碌：{status['busy_agents']}")

    # 清理
    bash("rm -f .test_output.txt")

    print("\n" + "=" * 60)
    print("所有系统运行正常，s_full 框架就绪。")
    print("=" * 60)
