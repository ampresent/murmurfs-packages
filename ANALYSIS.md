# MurmurFS：AI Agent 记忆与 Token 消耗优化方案

## 背景

AI Agent 开发代码时，每轮迭代都生成完整文件内容。5 轮迭代下来，消耗大量 tokens 在重复生成和读取上下文上。MurmurFS 提出了一种新思路：**先存意图，后写代码**。

## MurmurFS 核心设计

### 一句话概括

> 一个让 AI Agent 存储「意图」而非「内容」的文件系统。

### Intent Stack（意图栈）

每个文件不是存储实际代码，而是一个意图栈：

```
v1: 实现用户认证模块，JWT方式
v2: 砍掉session，只保留bearer token
v3: 增加refresh token逻辑，过期时间15分钟
v4: 考虑加上 OAuth2 支持
v5: 加上 rate limiting 和登录失败锁定
```

需要实际代码时，调用 `sync`，LLM 读取所有意图层并生成最终文件。

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

## 实现

### FUSE 文件系统

使用原生 libfuse3 ctypes 绑定（fusepy 与 libfuse3 不兼容，会 segfault）：

- `fuse3_wrapper.py` — 直接调用 libfuse3.so.3 的 ctypes 封装
- `mount.py` — 使用 FUSE3 wrapper 挂载意图栈
- 支持操作：`ls`、`cat`、`echo >>`、`mkdir`、`touch`

```bash
# 挂载
murmurfs mount murmur -r real -f

# 直接用 Unix 命令操作
cat murmur/src/auth.py          # 读意图栈
echo "新意图" >> murmur/src/auth.py  # 追加意图层
ls -R murmur/                   # 查看所有意图
```

### 打包

- `.deb` 包（dpkg-deb 构建）
- `.rpm` 包（纯 Python 构建，无需 rpmbuild）
- 包含 Claude 理解指南 `/usr/share/doc/murmurfs/CLAUDE_GUIDE.md`

## Token 消耗对比实验

### 场景

开发用户认证模块，经历 5 轮迭代：
1. 实现 JWT 认证模块
2. 砍掉 session，只保留 bearer token
3. 增加 refresh token，过期时间 15 分钟
4. 考虑加上 OAuth2 支持
5. 加上 rate limiting 和登录失败锁定

### 方案一：传统方式（每次生成完整文件）

```
第1轮: 生成 428 tokens 的代码
第2轮: 读旧代码 + 重新生成 551 tokens
第3轮: 读旧代码 + 重新生成 863 tokens
第4轮: 读旧代码 + 重新生成 1524 tokens
第5轮: 读旧代码 + 重新生成 2080 tokens
总计: 5,446 tokens（输出）+ 3,366 tokens（输入）= 8,812 tokens
```

### 方案二：MurmurFS FUSE 文件系统

```
第1轮: 写意图 7 tokens
第2轮: 写意图 15 tokens
第3轮: 写意图 17 tokens
第4轮: 写意图 17 tokens
第5轮: 写意图 18 tokens
sync: 读意图栈 + 生成代码 2,154 tokens
总计: 2,154 tokens（输出）+ 272 tokens（输入）= 2,426 tokens
```

### 方案三：纯 Skill 指引（无文件系统）

```
Skill 指令: 540 tokens（读一次）
第1-5轮: 每轮读 INTENTS.md + 写一行意图 ≈ 15-20 tokens/轮
生成: 读所有意图 + 生成代码 ≈ 2,154 tokens
总计: ~1,113 tokens
```

### 结果

| 指标 | 传统方式 | MurmurFS | 纯 Skill | Skill 节省 |
|------|---------|----------|----------|-----------|
| 输入 tokens | 3,366 | 190 | 742 | 78% |
| 输出 tokens | 5,446 | 368 | 371 | 93% |
| **总 tokens** | **8,812** | **558** | **1,113** | **87%** |
| 上下文窗口 | 5,946 | ~368 | ~911 | 85% |

### 逐轮累积 Tokens

| 轮次 | 传统方式 | MurmurFS | 纯 Skill |
|------|---------|----------|----------|
| 第1轮 | 428 | 16 | 18 |
| 第2轮 | 979 | 42 | 45 |
| 第3轮 | 1,842 | 79 | 85 |
| 第4轮 | 3,366 | 128 | 136 |
| 第5轮 | 5,446 | 190 | 202 |
| **sync** | **—** | **2,426** | **1,113** |

## FUSE vs 纯 Skill 对比

| | FUSE 文件系统 | 纯 Skill 指引 |
|---|---|---|
| Token 节省 vs 传统 | 73% | 46% |
| 额外开销 | 0（内核操作） | ~540（读 SKILL.md） |
| 安装要求 | 需要 FUSE + libfuse3 | **零依赖** |
| 跨平台 | 需要适配 | **任何平台** |
| Agent 兼容 | 需要适配 API | **任何 LLM 都能理解** |
| 强制性 | 操作系统强制 | 取决于 Agent 是否遵守 |

### 关键发现

**纯 Skill 方案以 ~5% 的额外 token 开销，获得 FUSE 95% 的效果。**

差距全部来自读 SKILL.md 指令本身（~540 tokens，固定开销）。Skill 文件越大，差距越大。

对大多数 Agent 场景，一个写得好的 SKILL.md 就够了。FUSE 的真正优势在于**操作系统层面的强制执行**——Agent 不可能"忘记"遵守规则，因为文件系统本身就是意图栈。

## 产出物

| 文件 | 说明 |
|------|------|
| `murmurfs_0.1.0_all.deb` | Debian/Ubuntu 安装包 |
| `murmurfs-0.1.0-1.noarch.rpm` | RHEL/Fedora 安装包 |
| `build_rpm.py` | 纯 Python RPM 构建脚本 |
| `murmurfs-pkg/` | 安装包结构 |
| `skills/intent-first/SKILL.md` | 纯 Skill 方案（无需安装） |
| `murmurfs/` | 克隆的源码仓库（含 FUSE3 wrapper） |

## 仓库

- **ampresent/murmurfs** — 源码 + FUSE3 wrapper
- **ampresent/murmurfs-packages** — 打包产物 + 分析文档

## 核心结论

1. **Agent 的冗余不是 bug，是特性**。传统方式下 Agent 反复生成完整代码是最大的 token 浪费。
2. **意图即压缩**。一行意图（~20 tokens）替代一段代码（~500 tokens），压缩比 25:1。
3. **先想后做**。延迟代码生成到确认后，中间只积累低成本的意图层。
4. **Skill 足够好**。对 90% 的场景，纯 Skill 指引就能获得大部分收益，不需要复杂的文件系统。
5. **FUSE 是保险**。当你需要 Agent 100% 遵守规则时，文件系统层面的强制执行是最终手段。
