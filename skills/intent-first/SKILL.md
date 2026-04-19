# Intent-First Skill

> 让 Agent 先存意图，后写代码。节省 60-70% token，保留完整推理历史。

## 核心规则

当你执行以下操作时，**必须先写意图，不要直接生成代码**：

1. 用户要求写/改代码 → 先在 `INTENTS.md` 追加意图层
2. 用户确认意图后 → 才生成代码到文件
3. 用户反馈修改 → 追加新意图层（不要重写代码）

## 操作流程

### 第一步：写意图（免费）

当用户说"帮我写 X"或"改一下 Y"时，不要直接写代码。改为：

```bash
# 在项目根目录维护 INTENTS.md
cat >> INTENTS.md << EOF
## file: path/to/file.py

- [意图摘要] 用户要求的一句话描述
  详细：具体实现方案、关键决策、约束条件
EOF
```

然后回复用户：意图已记录，确认后生成代码。

### 第二步：确认后生成（昂贵）

用户说"好"、"生成"、"可以"时：

1. 读 INTENTS.md 中对应文件的所有意图层
2. 综合所有意图生成代码
3. 生成后在意图层标记 `[已生成]`

### 第三步：迭代（追加，不重写）

用户要修改时：
- 追加新意图层到 INTENTS.md
- 不要重写已生成的代码
- 等用户说"重新生成"时才重做

## INTENTS.md 格式

```markdown
# Intent Stack

> 意图栈：先规划，后实现。每层意图消耗 ≈20 tokens，每轮代码生成 ≈500+ tokens。

## file: src/auth.py

- [v1] 实现JWT认证模块
  - 包含登录/登出接口
  - 使用 HS256 算法
  - Token 有效期 24 小时
- [v2] 砍掉session，只保留bearer token
  - 移除所有session相关代码
  - Authorization: Bearer <token> 格式
- [v3] 增加refresh token（15分钟access + 7天refresh）
  - 双token机制
  - refresh token 用 secrets.token_urlsafe 生成
- [已生成]

## file: src/db.py

- [v1] PostgreSQL连接池
  - asyncpg，池大小20
```

## Token 节省原理

| 传统方式 | Intent-First |
|---------|-------------|
| 每轮：读旧代码 + 生成新代码 ≈1500 tokens | 每轮：读意图栈 + 写一行意图 ≈100 tokens |
| 5轮迭代 ≈7500 tokens | 5轮意图 + 1次生成 ≈2000 tokens |
| 推理历史丢失 | INTENTS.md 保留完整历史 |
| 上下文窗口不断膨胀 | 意图栈远小于代码 |

## 关键指令

当这个 skill 激活时，你应该：

1. **默认不写代码**，先问"需要我现在生成还是先记下意图？"
2. **用 INTENTS.md 作为唯一真相源**，不要维护多份意图
3. **squash 意图**：超过 5 层时主动压缩为 1 层
4. **读意图栈理解项目**，不要读整个代码库
5. **sync 只在确认后**，生成时综合所有意图层
