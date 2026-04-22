#!/usr/bin/env python3
# Harness: team lead -- orchestrates teammates with spawn, messaging, and task claiming.
"""
s11_team_lead.py - Lead Agent with Teammate Spawning

A lead agent that can:
  1. Spawn teammates as subagents (s09 pattern)
  2. Send direct messages to teammates
  3. Maintain an inbox for incoming messages
  4. Implement shutdown protocol (s10)
  5. Gate plan approval before execution
  6. Auto-claim unassigned tasks for idle teammates

Builds on: s02 (base tools), s09 (subagents), s10 (protocols)
"""

import os
import sys
import json
import time
import uuid
from datetime import datetime
from typing import Optional

# ─── Base Tools (from s02) ───────────────────────────────────────────────────

def bash(command: str, timeout: int = 60) -> dict:
    import subprocess
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
    try:
        with open(path, 'r') as f:
            if limit:
                return f.read(limit)
            return f.read()
    except Exception as e:
        return f"ERROR: {e}"

def write_file(path: str, content: str) -> dict:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, 'w') as f:
            f.write(content)
        return {"success": True, "path": path, "bytes": len(content)}
    except Exception as e:
        return {"success": False, "error": str(e)}

def edit_file(path: str, old_text: str, new_text: str) -> dict:
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

# ─── Teammate Spawning (from s09) ────────────────────────────────────────────

TEAMMATE_REGISTRY = {}  # teammate_id -> {pid, role, status, inbox_path}

def spawn_teammate(role: str, system_prompt: str, workdir: Optional[str] = None) -> dict:
    """Spawn a teammate subagent process."""
    teammate_id = f"teammate_{role}_{uuid.uuid4().hex[:8]}"
    inbox_path = f".inbox/{teammate_id}.jsonl"
    os.makedirs(".inbox", exist_ok=True)

    # In a real implementation, this would fork/exec a new agent process
    # For harness demonstration, we record the metadata
    import hashlib
    pid = int(hashlib.md5(teammate_id.encode()).hexdigest()[:8], 16)

    teammate = {
        "id": teammate_id,
        "role": role,
        "pid": pid,
        "status": "idle",
        "inbox_path": inbox_path,
        "workdir": workdir or os.getcwd(),
        "spawned_at": datetime.utcnow().isoformat(),
        "system_prompt_hash": hashlib.sha256(system_prompt.encode()).hexdigest()[:16]
    }
    TEAMMATE_REGISTRY[teammate_id] = teammate

    return {
        "success": True,
        "teammate_id": teammate_id,
        "pid": pid,
        "inbox_path": inbox_path
    }

# ─── Messaging System ────────────────────────────────────────────────────────

def send_message(to_id: str, message_type: str, payload: dict, from_id: str = "lead") -> dict:
    """Send a message to a teammate's inbox."""
    if to_id not in TEAMMATE_REGISTRY:
        return {"success": False, "error": f"Unknown teammate: {to_id}"}

    inbox_path = TEAMMATE_REGISTRY[to_id]["inbox_path"]
    msg = {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "from": from_id,
        "to": to_id,
        "type": message_type,
        "payload": payload,
        "timestamp": datetime.utcnow().isoformat(),
        "read": False
    }

    with open(inbox_path, 'a') as f:
        f.write(json.dumps(msg) + "\n")

    return {"success": True, "message_id": msg["id"]}

def read_inbox(teammate_id: str, mark_read: bool = True) -> list:
    """Read messages from a teammate's inbox."""
    if teammate_id not in TEAMMATE_REGISTRY:
        return []

    inbox_path = TEAMMATE_REGISTRY[teammate_id]["inbox_path"]
    if not os.path.exists(inbox_path):
        return []

    messages = []
    with open(inbox_path, 'r') as f:
        for line in f:
            if line.strip():
                msg = json.loads(line)
                if not msg.get("read"):
                    messages.append(msg)
                    if mark_read:
                        msg["read"] = True

    # Rewrite inbox with read status updated
    if mark_read and messages:
        all_msgs = []
        with open(inbox_path, 'r') as f:
            for line in f:
                if line.strip():
                    all_msgs.append(json.loads(line))
        with open(inbox_path, 'w') as f:
            for msg in all_msgs:
                f.write(json.dumps(msg) + "\n")

    return messages

def broadcast_message(message_type: str, payload: dict, from_id: str = "lead") -> dict:
    """Send a message to all teammates."""
    results = []
    for teammate_id in TEAMMATE_REGISTRY:
        result = send_message(teammate_id, message_type, payload, from_id)
        results.append({"teammate_id": teammate_id, **result})
    return {"success": True, "sent": len(results), "results": results}

# ─── Shutdown Protocol (from s10) ────────────────────────────────────────────

SHUTDOWN_REQUESTS = {}  # request_id -> {status, votes, deadline}

def request_shutdown(reason: str, timeout_sec: int = 30) -> dict:
    """Initiate shutdown protocol with all teammates."""
    request_id = f"shutdown_{uuid.uuid4().hex[:12]}"
    deadline = time.time() + timeout_sec

    SHUTDOWN_REQUESTS[request_id] = {
        "status": "pending",
        "reason": reason,
        "votes": {},
        "deadline": deadline,
        "requested_at": datetime.utcnow().isoformat()
    }

    # Broadcast shutdown request
    broadcast_message("shutdown_request", {
        "request_id": request_id,
        "reason": reason,
        "deadline": deadline,
        "reply_to": "lead"
    })

    return {
        "success": True,
        "request_id": request_id,
        "status": "pending",
        "timeout_sec": timeout_sec
    }

def vote_shutdown(request_id: str, teammate_id: str, approve: bool) -> dict:
    """Teammate votes on shutdown request."""
    if request_id not in SHUTDOWN_REQUESTS:
        return {"success": False, "error": "Unknown shutdown request"}

    req = SHUTDOWN_REQUESTS[request_id]
    if req["status"] != "pending":
        return {"success": False, "error": f"Shutdown already {req['status']}"}

    req["votes"][teammate_id] = approve

    # Check if all teammates voted
    all_voted = all(tid in req["votes"] for tid in TEAMMATE_REGISTRY)
    if all_voted:
        approvals = sum(1 for v in req["votes"].values() if v)
        if approvals == len(TEAMMATE_REGISTRY):
            req["status"] = "approved"
        else:
            req["status"] = "rejected"

    return {
        "success": True,
        "request_id": request_id,
        "your_vote": approve,
        "current_status": req["status"],
        "votes_cast": len(req["votes"]),
        "total_teammates": len(TEAMMATE_REGISTRY)
    }

def check_shutdown_status(request_id: str) -> dict:
    """Check current status of shutdown request."""
    if request_id not in SHUTDOWN_REQUESTS:
        return {"success": False, "error": "Unknown request"}

    req = SHUTDOWN_REQUESTS[request_id]

    # Check timeout
    if req["status"] == "pending" and time.time() > req["deadline"]:
        req["status"] = "timeout"

    return {
        "request_id": request_id,
        "status": req["status"],
        "votes": req["votes"],
        "reason": req["reason"]
    }

# ─── Plan Approval Protocol ──────────────────────────────────────────────────

PLAN_REQUESTS = {}  # request_id -> {plan, status, feedback}

def submit_plan(description: str, steps: list, timeout_sec: int = 60) -> dict:
    """Submit a plan for approval before execution."""
    request_id = f"plan_{uuid.uuid4().hex[:12]}"
    deadline = time.time() + timeout_sec

    PLAN_REQUESTS[request_id] = {
        "status": "pending_review",
        "description": description,
        "steps": steps,
        "feedback": {},
        "deadline": deadline,
        "submitted_at": datetime.utcnow().isoformat()
    }

    broadcast_message("plan_review", {
        "request_id": request_id,
        "description": description,
        "steps": steps,
        "deadline": deadline
    })

    return {
        "success": True,
        "request_id": request_id,
        "status": "pending_review",
        "step_count": len(steps)
    }

def review_plan(request_id: str, reviewer_id: str, approve: bool, feedback: str = "") -> dict:
    """Review and approve/reject a plan."""
    if request_id not in PLAN_REQUESTS:
        return {"success": False, "error": "Unknown plan request"}

    req = PLAN_REQUESTS[request_id]
    req["feedback"][reviewer_id] = {
        "approve": approve,
        "feedback": feedback,
        "timestamp": datetime.utcnow().isoformat()
    }

    # Simple majority for approval
    approvals = sum(1 for f in req["feedback"].values() if f["approve"])
    if approvals > len(TEAMMATE_REGISTRY) / 2:
        req["status"] = "approved"
    elif len(req["feedback"]) == len(TEAMMATE_REGISTRY):
        req["status"] = "rejected"

    return {
        "success": True,
        "request_id": request_id,
        "your_vote": approve,
        "current_status": req["status"]
    }

# ─── Task Claiming System ────────────────────────────────────────────────────

TASK_REGISTRY = {}  # task_id -> {status, assignee, description, dependencies}

def create_task(description: str, dependencies: list = None) -> dict:
    """Create a new task."""
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    TASK_REGISTRY[task_id] = {
        "id": task_id,
        "description": description,
        "status": "pending",
        "assignee": None,
        "dependencies": dependencies or [],
        "created_at": datetime.utcnow().isoformat(),
        "claimed_at": None,
        "completed_at": None
    }
    return {"success": True, "task_id": task_id}

def claim_task(task_id: str, teammate_id: str) -> dict:
    """Claim a task for execution."""
    if task_id not in TASK_REGISTRY:
        return {"success": False, "error": "Unknown task"}

    task = TASK_REGISTRY[task_id]
    if task["status"] != "pending":
        return {"success": False, "error": f"Task already {task['status']}"}

    # Check dependencies
    for dep_id in task["dependencies"]:
        if dep_id in TASK_REGISTRY and TASK_REGISTRY[dep_id]["status"] != "completed":
            return {"success": False, "error": f"Dependency {dep_id} not completed"}

    task["status"] = "claimed"
    task["assignee"] = teammate_id
    task["claimed_at"] = datetime.utcnow().isoformat()

    # Update teammate status
    if teammate_id in TEAMMATE_REGISTRY:
        TEAMMATE_REGISTRY[teammate_id]["status"] = "busy"

    return {"success": True, "task_id": task_id, "assignee": teammate_id}

def complete_task(task_id: str, result: dict = None) -> dict:
    """Mark a task as completed."""
    if task_id not in TASK_REGISTRY:
        return {"success": False, "error": "Unknown task"}

    task = TASK_REGISTRY[task_id]
    task["status"] = "completed"
    task["completed_at"] = datetime.utcnow().isoformat()
    task["result"] = result or {}

    # Free up teammate
    if task["assignee"] and task["assignee"] in TEAMMATE_REGISTRY:
        TEAMMATE_REGISTRY[task["assignee"]]["status"] = "idle"

    return {"success": True, "task_id": task_id}

def auto_claim_idle(max_wait_sec: int = 60, poll_interval: int = 5) -> list:
    """Auto-claim unclaimed tasks for idle teammates.

    Polls inbox every poll_interval seconds for max_wait_sec timeout.
    If teammate remains idle, auto-claims an unclaimed, unblocked task.
    """
    claimed = []
    start = time.time()

    while time.time() - start < max_wait_sec:
        for teammate_id, teammate in TEAMMATE_REGISTRY.items():
            if teammate["status"] != "idle":
                continue

            # Check inbox for messages
            messages = read_inbox(teammate_id, mark_read=False)
            if messages:
                continue  # Teammate has work to do

            # Find unclaimed, unblocked task
            for task_id, task in TASK_REGISTRY.items():
                if task["status"] != "pending":
                    continue

                # Check dependencies
                deps_satisfied = all(
                    TASK_REGISTRY.get(dep_id, {}).get("status") == "completed"
                    for dep_id in task["dependencies"]
                )
                if not deps_satisfied:
                    continue

                # Auto-claim with identity reinjection
                result = claim_task(task_id, teammate_id)
                if result["success"]:
                    # Send identity context for compressed contexts
                    send_message(teammate_id, "task_assignment", {
                        "task_id": task_id,
                        "description": task["description"],
                        "identity": {
                            "role": teammate["role"],
                            "teammate_id": teammate_id,
                            "context": "You are an autonomous teammate. Execute the assigned task."
                        }
                    })
                    claimed.append(result)
                    break

        if claimed:
            break

        time.sleep(poll_interval)

    return claimed

def list_tasks(status: Optional[str] = None) -> list:
    """List all tasks, optionally filtered by status."""
    tasks = list(TASK_REGISTRY.values())
    if status:
        tasks = [t for t in tasks if t["status"] == status]
    return tasks

def list_teammates() -> list:
    """List all teammates and their status."""
    return list(TEAMMATE_REGISTRY.values())

# ─── Demo / Self-Test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("S11：团队领导 Agent 测试框架")
    print("=" * 60)

    # 生成队友
    print("\n--- 生成队友 ---")
    t1 = spawn_teammate("backend", "你是一名后端开发工程师。")
    t2 = spawn_teammate("frontend", "你是一名前端开发工程师。")
    print(f"已生成：{t1['teammate_id']} (PID: {t1['pid']})")
    print(f"已生成：{t2['teammate_id']} (PID: {t2['pid']})")

    # 创建任务
    print("\n--- 创建任务 ---")
    create_task("设计 API 结构")
    create_task("实现后端接口", dependencies=["task_xxx"])  # 依赖 ID 稍后修正
    create_task("构建前端组件")

    # 修正依赖为真实任务 ID
    task_ids = list(TASK_REGISTRY.keys())
    TASK_REGISTRY[task_ids[1]]["dependencies"] = [task_ids[0]]
    print(f"已创建 {len(TASK_REGISTRY)} 个任务")

    # 认领任务
    print("\n--- 认领任务 ---")
    result = claim_task(task_ids[0], t1["teammate_id"])
    print(f"认领结果：{result}")

    # 发送消息
    print("\n--- 消息传递 ---")
    msg_result = send_message(t1["teammate_id"], "status_check", {"query": "进度"})
    print(f"消息已发送：{msg_result['message_id']}")

    inbox = read_inbox(t1["teammate_id"])
    print(f"收件箱消息数：{len(inbox)}")

    # 提交计划
    print("\n--- 计划审批 ---")
    plan = submit_plan("重构认证系统", [
        "审查当前认证流程",
        "设计新的 JWT 处理方案",
        "实施变更",
        "更新测试"
    ], timeout_sec=300)
    print(f"计划已提交：{plan['request_id']}")

    # 审批计划
    review_plan(plan["request_id"], t1["teammate_id"], True, "看起来不错！")
    review_plan(plan["request_id"], t2["teammate_id"], True, "已批准。")
    print(f"计划状态：{PLAN_REQUESTS[plan['request_id']]['status']}")

    # 关闭协议
    print("\n--- 关闭协议 ---")
    shutdown = request_shutdown("冲刺结束", timeout_sec=60)
    print(f"关闭请求已发出：{shutdown['request_id']}")

    vote_shutdown(shutdown["request_id"], t1["teammate_id"], True)
    vote_shutdown(shutdown["request_id"], t2["teammate_id"], True)

    status = check_shutdown_status(shutdown["request_id"])
    print(f"关闭状态：{status['status']}")

    # 最终状态
    print("\n--- 最终状态 ---")
    print(f"队友数量：{len(list_teammates())}")
    print(f"任务总数：{len(list_tasks())}")
    print(f"待处理：{len(list_tasks('pending'))}")
    print(f"已认领：{len(list_tasks('claimed'))}")

    print("\n" + "=" * 60)
    print("S11 框架就绪，可用于团队协作编排")
    print("=" * 60)
