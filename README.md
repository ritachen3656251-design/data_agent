# Data Agent

数据分析 Agent：根据用户问题执行数据查询，生成结构化洞察与自然语言回答。

## 功能概览

- 描述型：单日/多日核心指标（UV、PV、买家数）、漏斗转化率、留存、日活
- 诊断型：为什么转化/UV/买家下降？支持两日对比（如「12月1日到2日」）
- 归因型：哪些类目导致买家变化、拖累/拉动

## 架构

```
用户问题 → Mapper(LLM+规则) → slots(intent, dt, days)
                ↓
         Planner(LLM+规则) → plan(calls, plots)
                ↓
         Tools(DB 查询) → results
                ↓
         Narrator(LLM) → 自然语言回答
```

- Mapper：意图识别、日期/天数抽取，LLM 失败时规则回退
- Planner：根据 slots 生成执行计划，优先 LLM，规则层做安全校验
- Tools：PostgreSQL 查询（daily_metrics、类目贡献等）
- Narrator：将 answer_obj 转为可读文本，诊断类输出因果分析

## 项目结构

```
data_agent/
├── agent/           # 编排层：orchestrator、main、starter
├── mapper/          # 意图解析：用户问题 → intent + dt/days
├── planner/         # 规划：slots → plan (calls + plots)
├── tools/           # 数据与工具：db、tools、plot_tools
├── narrator/        # 回答生成：narrator、analyzer
├── memory/          # 会话记忆
├── evals/           # 评估脚本与数据
├── docs/            # 文档
├── data/            # 数据文件
├── scripts/         # 脚本
├── logs/            # 日志
├── main.py          # 根入口
├── requirements.txt
└── .env.example
```

## 快速开始

### 安装

```bash
pip install -r requirements.txt
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY 和数据库连接
```

### 运行

```bash
python main.py
```

启动后输入问题，例如：

- `最近9天核心指标趋势？`
- `为什么12月1日到2日的买家数变化？`
- `12月3日哪些类目导致买家下降？`

## 评估

```bash
# 评估指标（意图、参数、模板、边界、幻觉）
python -m evals.run_eval_metrics

# 多轮对话评估
python -m evals.run_eval_multiturn

# 回归测试（mapper + planner）
python -m evals.run_all_regression
```

## 其他命令

```bash
# 数据库检查
python scripts/inspect_db.py
```

## 数据

`data/UserBehavior.csv` 因体积过大（约 3.5GB）未纳入仓库。若需本地运行，请将数据文件放入 `data/` 目录。

## 依赖

- Python 3.10+
- pandas, matplotlib, sqlalchemy, psycopg2-binary
- dashscope（阿里云百炼，Qwen-Max）
