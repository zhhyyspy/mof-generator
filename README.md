# MOF M1 Generator — AI辅助建模工具

基于 MOF 元模型体系（M3→M2→M1→M0）的数据治理工具。通过AI从业务文档中自动提取M1领域模型，并可进一步反推M2元模型，帮助架构师从"逐字段逐属性手工建模"的工作中解放出来，专注于结构决策和质量审核。

---

## 核心理念

```
业务文档 (PDF/Excel/DOCX/TXT/MD/CSV)
    │
    ▼  AI提取M1
M1 领域模型（具体设备/属性/关联）
    │
    ▼  （可选）AI反推M2
M2 元模型（抽象通用基类）
    │
    固定基础: M3 元元模型（Class/Attribute/Association/DataType/...）
```

- **M3**：固定的建模语言（Class、Attribute、Association、DataType、Enumeration、Multiplicity、Constraint、Package）
- **M2**：通用业务对象类型的抽象（如"设备"基类）
- **M1**：领域特定模板（如"抽水蓄能机组台账 v1.0"）
- **M0**：运行时实例数据（不在本工具范畴内）

---

## 主要功能

### 1. AI辅助提取 M1 模型
- **多文档批处理**：上传N份业务文档（Excel、PDF、DOCX、TXT、MD、CSV），AI自动识别实体类型、属性、关联、枚举
- **全量数据 + 上下文累积**：每份文档完整发送给AI（不截断），后续批次带上前面已发现的实体作为上下文，确保跨文档实体关联一致性
- **并行处理**：实体发现和属性提取同时并行3路调用，大幅提速
- **智能JSON修复**：AI返回的JSON如有格式问题（缺逗号、多引号、括号不平衡等）自动修复，无需LLM重试

### 2. M1 → M2 反推
- 从已生成的M1模型中抽象出通用基类
- 自动识别共性属性合并到M2基类
- 自动为M1类标注继承关系（parent_class_name）

### 3. 可视化建模

#### 三层模型 + 关系图
- **M3元元模型**：固定10个核心概念卡片展示
- **M2元模型**：AI推导的抽象基类
- **M1领域层**：具体业务模板
- **关系图**：SVG连线展示M3→M2→M1的推导关系，M1类与M2基类的继承映射

#### 双视图模式
- **层级视图**（默认）：按继承+组合嵌套显示类关系
  ```
  [C] PumpedStorageUnit (extends Equipment)
    ◆ 包含 (composition)
      [C] WaterTurbine [1..1] via unitContainsTurbine
      [C] GeneratorMotor [1..1] via unitContainsGenerator
      [C] BallValve [0..*] via unitContainsBallValve
  ```
- **平铺视图**：所有类平铺列出

#### 多M1并行管理
支持多个平行M1模型共存（如"抽水蓄能台账 v1.0"和"电化学储能台账 v1.0"都是M1，互为兄弟），通过顶部下拉框切换，带数字徽章显示总数。

### 4. 审查面板（人工审核后才保存）
- AI提取完成后**不自动保存**
- 弹出审查面板：
  - 统计卡片（类/属性/关联/枚举数量）
  - 推导过程摘要
  - AI注意事项
  - 可勾选的实体列表（可展开查看详情）
  - 自定义M1模型名称
- 用户确认后才持久化到磁盘

### 5. 实时进度面板
- **文档解析进度**：逐个文档加载状态
- **5阶段流水线**：解析文档 → 识别实体 → 提取属性 → 分析关联 → 保存模型
- **并行子任务显示**：每个并发AI调用独立显示（排队/运行/完成）
- **LLM对话流**：可展开查看每次AI调用的完整prompt和原始响应
- **心跳提示**：长AI调用期间动态显示阶段性描述
- **可最小化**：提取过程中可切到后台，右下角浮动徽章继续显示进度
- **可中止**：随时点击停止按钮优雅中止（不会崩溃服务器）

### 6. LLM 多提供商支持
内置9种LLM提供商预设：
- Anthropic (Claude)
- OpenAI (GPT-4o, o1, o3)
- Azure OpenAI
- DeepSeek
- 智谱GLM
- 月之暗面Kimi
- 通义千问Qwen
- Ollama（本地部署）
- 自定义OpenAI兼容接口

可保存多个配置、一键切换、测试连通性。

### 7. LLM 调用统计
- 总调用次数、成功率、平均响应时间、估算Tokens
- 按模型/提供商分组统计
- 最近20条调用明细
- 调用趋势柱状图

### 8. 验证与导出
- **M3规范校验**：检查数据类型合法性、枚举引用、关联端点、多重性等
- **导出格式**：JSON、YAML、MOF文本（人类可读的伪代码格式）
- **版本管理**：M1模型支持版本快照

---

## 技术栈

**后端**
- Python 3.10+
- FastAPI + Uvicorn
- Pydantic 2.x（数据模型）
- anthropic / openai SDK（LLM调用）
- pdfplumber（PDF解析）
- python-docx（DOCX解析）
- openpyxl（Excel解析）

**前端**
- 原生 HTML/CSS/JavaScript（无框架、无构建）
- ES Modules
- SVG（关系图绘制）

**存储**
- 本地JSON文件（`backend/data/`）
- 无数据库依赖

---

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 启动服务
```bash
python run.py
```

服务默认运行在 `http://localhost:8000`

### 3. 配置LLM
浏览器打开 `http://localhost:8000`，点击右上角 `⚙ LLM`：
1. 点击"+新增"
2. 选择提供商（如 DeepSeek / 通义千问 / Anthropic）
3. 填入 API Key 和模型名（有预设自动填充）
4. 点击"测试连接"验证
5. 点击"保存"并激活

### 4. 使用流程
1. 左侧面板拖入业务文档（支持批量上传）
2. 点击 `⚙ AI提取M1` 开始提取
3. 观察进度面板（可最小化继续工作）
4. 提取完成后审查结果，勾选要保留的实体
5. 填写M1模型名称，点击"确认导入"
6. 在中间面板查看M1模型树（可切换层级/平铺视图）
7. 可选：点击 `↑ 反推M2` 从M1抽象出M2元模型
8. 点击 `↧ 导出` 导出 JSON/YAML/MOF 文本

---

## 项目结构

```
E:\MOFGenerator\
├── run.py                        # 启动入口
├── requirements.txt              # Python依赖
├── backend/
│   ├── app.py                    # FastAPI应用
│   ├── config.py                 # 配置（API Key路径等）
│   ├── models/                   # Pydantic数据模型
│   │   ├── m3_schema.py          # M3元元模型
│   │   ├── m2_template.py        # M2模板定义
│   │   ├── m1_model.py           # M1模型结构（含版本）
│   │   ├── llm_config.py         # LLM配置模型
│   │   └── api_schemas.py        # API请求/响应
│   ├── services/
│   │   ├── document_parser.py    # 多格式文档解析
│   │   ├── ai_extractor.py       # 核心AI提取逻辑（批处理+并行+上下文）
│   │   ├── llm_client.py         # 统一LLM调用层
│   │   ├── llm_stats.py          # 调用统计
│   │   ├── model_validator.py    # M3规范校验
│   │   ├── model_exporter.py     # 多格式导出
│   │   └── version_manager.py    # 版本管理
│   ├── routers/                  # API路由
│   │   ├── documents.py          # 文档上传管理
│   │   ├── extraction.py         # AI提取任务（含并行/中止/心跳）
│   │   ├── models.py             # M1/M2模型CRUD
│   │   ├── m2_templates.py       # M2模板+M3固定定义
│   │   ├── export.py             # 导出接口
│   │   └── llm_config.py         # LLM配置+统计
│   ├── storage/
│   │   └── file_store.py         # JSON文件存储
│   └── data/                     # 运行时数据
│       ├── documents/            # 上传的文档
│       ├── models/               # 保存的M1/M2模型
│       ├── m2_templates/         # 预置M2模板
│       ├── m3_fixed.json         # M3固定定义
│       ├── llm_providers.json    # LLM配置
│       └── llm_stats.json        # 调用统计
└── frontend/
    ├── index.html                # 单页应用
    ├── css/style.css             # 样式
    └── js/
        ├── api.js                # API客户端
        └── app.js                # 应用逻辑
```

---

## 性能优化

从最初的**2小时/18文件**优化到**~5-10分钟**，关键改进：

| 优化项 | 效果 |
|---|---|
| 小批次 (25K chars/call) | 单次调用从50K tokens降到12K tokens |
| 3路并行 (asyncio.gather + Semaphore) | 实体发现/属性提取并行 |
| 本地JSON修复 (10轮迭代) | 不再触发LLM重试双倍调用 |
| 上下文累积 | 后续批次知道已发现的实体，避免重复提取 |

---

## 架构亮点

### AI提取核心流程
```
1. 文档按大小分批（每批≤25K chars，单文件不截断）
2. 实体发现：
   - 第1批串行（获取初始实体上下文）
   - 后续批次并行（3路并发，共享第1批上下文）
   - 结果合并去重
3. 属性提取（每类批 × 每文档批的矩阵式遍历）：
   - 对每3个类的一批
   - 依次遍历所有文档批
   - 每次AI调用告知"已有这些属性"，让AI只报新的或更详细的
4. 关联提取（类似属性，跨文档上下文累积）
```

### 中止机制（三层防御）
- **前端**：设置 `_pollAborted` 标志立即停止轮询
- **后端**：设置 `_cancel_flags` 标志，每批次检查点自动退出
- **不使用 `task.cancel()`**：避免在HTTP请求中途打断httpx/openai SDK导致崩溃

### JSON修复（多重策略）
1. 清理 markdown 代码围栏 / 注释 / 尾逗号 / 缺失逗号
2. 迭代修复报错位置的引号问题（最多10轮）
3. 自动补全不平衡的括号
4. 正则提取最外层JSON对象

---

## 已知限制

- 依赖LLM的理解能力：不同模型提取质量差异明显，建议使用Claude Sonnet 4 / GPT-4o / Qwen-Plus 级别模型
- 无数据库：所有数据存本地JSON，不适合多用户生产环境（是设计选择）
- 中文优先：prompts和UI针对中文业务场景优化

---

## License

MIT
