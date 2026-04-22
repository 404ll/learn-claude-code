# Claude Code 学习笔记

## 目录

- [设计哲学总览](#设计哲学总览)
- [s01 — Agent Loop](#s01--agent-loopagent-循环)
- [s02 — Tool Use](#s02--tool-use工具扩展)
- [s03 — TodoWrite](#s03--todowrite任务规划)
- [s04 — Subagent](#s04--subagent子-agent)
- [s05 — Skill Loading](#s05--skill-loading按需加载知识)
- [s06 — Context Compact](#s06--context-compact上下文压缩)
- [s07 — Task System](#s07--task-system任务系统)
- [s08 — Background Tasks](#s08--background-tasks后台任务)
- [s09 — Agent Teams](#s09--agent-teamsagent-团队)
- [s10 — Team Protocols](#s10--team-protocols团队协议)
- [s11 — Autonomous Agents](#s11--autonomous-agents自治-agent)
- [s12 — Worktree Task Isolation](#s12--worktree-task-isolationworktree-任务隔离)

---

## 设计哲学总览

这个仓库的核心不是具体实现，而是**设计哲学**：复杂的 agent 系统，是简单模式的组合叠加，循环本身从未改变。

### 每章解决的问题

| 章节 | 引入机制 | 解决的问题 |
|------|----------|------------|
| s01 | Agent Loop | 模型无法触达真实世界 |
| s02 | Tool Dispatch | 工具扩展不改循环 |
| s03 | TodoManager + Nag（催促注入） | 模型在多步任务中迷失、忘记更新进度 |
| s04 | Subagent | 子任务噪音污染主 context |
| s05 | Skill Loading | system prompt 塞太多知识 |
| s06 | Context Compact | context 窗口被撑爆 |
| s07 | Task Graph | 任务无依赖关系、不持久化 |
| s08 | Background Tasks | 慢命令阻塞 agent |
| s09 | Agent Teams | 单 agent 无法协作分工 |
| s10 | Team Protocols | agent 间缺乏结构化协调（关机/审批）|
| s11 | Autonomous Agents | 领导无法扩展地手动分配任务 |
| s12 | Worktree Isolation | 并行 agent 共享目录互相污染 |

### 可类比的设计模式

| 章节 | 类比模式 |
|------|----------|
| s01 Agent Loop | 事件循环（浏览器 Event Loop） |
| s04 Subagent | 无状态函数调用 / REST API |
| s05 按需加载 | 懒加载 / Code Splitting |
| s08 后台任务 | 观察者模式 / 事件驱动 |
| s09 收件箱通信 | Actor 模型 |
| s10 请求-响应协议 | 有限状态机（Finite State Machine）：状态只能在有限几个值之间按规则流转，如 `pending → approved / rejected`。前端类比：按钮的 `normal → loading → success / error` |

### 贯穿全课的核心规律

- **Tool 是模型与世界的唯一接口**，模型能做什么完全取决于 harness 注册了哪些工具
- **messages 是信息注入的唯一通道**，harness 通过控制往里塞什么来控制模型的注意力
- **模型决策，harness 执行**——`stop_reason == "tool_use"` 是两者之间唯一的协议边界
- **循环本身从未改变**，s01 的 while loop 在 s12 依然是同一个结构

### 软约束 vs 硬约束

贯穿全课的一个重要区别：

| | 软约束 | 硬约束 |
|---|---|---|
| 实现方式 | system prompt 引导 | 代码逻辑强制 |
| 例子 | "高风险操作先提交计划" | subagent 没有 `task` 工具（物理上无法递归） |
| 例子 | "用 todo 工具规划任务" | nag reminder 连续 3 轮强制注入 |
| 可靠性 | 依赖模型理解和遵守 | 代码保证，模型无法绕过 |

**模型对 harness 是黑盒**：模型感知不到 subagent 背后有完整的 agent loop，感知不到后台线程，感知不到收件箱文件——它只看到工具调用和 tool_result。harness 的内部实现对模型完全透明。

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

## s06 — Context Compact（上下文压缩）

### 核心思想

上下文窗口是有限的，读文件、跑命令会快速消耗 token。三层压缩策略，激进程度递增，让 agent 能在大项目里持续工作。

### 三层压缩

**Layer 1 — micro_compact（每轮静默执行）**

把超过 3 轮的旧 `tool_result` 替换为占位符，保留最近几条：

```
[Previous: used read_file]   <-- 原来的文件内容被替换掉
```

轻量、无感知，每轮自动执行。

**Layer 2 — auto_compact（token 超阈值自动触发）**

1. 把完整对话存到磁盘 `.transcripts/`（备份，灾难恢复用）
2. 新建模型实例对原对话做摘要
3. 用摘要**替换掉内存里的全部 messages**，只剩一条：

```python
return [{"role": "user", "content": f"[Compressed]\n\n{summary}"}]
```

压缩后：
- **磁盘**：完整原始对话（模型不会读它，仅备份）
- **内存**：只剩摘要这一条 message

**Layer 3 — compact 工具（模型主动触发）**

模型调用 `compact` 工具，触发和 Layer 2 完全相同的摘要流程。区别只是触发时机：Layer 2 是 token 超阈值被动触发，Layer 3 是模型主动判断需要压缩。

### 关键要点

- 三层机制解决的是同一个问题，激进程度不同：占位符 → 自动摘要 → 主动摘要。
- 摘要只存在于内存 messages 里，不会和原始对话一起存磁盘。
- s04 的 subagent 隔离 context 是**预防**手段，s06 的压缩是**治理**手段，两者互补。

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

---

## s08 — Background Tasks（后台任务）

### 核心思想

耗时命令（`npm install`、`pytest`、`docker build`）阻塞 agent loop 时，模型只能干等。s08 把慢命令丢到后台线程跑，主线程继续工作，任务完成后在下一轮 LLM 调用前注入结果。

类比前端：

```javascript
// 发出去不等，完成后回调
fetch('/run').then(result => notificationQueue.push(result))
return "task started"  // 立即返回
```

### 执行时序

```
主线程：spawn A → spawn B → 做其他事 → [收到 A、B 结果] → 继续
后台：  [A 在跑]   [B 在跑]
```

### 结果注入时机

每轮循环开头，LLM 调用之前，先排空通知队列：

```
while True:
    0. 检查后台任务队列，有结果就注入 messages  ← 新加的
    1. 调用 LLM
    2. 检查 stop_reason
    3. 执行工具
    4. 把结果塞回 messages
    5. 回到第 1 步
```

模型每次开始新一轮思考，都能看到截至那一刻所有已完成的后台任务结果。

### 关键要点

- **agent loop 本身还是单线程**，只有子进程的 I/O 被并行化——模型感知不到背后有线程在跑。
- 通知结果被截断为前 500 字符，避免撑爆 context。模型需要完整结果时，主动调用 `check` 工具查询。
- "并行"是 harness 用线程实现的，不是模型的能力。模型只是调用了 `background_run` 工具。

---

## s09 — Agent Teams（Agent 团队）

### 核心思想

s04 的 subagent 是一次性的，用完即销毁。s09 引入有身份、有生命周期、能持续通信的**持久 teammate**，实现真正的多 agent 协作。

### s04 vs s09

| | s04 Subagent | s09 Teammate |
|---|---|---|
| 身份 | 无 | 有名字、有角色 |
| 生命周期 | 函数调用，返回即销毁 | 线程持续存活，idle → working → idle |
| 通信 | 无 | JSONL 收件箱 |
| messages | 函数局部变量，用完释放 | 线程内持久，跨多轮保留 |

### 文件结构

```
.team/
  config.json       ← 团队名册（成员、状态）
  inbox/
    alice.jsonl     ← alice 的收件箱（append-only）
    bob.jsonl
```

### 通信机制

- `send()`：往目标的 `.jsonl` 文件追加一行 JSON
- `read_inbox()`：读取全部消息，**读完立刻清空（drain-on-read）**

```python
path.write_text("")  # 读完即清空，消息只被消费一次
```

每个 teammate 在每次 LLM 调用前检查收件箱，有消息就注入 context——和 s08 的通知队列机制一样。

### 关键要点

- **收件箱是 drain-on-read**：消息只会被消费一次，不可重复读取——类似消息队列的消费者模型。
- 每个 teammate 是独立线程，有自己的 messages 和自己的 loop，这是和 s04 最本质的区别。
- 整体可类比 **Actor 模型**：每个 agent 只处理发给自己的消息，不共享状态。
- `/inbox`、`/team` 是 harness 层的调试快捷命令，直接由 Python 处理，不经过 LLM。
- s09 的缺陷：没有协调协议，agent 完成任务后只能靠反复轮询收件箱"忙等"，最多跑 50 轮强制退出。这个问题在 s10 解决。

---

## s10 — Team Protocols（团队协议）

### 核心思想

s09 的 agent 能通信但缺乏结构化协调。s10 引入**请求-响应协议**，解决两个问题：优雅关机 + 高风险操作审批。

### 两个协议场景

**关机握手**
```
Lead                    Teammate
 |-- shutdown_request -->|
 |   {req_id: "abc"}     |
 |<-- shutdown_response--|
 |   {req_id: "abc",     |
 |    approve: true}     |
```
防止直接杀线程留下写了一半的文件。

**计划审批**
```
Teammate                Lead
 |-- plan_request ------>|
 |   {req_id: "xyz",     |
 |    plan: "..."}       |  lead 审查
 |<-- plan_response -----|
 |   {req_id: "xyz",     |
 |    approve: true}     |
```
高风险变更先过审，teammate 等到批准才执行。

### 统一结构：同一个 有限状态机（Finite State Machine）

两个协议用完全相同的状态机：

```
pending → approved
pending → rejected
```

`req_id` 把请求和响应关联起来，发起方可以同时有多个待审请求，不会混淆。

### 关键要点

- **软约束**：teammate "判断高风险操作需要审批"靠 system prompt 引导，模型自己决定什么算高风险。
- **硬约束**：关机握手是代码层面的——不收到 `shutdown_response` 就不会退出线程。
- 一个 Request-Response 模式可以套用到任何需要协商的场景，不只是这两种。

---

## s11 — Autonomous Agents（自治 Agent）

### 核心思想

s09-s10 的 agent 需要 lead 手动指派任务。s11 让 agent 自己扫描任务看板、自己认领、做完再找下一个——自组织，不需要领导逐个分配。

### Agent 生命周期

```
spawn → WORK → IDLE（轮询 5s/次，最多 60s）→ SHUTDOWN
                 ↑                    ↓
          收到收件箱消息         60s 无任务 → 自动关机
          或认领到新任务
```

每个阶段都有明确的触发条件，agent 有完整的生命周期管理。

### 自动认领逻辑

扫描 `.tasks/`，找满足三个条件的任务：`pending` 状态 + 无 owner + `blockedBy` 为空：

```python
if (task.get("status") == "pending"
        and not task.get("owner")
        and not task.get("blockedBy")):
    unclaimed.append(task)
```

依赖关系（`blockedBy`）天然防止 agent 认领还没解锁的任务。

### 身份重注入

s06 的 context 压缩会把 messages 替换成摘要，agent 可能"忘了自己是谁"。s11 的修补：检测 messages 长度，过短说明发生了压缩，在开头重新插入身份块：

```python
if len(messages) <= 3:
    messages.insert(0, {"role": "user",
        "content": "<identity>You are 'alice', role: coder...</identity>"})
```

这是两个章节机制叠加产生的副作用，s11 在这里做了修补。

### 关键要点

- s07 的任务图（`blockedBy`）在这里发挥了作用——agent 自治认领时自动尊重依赖顺序。
- 自动关机是资源管理设计，不让空闲 agent 永远占着线程。

---

## s12 — Worktree Task Isolation（Worktree 任务隔离）

### 核心思想

s11 的所有 agent 共享同一个目录，并行修改同一文件会互相污染。s12 用 Git worktree 给每个任务分配独立目录和独立分支，彻底隔离执行环境。

### 两个平面

```
控制平面（.tasks/）          执行平面（.worktrees/）
  管"做什么"                   管"在哪做"
  任务状态、依赖、owner  <-->   独立目录、独立分支
              task_id 双向绑定
```

### 文件结构

```
.tasks/task_1.json          ← 控制平面：status, worktree="auth-refactor"
.worktrees/
  index.json                ← worktree 注册表
  events.jsonl              ← 生命周期事件流
  auth-refactor/            ← 独立工作目录（branch: wt/auth-refactor）
  ui-login/                 ← 独立工作目录（branch: wt/ui-login）
```

### 核心工具

- `worktree_create`：调用 `git worktree add -b wt/<name>`，同时绑定任务
- `worktree_run`：在指定 worktree 目录里执行命令（`cwd=worktree_path`）——这是隔离的核心
- `worktree_remove`：删除目录 + 可选标记任务完成，**不自动合并分支**
- `worktree_keep`：保留目录，分支留在 git 历史，由人工决定后续合并

### 事件流

每个生命周期步骤写入 `events.jsonl`，崩溃后可从磁盘重建现场：

```
worktree.create.before/after/failed
worktree.remove.before/after/failed
worktree.keep
task.completed
```

### 关键要点

- **没有自动合并**：s12 解决的是写入冲突（并行改同一文件），不是合并冲突。分支合并留给人工或更上层机制。
- **agent loop 回归最简**：复杂性全部下沉到 `WorktreeManager` 和 `TaskManager`，loop 本身和 s02 一样干净。
- **会话记忆是易失的，磁盘状态是持久的**：这是贯穿 s06→s12 的核心设计原则。
- 和 s04 的设计哲学一脉相承：s04 隔离 context，s12 隔离文件系统——**隔离工作区，只在边界处交换结果**。

