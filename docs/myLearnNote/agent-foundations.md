# Agent 基础概念延伸笔记

从 learn-claude-code 仓库出发，向更广泛的 agent 设计概念延伸。

---

## 第一性原理：模型的本质局限

模型本身没有记忆，每次调用都是全新的——你给它什么它就看到什么，不给它就不知道。

所以所有"记忆"、"知识"问题，本质都是同一个问题：

> **怎么决定把什么信息放进 context？**

后续所有机制（Memory、RAG、压缩、按需加载）都是在回答这个问题。

---

## Memory vs RAG

两者经常被混淆，但解决的是不同子集：

| | Memory | RAG |
|---|---|---|
| 解决的问题 | 过去发生的事怎么保留和利用 | 外部知识库怎么按需注入 |
| 信息来源 | agent 自己的经历（对话、任务、用户行为） | 外部静态知识（文档、代码库、手册）|
| 核心挑战 | context 有限，怎么选择性保留/压缩/遗忘 | 知识库太大，怎么找到最相关的片段 |

**一句话：**
- Memory：agent 自己经历过的事 → 怎么记住
- RAG：外部已有的知识 → 怎么找到并用上

---

## Memory 系统架构

### 四种记忆类型

```python
class AgentMemory:
    def __init__(self):
        self.short_term  = []               # 短期记忆：对话上下文
        self.long_term   = VectorStore()    # 长期记忆：向量存储
        self.experience  = ExperienceReplay() # 经验记忆：强化学习
        self.working     = TaskState()      # 工作记忆：当前任务状态
```

对照仓库：

| 记忆类型 | 对应仓库机制 | 说明 |
|---|---|---|
| 短期记忆 | messages 列表 | 当前对话上下文，用完即丢 |
| 工作记忆 | s07 `.tasks/` 磁盘文件 | 当前任务状态，持久化 |
| 中期记忆 | s06 压缩摘要 | 跨多轮但不永久（仓库的扩展概念）|
| 长期记忆 | 向量数据库（仓库未涉及）| 重要信息 embedding 后存储，按需检索 |
| 经验记忆 | 仓库未涉及 | 强化学习，从成功/失败中改变行为策略，与其他记忆本质不同——前三种是"存信息"，经验记忆是"改行为" |

### 关键组件
- **对话历史管理**：决定保留哪些、压缩哪些、丢弃哪些
- **状态跟踪器**：工作记忆的持久化（对应 s07）
- **记忆压缩/遗忘机制**：对应 s06 的三层压缩
- **记忆检索和关联**：按需取出相关记忆注入 context（对应 s05 的按需加载思路）
- **经验回放缓冲池**：强化学习专用，仓库未涉及

---

## RAG 系统架构

```python
class RAGSystem:
    def __init__(self):
        self.doc_processor = DocumentProcessor() # 文档处理流水线
        self.embedder      = EmbeddingModel()    # 向量化模型
        self.vector_db     = VectorDatabase()    # 向量数据库
        self.retriever     = Retriever()         # 检索器
        self.reranker      = Reranker()          # 重排器
```

### 执行流程

```
用户提问
    ↓
对问题做 embedding（向量化）
    ↓
retriever：在向量库里找语义最近的文档片段（粗筛）
    ↓
reranker：对候选结果二次评分排序（精排）
    ↓
把最相关的片段注入 context
    ↓
模型基于注入的知识回答
```

### 关键组件说明

- **doc_processor**：文档分割和清洗，把大文档切成适合检索的小块
- **embedder**：把文字转成向量，语义相近的内容在向量空间里距离也近
- **vector_db**：存储所有向量，支持快速相似性搜索
- **retriever**：粗筛，找出候选相关片段
- **reranker**：精排，对候选结果重新评分，把真正有用的排到前面
- **多跳检索**：复杂问题需要多次检索，每次检索结果引导下一次查询

### 类比 s05 的 skill 加载

- `get_descriptions()` = retriever 粗筛（知道有哪些内容）
- `get_content()` = reranker 精取（拿到真正需要的内容）

本质都是同一个哲学：**不一次性全部注入，用的时候才取。**

---

## MCP：统一 Tool 的"长相"

MCP（Model Context Protocol）是在 function call 能力之上建的**标准化协议**，规定了 tool 的统一格式：叫什么名字、接受什么参数、返回什么格式、怎么注册和发现。

仓库里每个项目都要自己写 `TOOLS` 数组 + `TOOL_HANDLERS` 字典。MCP 把这套东西标准化，变成生态——一次写好，任何支持 MCP 的模型和框架都能直接用。

类比：MCP 之于 Tool，就像 REST 规范之于 HTTP——协议本来就能传数据，REST 只是约定了统一的"长相"。

---

## 三种经典 Agent 范式

| 范式 | 思考与行动的关系 | 适用场景 |
|---|---|---|
| ReAct | 交替进行：想一步做一步 | 动态环境，需要实时感知 |
| Plan-and-Solve | 先整体规划，再逐步执行 | 目标明确、环境稳定的任务 |
| Reflection | 执行后回头审查，发现问题修正 | 需要自我改进、质量要求高的任务 |

**对应仓库：**
- ReAct → s01 的 agent loop（走一步看一步）
- Plan-and-Solve → s03 TodoWrite + s07 任务图（先拆解再执行）
- Reflection → s10 计划审批（提交 → 审查 → 修改）

### 混合范式架构（以智能家居为例）

```
用户指令
    ↓
Plan-and-Solve 层：拆解高层目标为子任务列表
    ↓
ReAct 层：执行每个子任务，实时感知环境变化
    ↓
Reflection 层：定期评估结果，学习用户习惯，优化默认策略
```

实际生产系统几乎都是混合范式，关键是**按需分配复杂度**。

---

*持续更新中...*
