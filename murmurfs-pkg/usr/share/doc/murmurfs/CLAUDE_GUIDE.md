# MurmurFS — Claude 理解指南

> 本文件专为 AI（Claude/Codex/Gemini 等）设计，帮助你在零上下文情况下理解 MurmurFS 的设计意图和使用方式。

## 一句话概括

**MurmurFS 是一个让 AI Agent 存储「意图」而非「内容」的 FUSE 文件系统。**

## 核心概念

### Intent Stack（意图栈）

每个文件不是存储实际代码，而是一个意图栈：

```
v1: 实现用户认证模块，JWT方式
v2: 砍掉session，只保留bearer token
v3: 增加refresh token逻辑，过期时间15分钟
```

当你需要实际代码时，调用 `sync`，LLM 读取所有意图层并生成最终文件。

### 三种操作

| 操作 | 成本 | 效果 |
|------|------|------|
| **write** | 免费（无 LLM） | 追加一层意图 |
| **squash** | 便宜（小 LLM 调用） | 压缩多层意图为一层 |
| **sync** | 昂贵（大 LLM 调用） | 生成实际文件内容 |

### 文件系统布局

```
project/
├── murmur/          # FUSE 挂载点 — 模糊视图（intent stack）
├── real/            # 同步后的真实文件
└── .murmurfs/       # 元数据存储
    ├── manifest.yaml   # 意图层、同步状态
    └── config.yaml     # 项目配置
```

## Claude 如何使用 MurmurFS

### 场景 1: 规划阶段 — 用 write 存意图

当用户说「帮我写一个认证模块」，不要直接写代码。先存储意图：

```bash
murmurfs write src/auth.py "实现用户认证模块，JWT方式"
murmurfs write src/auth.py "砍掉session，只保留bearer token"
```

**好处：**
- 不消耗 token 生成完整代码
- 保留你的推理过程
- 用户可以审查意图后再决定是否 sync

### 场景 2: 迭代阶段 — 用 squash 压缩

当意图层超过 5 层时，压缩它们：

```bash
murmurfs squash src/auth.py
```

**好处：**
- 减少 manifest.yaml 的大小
- 降低后续 sync 的 token 消耗
- 合并重复/矛盾的意图

### 场景 3: 实现阶段 — 用 sync 生成代码

当用户确认意图后，生成实际代码：

```bash
murmurfs sync src/auth.py
```

**好处：**
- 只在确认后才消耗大 token
- 生成的代码基于完整的意图历史
- 可以通过 `--mock` 测试流程

### 场景 4: 冲突解决 — 用 merge

多个 Agent 写了矛盾的意图：

```bash
murmurfs merge src/auth.py
```

## Token 优化策略

### 传统方式（无 MurmurFS）
```
Claude: 我来帮你写认证模块
[生成 200 行代码，消耗 ~2000 tokens]
用户: 不对，只要 JWT
Claude: 好的，我重写
[重新生成 180 行代码，消耗 ~1800 tokens]
用户: 再加个 refresh token
Claude: 好的
[再次生成 200 行，消耗 ~2000 tokens]
总计: ~5800 tokens
```

### MurmurFS 方式
```
Claude: 先存个意图
murmurfs write src/auth.py "实现用户认证模块，JWT方式"  [0 tokens]
用户: 不对，只要 JWT
murmurfs write src/auth.py "砍掉session，只保留bearer token"  [0 tokens]
用户: 再加个 refresh token
murmurfs write src/auth.py "增加refresh token逻辑"  [0 tokens]
用户: 好了，生成吧
murmurfs squash src/auth.py  [~200 tokens]
murmurfs sync src/auth.py    [~500 tokens]
总计: ~700 tokens（节省 88%）
```

## 读取 MurmurFS 项目

当你遇到一个使用 MurmurFS 的项目：

1. **读 `.murmurfs/manifest.yaml`** — 了解所有文件的意图栈
2. **读 `.murmurfs/config.yaml`** — 了解项目配置
3. **`murmurfs list`** — 查看所有文件和层数
4. **`murmurfs status`** — 项目整体状态
5. **`murmurfs read <file>`** — 查看特定文件的意图栈

## 关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 意图格式 | 纯文本 | 简单，LLM 友好 |
| 元数据存储 | 旁路 YAML 文件 | 人类可读，可版本控制 |
| 冲突处理 | LLM 决定，保留所有层 | 不丢失任何意图 |
| 同步后行为 | 保留意图历史 | 支持审计和回滚 |
| 目录列表 | 合并真实+意图文件 | Agent 看到完整项目愿景 |

## 完整工作流示例

```bash
# 1. 初始化项目
murmurfs init my-project -d "A web backend"

# 2. 写意图（免费）
murmurfs write src/auth.py "实现JWT认证" -f "支持登录登出，token过期15分钟"
murmurfs write src/auth.py "增加refresh token" -f "双token机制"
murmurfs write src/db.py "PostgreSQL连接池" -f "使用asyncpg，连接池大小20"

# 3. 查看状态
murmurfs status
murmurfs list

# 4. 压缩（便宜）
murmurfs squash src/auth.py

# 5. 生成代码（昂贵，确认后再做）
murmurfs sync src/auth.py
murmurfs sync --all

# 6. 挂载 FUSE（可选）
murmurfs mount ./murmur ./real
```

## 配置说明

配置文件 `.murmurfs/config.yaml`：

```yaml
auto_squash_threshold: 5    # 超过5层自动压缩
sync_on_squash: false        # 压缩后不同步

llm:
  model: "gpt-4o"            # 或任何 OpenAI 兼容模型
  base_url: "https://api.openai.com/v1"
  api_key_env: "MURMURFS_LLM_API_KEY"
  timeout: 60
  max_retries: 3
```

环境变量覆盖：
- `MURMURFS_LLM_API_KEY` — LLM API 密钥
- `MURMURFS_LLM_BASE_URL` — API 基础 URL
- `MURMURFS_LLM_MODEL` — 模型名称

## 依赖

- Python ≥ 3.10
- fusepy ≥ 3.0（FUSE 挂载需要）
- pyyaml ≥ 6.0
- click ≥ 8.0
- requests ≥ 2.28
- libfuse（系统包，FUSE 需要）

## 我是 Claude，我应该如何使用？

1. **规划阶段用 write**，不要直接生成代码
2. **定期 squash**，保持 manifest 精简
3. **确认后 sync**，只在用户批准时生成
4. **读 manifest** 理解项目结构，而不是读真实文件
5. **用 status** 快速了解项目状态
6. **保留意图历史**，不要跳过意图直接 sync
