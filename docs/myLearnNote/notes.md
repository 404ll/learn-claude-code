# Claude Code 学习笔记

---

## s01 — Agent Loop（Agent 循环）

### 核心思想

语言模型本身只能推理，无法触达真实世界。Agent Loop 就是把模型的输出（工具调用）执行后，把结果再喂给模型，形成闭环。

```
+--------+      +-------+      +---------+
|  User  | ---> |  LLM  | ---> |  Tool   |
| prompt |      |       |      | execute |
+--------+      +---+---+      +----+----+
                    ^                |
                    |   tool_result  |
                    +----------------+
                    (loop until stop_reason != "tool_use")
```

### 核心代码模式

```python
def agent_loop(messages):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return  # 模型不再调用工具，结束

        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = run_bash(block.input["command"])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})
```

### 关键要点

- **退出条件**：`stop_reason != "tool_use"`，模型主动决定停止。
- **消息是累积的**：`messages` 列表不断追加，模型始终看到完整上下文。
- `response.content` 是一个 block 数组（API 层定义），类型可以是 `text` 或 `tool_use`，顺序不固定。
- `tool_result` 以 `role: "user"` 的形式回传，这是 API 的规定格式。
- 后续所有章节都在这个循环上叠加机制，**循环本身始终不变**。

---

## s02 — Tool Use（工具扩展）

### 核心思想

加工具不需要改循环。只需要：1）写 handler 函数；2）注册到 dispatch map；3）在 `TOOLS` 里加 schema。

```
+--------+      +-------+      +------------------+
|  User  | ---> |  LLM  | ---> | Tool Dispatch    |
| prompt |      |       |      | {                |
+--------+      +---+---+      |   bash: run_bash |
                    ^           |   read: run_read |
                    |           |   write: run_wr  |
                    +-----------+   edit: run_edit |
                    tool_result | }                |
                                +------------------+
```

### 核心代码模式

```python
# dispatch map：工具名 → handler
TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

# 循环内分发（与 s01 完全一致，只多了这一行查找）
handler = TOOL_HANDLERS.get(block.name)
output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
```

### 关键要点

- **Dispatch Map 模式**：用字典替代 if/elif 链，新增工具只加一条记录，不碰循环。
- **路径沙箱**：`safe_path()` 用 `resolve()` + `is_relative_to()` 防止路径逃逸工作区。
- `block.input` 是一个 dict，`**block.input` 直接解包传给 handler，参数名由 schema 的 `properties` 定义。
- s01 → s02 的唯一变化：工具从 1 个（bash）扩展到 4 个，循环代码零改动。

### s01 vs s02 对比

| 组件        | s01          | s02                        |
|-------------|--------------|----------------------------|
| 工具数量    | 1（bash）    | 4（bash/read/write/edit）  |
| 工具分发    | 硬编码       | `TOOL_HANDLERS` 字典       |
| 路径安全    | 无           | `safe_path()` 沙箱         |
| Agent loop  | 不变         | 不变                       |

---

## s03 — TodoWrite（任务规划）

### 核心思想

模型容易在多步任务中"迷失"。通过给模型一个 `todo` 工具 + system prompt 引导，让模型自己规划、自己追踪进度，harness 同时持有状态以便可见。

### 两个核心机制

**1. TodoManager：模型可写入的状态容器**

`TodoManager` 不是主动管理任务的 manager，而是一个**由模型写入、由 harness 持有**的状态容器。

- 模型每次调用 `todo` 工具时传入**完整的 items 数组**，`update()` 直接整体替换，不做增量 diff
- harness 负责校验合法性（最多 20 条、同时只能有一个 `in_progress`）并渲染成可读文本返回给模型

```python
class TodoManager:
    def update(self, items: list) -> str:
        # 校验：最多20条、只能有一个 in_progress
        if in_progress_count > 1:
            raise ValueError("Only one task can be in_progress at a time")
        self.items = validated
        return self.render()  # 返回可读的任务列表给模型
```

**2. Nag Reminder：harness 主动干预模型行为**

模型容易"忘记"更新任务状态，harness 检测到连续 3 轮没调用 `todo`，就往消息里强制注入一条提醒：

```python
rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
if rounds_since_todo >= 3:
    results.append({"type": "text", "text": "<reminder>Update your todos.</reminder>"})
```

这是**代码层面的行为约束**，不依赖 prompt 说"请记得更新"。

### 模型的"拆分任务"能力从哪来？

模型没有内置的拆分任务能力，来源是：
1. `TOOLS` 数组里注册了 `todo` 工具（给模型"手"）
2. system prompt 说 `"Use the todo tool to plan multi-step tasks"`（告诉模型"手"的用途）

模型自己推断出何时调用、如何拆分。**工具的 `description` 字段就是 harness 和模型之间的接口契约。**

### 泳道划分

```
模型 (LLM)                         Harness (Python)
    |                                     |
    |  -- function call 决定调用 todo -->  |
    |     (模型内部能力，harness不可见)      |
    |                               TodoManager.update()
    |                               校验 + 渲染状态
    |  <-- tool_result 返回 ------------   |
    |                                     |
```

- **模型泳道**：决定"要不要调用、调用哪个、传什么参数"——function call 能力
- **Harness 泳道**：执行工具、维护状态、把结果塞回消息
- 边界信号：`stop_reason == "tool_use"` 是模型说"球在你那边"的唯一协议

### 关键要点

- `TodoManager` 的状态只存在于 harness 内存中，模型通过 tool_result 感知当前状态。
- 模型每次调用 `todo` 必须传完整列表，不能只传"变更项"。
- Nag reminder 是 s03 引入的第一个**harness 主动干预**模式，后续章节会继续叠加。

---

## s04 — Subagent（子 Agent）

### 核心思想

把子任务派发给一个拥有全新 context 的 subagent 执行，只把摘要返回给 parent。parent 的 context 保持干净，子任务的噪音被隔离丢弃。

```
Parent agent                     Subagent
+------------------+             +------------------+
| messages=[...]   |             | messages=[]      |  <-- 全新
|                  |  task 工具  |                  |
|  调用 task ------>|----------->| while tool_use:  |
|                  |            |   执行工具        |
|  result = 摘要  |<-----------| return 最后文本  |
+------------------+  摘要       +------------------+
        |
  parent context 保持干净
  subagent context 执行完丢弃
```

### 关键机制

**1. `task` 就是普通工具**

对 parent 模型来说，`task` 工具和 `bash` 没有区别，都是 `TOOLS` 数组里的一项。parent 自己决定何时调用，harness 收到 `block.name == "task"` 后才实例化 subagent：

```python
if block.name == "task":
    output = run_subagent(prompt)   # harness 在这里实例化 subagent
else:
    handler = TOOL_HANDLERS.get(block.name)
    output = handler(**block.input)
```

**2. 隔离的是 messages，共享的是文件系统**

```python
def run_subagent(prompt: str) -> str:
    sub_messages = [{"role": "user", "content": prompt}]  # 全新 context
    # ... subagent loop ...
    # subagent 操作同一个 WORKDIR，文件改动 parent 可见
```

**3. 摘要是 subagent 最后一轮的自然输出**

```python
return "".join(b.text for b in response.content if hasattr(b, "text")) or "(no summary)"
```

靠 system prompt 软约束（`"summarize your findings"`），不是强制结构化。生产环境应用 JSON schema 保证格式。

**4. subagent 没有 `task` 工具，防止递归派生**

```python
CHILD_TOOLS = [bash, read_file, write_file, edit_file]  # 无 task
PARENT_TOOLS = CHILD_TOOLS + [task]                     # 只有 parent 有
```

### 泳道划分

```
模型决定调用 task → harness 收到信号 → harness 实例化 subagent → 摘要作为 tool_result 返回
```

parent 模型感知不到 `task` 背后跑了一个完整的 agent loop，封装由 harness 完成。

### 关键要点

- `block.type == "tool_use"` 是 **API 层**概念；`block.name == "task"` 是**应用层**概念，工具名完全由开发者自定义。
- 隔离的本质不只是"解决 context 过长"，而是**主动隔离子任务噪音**，让 parent 保持清晰。
- Tool 是模型和真实世界的唯一接口，模型能做什么完全取决于 harness 注册了哪些工具。

---

## s05 — Skill Loading（按需加载知识）

### 核心思想

不把所有知识都塞进 system prompt，而是分两层注入——启动时只注入元数据，真正需要时才加载完整内容。

```
Layer 1（启动时，~100 tokens/skill）：
  system prompt 里只有 skill 名称 + 描述

Layer 2（按需，模型调用 load_skill 时）：
  完整 skill body 通过 tool_result 注入进 messages
```

### 两层结构

**Layer 1：元数据注入 system prompt**

```python
SYSTEM = f"""You are a coding agent at {WORKDIR}.
Skills available:
{SKILL_LOADER.get_descriptions()}"""  # 启动时执行一次，静态字符串
```

`get_descriptions()` 在程序启动时通过 f-string **执行一次**，结果是静态字符串，不会每次请求重新扫描。

**Layer 2：按需加载完整内容**

```python
"load_skill": lambda **kw: SKILL_LOADER.get_content(kw["name"])
# 返回：<skill name="...">完整 body 内容</skill>
```

模型调用 `load_skill` 后，完整内容作为 `tool_result` 进入 messages，**后续轮次模型仍能看到**——是推迟注入时机，不是用完就丢。

### Skill 文件结构

```
skills/
  pdf/
    SKILL.md    <-- YAML frontmatter (name, description) + body
  code-review/
    SKILL.md
```

frontmatter 提供 Layer 1 的元数据，body 是 Layer 2 加载的完整内容。

### 关键要点

- 这是一个 **token 预算管理策略**：把不常用的大块知识从 system prompt 移出，只在需要时占用 context。
- `load_skill` 和其他工具一样，模型自己决定何时调用——靠 system prompt 里的引导（`"Use load_skill to access specialized knowledge"`）。
- skill 加载后留在 messages 里，不是临时注入。

---

## s07 — Task System（任务系统）

### 核心思想

把 s03 的内存扁平清单升级为**持久化到磁盘的任务图（DAG）**，支持依赖关系，为后续多 agent 并行协作打基础。

### s03 vs s07

| | s03 TodoManager | s07 TaskManager |
|---|---|---|
| 结构 | 扁平列表 | DAG 图（有依赖关系）|
| 存储 | 内存，进程退出即消失 | 磁盘文件，重启后存活 |
| 阻塞逻辑 | 无 | `blockedBy` 字段 + 自动解锁 |
| 调度 | 模型自己决定 | 模型自己决定（并行调度在后续章节）|

### 任务图结构

```
.tasks/
  task_1.json  {"id":1, "status":"completed"}
  task_2.json  {"id":2, "blockedBy":[1], "status":"pending"}
  task_3.json  {"id":3, "blockedBy":[1], "status":"pending"}
  task_4.json  {"id":4, "blockedBy":[2,3], "status":"pending"}
```

task 1 完成 → 解锁 task 2 和 3（可并行）→ 两者都完成 → 解锁 task 4。

### 持久化：直接写文件

没有单独的持久化模块，每次 `create` / `update` 都立刻调用 `_save()` 落盘：

```python
def _save(self, task):
    path = self.dir / f"task_{task['id']}.json"
    path.write_text(json.dumps(task, indent=2))
```

### 自动解锁依赖

任务标记为 `completed` 时，自动把它的 ID 从其他任务的 `blockedBy` 里移除：

```python
def _clear_dependency(self, completed_id):
    for f in self.dir.glob("task_*.json"):
        task = json.loads(f.read_text())
        if completed_id in task.get("blockedBy", []):
            task["blockedBy"].remove(completed_id)
            self._save(task)
```

### 关键要点

- s07 的重点是**建立结构 + 持久化**，调度逻辑（谁来决定执行顺序）仍靠模型自己判断 `blockedBy` 是否为空。
- 真正利用这个图结构并行调度多个 agent，是 s08、s09 之后的事。
- 任务图是 s07 之后所有机制的协调骨架：后台执行（s08）、多 agent 团队（s09+）、worktree 隔离（s12）都读写同一个结构。
