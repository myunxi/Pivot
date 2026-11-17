# Direct 模式下的 Super-agent 编排层：方案草案

- 状态：**Draft for review**
- 日期：2026-11-05
- 范围：`feature/direct-mode` 分支；只改 direct 模式，不引入云能力
- 参考：DeepSeek《Super-agent 设计探索》及其原型 `dsh-superagent-prototype`、卡兹克 Agent-Autoloop
- 代码基线：`affc715 fix(web): keep transcript viewport when sending from history`
- 说明：本文只是方案，未改任何代码；仓库工作区干净

---

## 0. Review 指南

先看这四节，它们决定了后面所有设计：

1. **§2 三个硬约束** — 如果其中任何一条不可接受，方案需要重做。
2. **§3 现状与边界** — 确认我对 direct 模式的理解没有偏差。
3. **§5 决策清单** — 每一条都是可投票的；不同意的直接标出来。
4. **§14 待决问题** — 需要你拍板的业务选择。

其余章节（数据模型、流程、API、路线图）都是这四节的推导，可以后看。

---

## 1. 目标与非目标

### 目标

- 在 direct 模式（单机、本地 SQLite、Tailscale 直连、无云账号）上增加一层**持久化编排能力**：
  - 事件 → 工单 → 派发 → worker 会话 → 结构化回报 → 审批 → 收件箱
- 手机与电脑通过同一个 Web 应用管理所有工单和会话，不另建 UI。
- 进程重启后工单状态不丢，worker 可恢复或重试。
- 人能在一个收件箱里看到"现在需要我做什么"。

### 非目标

- 不做多租户、不做团队协作、不做共享。
- 不引入 Postgres / Alembic / 云端 API / 推送服务。
- 不做多机聚合；每台机器各自运行一个 direct 实例。
- 不做公网 webhook 入口（Tailscale Funnel 明确禁用）。
- 不在第一阶段引入 LLM 路由器。

---

## 2. 三个硬约束

| # | 约束 | 后果 | 建议应对 |
|---|---|---|---|
| C1 | **电脑必须常开** | 电脑睡眠 = 编排器停止；手机端看到的是最后状态 | 用常开的 Mac mini / 台式机；`launchd` 开机自启；必要时 `caffeinate` |
| C2 | **外部 webhook 进不来** | Tailscale Serve 只对 tailnet 开放，GitHub / 飞书无法回调 | 本地轮询（`gh api` / API token）；只接受 tailnet 设备与本机脚本事件 |
| C3 | **没有真推送** | 无 APNs / FCM；iOS PWA 无法后台推送 | 站内收件箱 + 角标 + 前台 Web Notification；桌面端可加 macOS 本地通知 |

C1 是产品形态问题，不只是部署问题：如果用户预期"手机随时能派活"，就必须有一台永不睡眠的机器。C2/C3 决定了第一阶段的事件源和提醒方式。

---

## 3. 现状与边界

### 3.1 运行形态

```
手机 / 浏览器
   │  Tailscale Serve (TLS, tailnet-only)
   ▼
127.0.0.1:8765  FastAPI (local_server/app.py)
   ├─ /api/v1/*   本地 REST 子集
   ├─ /ws         user / session / machine 三种作用域
   └─ 其它 HTTP   反向代理给受监管的 Next 进程
        │
        ├─ local_server/store.py    SQLite (~/.vicoa/direct/store.db)
        │    agent_instances / messages / attachments / recent_directories
        │
        └─ MachineDaemon（进程内 RPC 处理器）
             spawn-session / resume / worktree / files / git / pty
                  │
                  ▼
             headless CLI agent
```

关键文件：

- `backend/src/vicoa/local_server/app.py` — 本地 FastAPI，1639 行
- `backend/src/vicoa/local_server/store.py:196` — `_create_tables`，目前只有四张表
- `backend/src/vicoa/local_server/runner.py` — 启动本地服务、Next 监管、daemon
- `backend/src/vicoa/local_server/rpc.py` — `LocalRpcDispatcher`
- `backend/src/vicoa/machine_daemon.py:2688` — `spawn-session` 处理
- `apps/web/lib/direct-mode-routes.ts` — 云路径屏蔽名单
- `apps/web/app/dashboard/dashboard-layout.tsx:92` — `IS_DIRECT_WEB` 导航门控
- `docs/guides/direct-mode.md` — 现有运维文档

### 3.2 可直接复用的能力

| 能力 | 现有实现 |
|---|---|
| 会话创建 | `spawn-session`，支持 `resume`、`worktree.new`、模型/权限 metadata |
| 消息投递 | `add_user_message`、队列语义、`/steer`、`/cancel` |
| 实时观察 | user-scoped WS 接收任意 `entity` 的 update |
| 工作区隔离 | `git-worktree-create` / worktree 参数 |
| 终端与文件 | pty、文件浏览、附件 |
| 认证 | 单一 Bearer nonce + Tailscale ACL |

### 3.3 缺失的能力

- tasks / projects / automations / agents 在 direct 下都是空壳端点
- 无事件表、无规则、无工单状态机、无审批、无审计
- 无消息级幂等键
- 无 watchdog

### 3.4 边界声明

编排层只应存在于 `run_local_only_daemon`（direct）路径。登录版 desktop 的本地 store 保持空，不要启用编排器，避免两套状态源。

---

## 4. 目标架构

```
事件源（direct 下只有四类）
  ① 人在 web / 手机新建工单
  ② 本地定时器
  ③ 本机脚本或命令（curl 本地端点 / vicoa CLI）
  ④ 轮询源（git / GitHub / 本地文件）
                    │
                    ▼
            ┌───────────────┐
            │  event 表      │  验签、归一化、去重
            └──────┬────────┘
                   ▼
            ┌───────────────┐
            │  规则匹配      │  确定性、可 dry-run
            └──────┬────────┘
                   ▼
            ┌───────────────┐
            │  ticket 表     │  状态机 + 授权（Mandate）
            └──────┬────────┘
                   ▼
            ┌───────────────┐  spawn-session
            │  dispatcher   │──────────────▶ worker session
            └──────┬────────┘                （独立 worktree）
                   │                               │
                   │      report / ask / done      │
                   │◀──────────────────────────────┘
                   ▼
            ┌───────────────┐
            │ approval 表    │──▶ web 收件箱 / 审批
            └───────────────┘
```

设计原则：

1. **会话不承载状态，工单承载状态。** 会话丢了可以重建，工单不能丢。
2. **一工单一会话一 worktree。** 上下文隔离、可并行、可恢复。
3. **规则优先，不上 LLM Router。** direct 的事件源基本是人；人就是最准的路由器。
4. **所有决策落审计。** "为什么派了这个活"必须可查。
5. **单写者。** 工单状态只允许一个类写。

---

## 5. 决策清单（逐条 review）

| # | 决策 | 选择 | 备选 | 理由 | 状态 |
|---|---|---|---|---|---|
| D1 | 编排器位置 | daemon 进程内 | 独立进程 / Next 内 | 需要调 `spawn-session`，且必须随 daemon 生死 | Proposed |
| D2 | 存储 | 独立 SQLite 库 `orchestrator.db` | 复用 `store.db` / Postgres | direct 无 Postgres；独立库避免抢写锁 | Proposed |
| D3 | 迁移方式 | `PRAGMA user_version` 加法迁移 | Alembic | direct 不走云端迁移链 | Proposed |
| D4 | 路由 | 确定性规则 | LLM Router / 固定流程 | 单机事件量小；可审计、零成本 | Proposed |
| D5 | 执行单元 | 一工单一 session + 一 worktree | 复用长会话 | 防上下文污染，便于并行与恢复 | Proposed |
| D6 | worker 回报 | CLI 调本地 REST | MCP / 解析最后一条回复 | spawn 已注入 token 与实例 ID，CLI 零改造 | Proposed |
| D7 | 通知 | 站内 + 前台通知 | 自建推送 / ntfy | 无云即无真推送；先接受限制 | Proposed |
| D8 | 多机 | 暂不做 | 聚合层 | 每台机器各跑一个 direct | Proposed |
| D9 | 工单与 Task | 新建 ticket 表 | 复用/改造 Task | direct 下 Task 是空壳；语义不同 | Proposed |
| D10 | 审批 token | P1 校验实例归属，P3 再拆 scoped token | 立即做 scoped token | 先挡住误调用，再谈隔离 | Proposed |

---

## 6. 数据模型（草案）

独立库：`~/.vicoa/direct/orchestrator.db`
连接参数：`journal_mode=WAL`、`busy_timeout=5000`、`foreign_keys=ON`
时间格式沿用 `store.py` 的 wire 格式：`YYYY-MM-DDTHH:MM:SS.ffffffZ`

```sql
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS ticket (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    intent            TEXT NOT NULL,
    stage             TEXT NOT NULL,          -- idle|queued|working|blocked|failed|waiting_person|ended
    stage_detail      TEXT,                    -- answer|review|delivery|...
    priority          INTEGER NOT NULL DEFAULT 50,
    project_dir       TEXT NOT NULL,
    worktree_path     TEXT,
    branch            TEXT,
    assigned_agent    TEXT,
    session_config    TEXT,                    -- JSON
    source            TEXT,
    source_ref        TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    ended_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ticket_stage ON ticket (stage, priority, updated_at);

CREATE TABLE IF NOT EXISTS ticket_input (
    id            TEXT PRIMARY KEY,
    ticket_id     TEXT NOT NULL REFERENCES ticket(id),
    source        TEXT NOT NULL,
    dedupe_key    TEXT NOT NULL,
    content       TEXT NOT NULL,
    mode          TEXT NOT NULL DEFAULT 'queue',  -- queue|steer|inject
    created_at    TEXT NOT NULL,
    delivered_at  TEXT,
    UNIQUE (ticket_id, dedupe_key)
);

CREATE TABLE IF NOT EXISTS assignment (
    id                 TEXT PRIMARY KEY,
    ticket_id          TEXT NOT NULL REFERENCES ticket(id),
    agent_instance_id  TEXT,
    attempt            INTEGER NOT NULL DEFAULT 1,
    state              TEXT NOT NULL,          -- dispatched|running|done|failed|lost
    dispatch_key       TEXT NOT NULL UNIQUE,
    lease_until        TEXT,
    first_message_id   TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assignment_ticket ON assignment (ticket_id, attempt);

CREATE TABLE IF NOT EXISTS approval (
    id                     TEXT PRIMARY KEY,
    ticket_id              TEXT NOT NULL REFERENCES ticket(id),
    category               TEXT NOT NULL,      -- merge|deploy|destructive|spend|external|review
    payload                TEXT NOT NULL,      -- JSON
    status                 TEXT NOT NULL,      -- pending|approved|denied|expired
    requested_by_instance  TEXT,
    decided_at             TEXT,
    decided_via            TEXT,               -- web|cli
    created_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approval_pending ON approval (status, created_at);

CREATE TABLE IF NOT EXISTS event (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    external_id  TEXT,
    type         TEXT NOT NULL,
    dedupe_key   TEXT NOT NULL UNIQUE,
    payload      TEXT NOT NULL,                -- JSON
    received_at  TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS rule (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 100,
    enabled     INTEGER NOT NULL DEFAULT 1,
    match       TEXT NOT NULL,                 -- JSON
    action      TEXT NOT NULL,                 -- JSON
    last_hit_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    entity     TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    type       TEXT NOT NULL,
    data       TEXT,                           -- JSON
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit (entity, entity_id, seq);
```

### stage 优先级

收件箱排序与"卡在哪"的判断统一用一个函数：

```
ended > waiting_person(answer|review|delivery)
      > blocked|failed
      > working
      > queued
      > idle
```

### 与会话库的关系

- `assignment.agent_instance_id` 指向 `store.db` 的 `agent_instances.id`，**跨库不设外键**。
- 派发时由 `spawn-session` 启动 agent，agent 自注册后产生实例行；这不是一个可原子化的事务，因此编排层必须先写 assignment（`dispatched`），再补实例 ID（`running`）。
- 工单详情页通过 `agent_instance_id` 复用现有会话组件。

### 可选增强（P1）

给 `store.db` 的 `messages` 增加：

```sql
ALTER TABLE messages ADD COLUMN idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_idempotency
    ON messages (agent_instance_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
```

用于派发重试时不重复投递首条 assignment。

---

## 7. 核心流程

### 7.1 建单

来源：web 表单、`vicoa ticket new`、规则命中、本地脚本 POST event。

必填：`title`、`intent`、`project_dir`。
可选：`priority`、`assigned_agent`、`worktree`、`source_ref`。

建单后直接进入 `queued`（或被规则置为 `waiting_person` 等人工确认）。

### 7.2 派发

```
ticket(stage=queued)
  → 选择 agent（本地 catalog；可来自规则或用户）
  → spawn-session(
        directory=project_dir,
        worktree={new: true, name: "ticket-<id8>"},
        agent=...,
        metadata={model, permission_mode, ...},
        resume={...}?          # 仅重试/恢复
    )
  → assignment(state=dispatched, dispatch_key=...)
  → 首条消息 = 结构化 assignment + 人可读摘要
  → ticket(stage=working)
```

首条消息示例：

```json
{
  "ticket_id": "01J...",
  "title": "修复登录页在 Safari 上的白屏",
  "assignment": "定位并修复……",
  "context": {"project_dir": "...", "branch": "ticket-abc123"},
  "constraints": {"do_not_push": true, "max_files": 20},
  "reply_contract": {
    "report": "vicoa ticket report --ticket <id> --state working --summary ...",
    "ask": "vicoa ticket ask --ticket <id> --question ... --category review",
    "done": "vicoa ticket done --ticket <id> --summary ... --branch ..."
  }
}
```

正文前附一段人可读摘要。worker 读字段，人读摘要。

### 7.3 追加输入

- 空闲：`add_user_message`，`mode=queue`
- 运行中且需要纠偏：现有 `/steer`
- 需要补上下文但不打断：`mode=inject`（P2）

所有输入先写 `ticket_input`，再投递；投递成功后写 `delivered_at`。这样即使 daemon 崩溃，也能知道哪些输入没送到。

### 7.4 worker 回报

CLI（读取现有的 `VICOA_API_KEY` 与 `VICOA_AGENT_INSTANCE_ID`）：

```bash
vicoa ticket report --ticket <id> --state working --summary "..."
vicoa ticket ask    --ticket <id> --question "..." --category review
vicoa ticket done   --ticket <id> --summary "..." --branch ticket-abc123
vicoa ticket fail   --ticket <id> --reason "..."
```

服务端校验：`agent_instance_id` 必须是该工单当前 assignment 的实例，否则 403。

### 7.5 审批

```
worker: vicoa ticket ask --category merge --payload '{"branch": "..."}'
  → approval(pending)
  → 在目标会话插入 requires_user_input 消息
  → web 收件箱出现卡片
人：approve / deny
  → approval(decided)
  → 结果作为用户消息投递给 worker
  → ticket(stage) 推进
```

审批分类建议：`merge`、`deploy`、`destructive`、`spend`、`external`、`review`。

### 7.6 完成与结束

- `done`：记录 summary、branch、产物；`ticket(stage=waiting_person/review)` 或直接 `ended`（取决于规则）。
- `fail`：`stage=failed`，按策略重试或转人工。
- 结束时不自动删 worktree；保留分支供 review/合并。清理作为显式操作（P3）。

### 7.7 恢复

loop 启动时扫描 `assignment.state in (dispatched, running)`：

1. 实例进程仍存活 → 重新挂上，不重发首条消息。
2. 进程已退出、工单未结束 → `spawn-session` 带 `resume.agent_instance_id` 重开同一实例行；有 `agent_session_id` 时一并带上。
3. 连续失败 ≥ N → `ticket(stage=blocked)`，进收件箱。

> 待验证：本地 `instance_metadata` 是否保留了 agent 自己的 `session` handle；如果没有，恢复只能保证"同一实例行"，不能保证"同一段对话历史"。

---

## 8. API 草案

全部挂在现有 `/api/v1/orchestrator/*`，沿用 Bearer nonce，不新增端口。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/events` | 本机脚本 / tailnet 设备投递事件 |
| GET | `/events` | 事件列表（调试） |
| POST | `/tickets` | 建单 |
| GET | `/tickets` | 列表，支持 `stage`、`limit`、`offset` |
| GET | `/tickets/{id}` | 详情（含 inputs、assignments、approvals、audit） |
| POST | `/tickets/{id}/inputs` | 追加输入 |
| POST | `/tickets/{id}/stop` | 停止 worker |
| POST | `/tickets/{id}/retry` | 重新派发 |
| POST | `/tickets/{id}/approvals` | 创建审批（主要给 worker CLI） |
| POST | `/tickets/{id}/approvals/{aid}/decision` | 人工批准/拒绝 |
| POST | `/worker/report` | worker 状态回报 |
| GET | `/rules` / PUT `/rules/{id}` | 规则管理 |
| GET | `/audit` | 审计查询 |
| GET | `/summary` | 收件箱计数与健康状态 |

WS：新增 `ticket`、`approval` 两个 entity 的 update，形状与现有 envelope 对齐。user-scoped 连接无需改动即可收到。

---

## 9. Web 端

新增：

```
/dashboard/orchestrator
  /inbox
  /tickets
  /tickets/[id]
  /approvals
  /rules
  /audit
```

改动点：

- `dashboard-layout.tsx`：新增一个仅 `IS_DIRECT_WEB` 显示的导航项。
- **不要**把 `/dashboard/orchestrator` 加进 `DIRECT_CLOUD_PATH_PREFIXES`。
- 工单详情内嵌现有 session 聊天组件（传 `instance_id`），复用转录、diff、终端。
- 手机首屏是收件箱；审批按钮大；点卡片进工单详情。
- `/summary` 用于角标与首页统计。

---

## 10. 安全边界

现状：direct 只有一个 nonce，`spawn-session` 通过 `VICOA_API_KEY` 把它传给每个 worker。因此 worker 能调用所有本地 REST，包括审批接口。

分阶段：

- **P1**：worker 回报/提问接口校验实例归属；审批记录 `decided_via`。挡误调用，不挡恶意。
- **P3**：给每个 runner 发派生 token `HMAC(nonce, instance_id)`，只能访问自己工单的读写接口；审批必须用主 token。此时"agent 不能自己批准自己"才真正成立。

必须保持：

- 不开 Tailscale Funnel。
- 所有新端点都要求 Bearer。
- `vicoa-key.` WebSocket 仍只允许 loopback。
- 不在日志里输出 token。

---

## 11. 可靠性

| 机制 | 阶段 | 说明 |
|---|---|---|
| 事件去重 | P0 | `event.dedupe_key` 唯一 |
| 派发幂等 | P1 | `assignment.dispatch_key` + message idempotency |
| 租约 | P3 | `lease_until`，过期由 watchdog 处理 |
| 重试 | P3 | 指数退避，上限后转人工 |
| 审计账本 | P1 | 所有状态变更写 `audit` |
| effect/outbox | P3 | 有外部副作用的动作先落库再执行 |
| watchdog | P3 | 超时、卡死、连续失败、daemon 掉线 |

低风险阶段可以先用"每次 tick 重新计算"代替完整事件溯源；审计表保证可回放即可。

---

## 12. 路线图

| 阶段 | 工作量 | 交付 | 验收标准 |
|---|---|---|---|
| P0 骨架 | 3–5 天 | orchestrator 包、建表、loop、手动建单、规则匹配、spawn worker、`vicoa ticket report`、只读收件箱 | 手机上建单 → 电脑起 worker → 工单页看到汇报 |
| P1 闭环 | 1 周 | 结构化 assignment、ask/approve、审批页、消息幂等、steer/queue 接入工单 | 一张单从 queued 走到 ended，中途审批一次 |
| P2 事件源 | 1 周 | 定时器、本地命令入口、git 轮询、GitHub 轮询、线程/去重 | 同一条 issue 多次更新归并到一张单 |
| P3 韧性 | 1 周 | watchdog、租约、失败升级、审计回放、per-session token | 杀掉 worker 后自动 resume |
| P4 可选 | 按需 | LLM 兜底分诊、多项目规则、并发配额 | 规则未命中时给出可解释决策 |

---

## 13. 不建议做的事

- 不把 Postgres / Alembic / `user_id` 那套搬进 direct。
- 不在事件量还小时上 LLM Router。
- 不为"看起来完整"去实现 tasks/automations 的云端语义。
- 不开 Tailscale Funnel。
- 不让编排器跑在 Next 进程里。
- 不另开第二个端口或第二套前端。
- 不把所有工单塞进一个长会话。

---

## 14. 待决问题（需要你拍板）

1. **常开机器**：哪台机器负责跑 direct？是否接受"电脑睡眠时编排器停摆"？
2. **事件源**：P0 只做"手动建单 + 定时器"是否够？GitHub 轮询是否必须？
3. **worker 会话可见性**：worker session 是否出现在现有会话列表里，还是默认隐藏、只在工单详情里看？
4. **审批边界**：哪些动作必须人工（merge / deploy / 删除 / 外发）？是否有可自动批准的类别？
5. **worktree 生命周期**：完成后自动合并分支，还是一律等人 review？何时清理？
6. **并发配额**：同时最多几个 worker？是否需要按项目限制？
7. **通知期望**：手机端"打开 app 才看到待办"是否可接受？是否需要桌面 macOS 通知兜底？
8. **CLI vs MCP**：worker 回报先做 CLI 是否可接受，还是必须直接上 MCP？

---

## 15. 待验证的技术点

- [ ] `instance_metadata` 是否保留 agent 自己的 session handle，决定恢复粒度
- [ ] 本地 daemon 重启后，已派发的 runner 进程是否仍存活、能否重连
- [ ] iOS PWA 在安装模式下能否使用 `Notification`（非 push）
- [ ] SQLite 部分唯一索引与当前 Python `sqlite3` 版本兼容性
- [ ] `spawn-session` 的 `worktree.new` 在 direct 模式下是否需要额外的前置检查（未提交改动、非 git 目录）
- [ ] user-scoped WS 在手机切后台/重连后的 catch-up 行为是否满足工单实时性

---

## 16. 与 DeepSeek 原型的映射

| 原型概念 | 本方案 |
|---|---|
| Source / Trigger | `event` + 四类事件源 |
| Policy / Admission | `rule` + 审批分类 |
| Decision | 规则动作（暂无 LLM Decision） |
| Mandate | `ticket.intent` + 审批约束 |
| Ticket | `ticket` |
| Assignment | `assignment` |
| worker Session | `spawn-session` 启动的本地会话 |
| Report / Reply | `vicoa ticket report` / `done` |
| Effects / Outbox | `effect`（P3） |
| Engine 单写者 | `OrchestratorStore` 单写入口 |
| Watchdog | `loop.py` 周期任务（P3） |

不照搬的部分：多源插件体系、main session 评估器、跨渠道回复规范、多机调度。这些在单机单用户场景下没有收益。

---

## 17. 下一步

1. Review 本文，尤其是 §2、§5、§14。
2. 确认后写 ADR，固化 D1–D5。
3. 落 `orchestrator/store.py` 建表与 `PRAGMA user_version` 骨架。
4. 做 P0 PoC：建单 → `spawn-session` → assignment → `vicoa ticket report` → 收件箱可见。
