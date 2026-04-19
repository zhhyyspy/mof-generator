# MOF M1 Generator — AI 辅助建模工具

基于 MOF 元模型体系（M3→M2→M1→M0）的数据治理工具。通过 AI 从业务文档中自动提取 M1 领域模型，并可进一步反推 M2 元模型，帮助架构师从"逐字段逐属性手工建模"的工作中解放出来，专注于结构决策和质量审核。

---

## 核心理念

```
业务文档 (PDF/Excel/DOCX/TXT/MD/CSV)
    │
    ▼  AI 提取 M1  (单趟扫描 · 并行分批)
M1 领域模型（具体设备/台账/报告/计划 ...）
    │
    ▼  AI 反推 M2  (3 阶段抽象 · 扁平元类)
M2 元模型（业务主题契约 · 动态层级）
    │
    固定基础: M3 元元模型（Class/Attribute/Association/DataType/Enumeration/Multiplicity）
```

| 层级 | 内容 | 可变性 |
|------|------|--------|
| **M3** 元元模型 | Class / Attribute / Association / DataType / Enumeration / Multiplicity / Constraint / Package | 固定不可变 |
| **M2** 元模型 | 业务主题契约（如"设备台账 / 会务资料 / 专题报告"）| AI 推导 + 人工确认 |
| **M1** 领域模型 | 具体领域类（如"抽水蓄能设备台账 v1.0"）| AI 提取 + 人工编辑 |
| **M0** 实例数据 | 业务系统里的实际台账记录 | 本工具不涉及 |

---

## 设计原则

1. **M2 严格扁平单层** — 不允许 M2 内部分层（如把"设备台账"拆成"机电设备台账 / 建筑设备台账"）。差异永远下沉到 M1 层解决，保证业务分析能跨类型汇总。
2. **两个正交维度** — M2 不在"专业类型维度"上分裂（机电/建筑/闸门），但**可以**在"层级角色维度"上通过 `level` 枚举 + 自关联表达多层结构（设施 → 功能分组 → 设备 → 部件）。
3. **上下文连续的分批** — 大文档必须分批送 AI，但每一批都带着之前批次发现的实体/属性列表作上下文，保证命名一致、不重复提取。
4. **可视 + 可编辑 + 可审查** — 模型产出后必须有直观的图形呈现、属性级别的编辑入口，以及给业务人员评审的工具链。

---

## 主要功能

### 1 · M1 提取（AI · 单趟合并）

- **所见即所得的批处理** — 每份文档完整送 AI（不做摘要/截断），按 `batch_max_chars` 切批（默认 8000，可调到 2000-40000）
- **单次调用获取 类 + 属性 + 枚举** — 原本要 `N_classes × N_doc_batches` 次调用（约 15,000 次），现在每个文档批只调 1 次（约 100-200 次），提速 **~75×**
- **3 路并行 + 序列种子** — 首批串行获得初始实体上下文，后续批次共享该上下文并发跑（3 并发 + 锁保护合并）
- **容错 JSON 解析** — 10 轮迭代修复 AI 返回的缺逗号/多引号/括号失衡/尾随文本等问题，不触发 LLM 重试
- **层级线索捕获** — AI 在提取时如识别到"该类属于 XX 层"这类提示，会附带 `hierarchy_hint` 元数据传给 M2 推导阶段
- **关联提取并行** — 跨文档批次并发抽取类间 composition / aggregation / association 关系

### 2 · M2 推导（AI · 3 阶段扁平抽象）

```
Phase 1  业务观测维度聚类     1 次调用
         (按"业务分析会把它们放一起查"分组, 不是按命名/属性相似度)
         → 15-40 个业务主题

Phase 2  组内抽象                N 次调用 (3 并行)
         每组产出 1 个扁平 M2 基类 + 共性属性 + self-associations
         差异属性留 M1, 禁用 Entity/Object 等空泛名

Phase 2.5  层级结构探测          N 次调用 (3 并行)
         每个 M2 基类判断是否有纵向包含层级
         有则动态产出 level 枚举 (如 [设施, 功能分组, 设备, 部件])
         同时分配每个 M1 子类的 default level

Phase 3  跨组去重                1 次调用
         语义实质相同的 M2 基类合并, 合并后仍扁平
```

- **M2 范围选择器** — 推导前弹出可视化卡片勾选界面，按业务域手动缩小范围
- **保留自关联** — M2 带 `contains Parent/Children` 松散自关联（aggregation），允许业务树任意层跨挂载
- **M1 回写** — 保存 M2 时自动给对应 M1 类追加带默认值的 `level` 属性（`is_inherited=true`）

### 3 · 实体编辑器（带级联预览）

- 全字段可编辑：名称、标签、描述、父类下拉、是否抽象
- **属性表** 内联编辑：名称 / 标签 / 数据类型 / 单位 / 枚举引用 / 多重性
- **关联表** 内联编辑：出向关联支持增改删；入向关联只读，点击源类名跳转过去编辑
- **父类跨层** — M1 类可以选 M2 类作为父类，下拉带中文标签 + `[M1]`/`[M2]` 徽章
- **继承属性自动重算** — 换父类后，继承属性列表立刻按新父类链重新生成
- **实时变更概览** — 右侧侧栏显示类字段/属性/关联的变更统计
- **预览 → 确认** — 提交前弹预览弹窗展示完整 diff（含级联：重命名类会显示哪些子类 `parent_class_name`、哪些关联 `class_name` 会被自动同步）
- **原子提交** — 一次 `PUT /models/{id}` 整包替换，后端 Pydantic 校验

### 4 · 关系图交互画布

- **两种布局**（顶部工具栏切换）
  - 分层布局（默认）— M2 在顶部一行，M1 按父类分网格；孤儿 M1 在右侧密集网格
  - 力导向 — 节点互斥 + 边吸引 + **硬位置碰撞校正**（不重叠）+ O(n) 空间哈希
- **无限 SVG 画板** — 鼠标拖动平移，滚轮以鼠标位置为原点缩放 (15%-400%)
- **节点** — 圆球渐变，M2 紫色、M1 蓝色，大小随属性数；右上角属性数徽章；下方类标签
- **hover 弹窗** — 显示类名/标签/描述/属性统计/父类/子类列表（点击跳转）
- **选中高亮** — 仅相邻节点 + 连接边保持不透明，其余全部暗化到 15%
- **跨层联动** — 点 M2 类详情能看到所有 M1 子类及其层级分布
- **小地图 + 搜索 + 层级标签筛选** — 顶部工具栏
- **键盘**：`F` 适应画布 · `0` 100% 缩放 · `L` 重新布局 · `/` 焦点搜索 · `Esc` 取消选中

### 5 · 审查包导出（给业务人员评审的完整包）

```
审查包_xxx_20260419.zip
├── 审查报告.docx              叙述式报告 (已嵌入 PNG 图, 业务人员直接能看)
├── 审查意见表.xlsx             5 个 sheet 的结构化反馈表 (下拉框 + id 列)
├── 审查图集.pdf                多页静态 PDF (任意阅读器能打开, 中文嵌入)
├── 审查图集.drawio             多页可编辑图 (draw.io 打开, 支持审查图章)
├── 图片快照/*.svg              单页 SVG (浏览器/Word 365 可视)
├── 附件_源数据.json            完整源数据 (给开发人员)
└── 使用说明.md                 带 UTF-8 BOM, Notepad 可读
```

- **带阶段动画的进度条** — 生成时显示 5 个阶段 chip（准备数据 → Word → Excel → 图集 → PDF 打包）+ 指数衰减进度曲线
- **CJK 字体全链路** — 自动注册 Microsoft YaHei 到 reportlab，强制 override 所有 SVG 文本节点的 fontName（绕过 svglib 内部映射），保证 PDF 和 Word 里中文都不变 ■■■
- **RFC 5987 中文文件名** — 保证浏览器下载的 zip 文件名不乱码

### 6 · 多文档、多 M1 并行管理

- 支持任意多个 M1 模型共存（如"抽蓄台账 v1.0" 和 "电化学台账 v1.0" 互为兄弟）
- 顶部下拉切换，带数字徽章显示总数
- 每个 M1 可绑定 0 或 1 个 M2 元模型

### 7 · LLM 多提供商支持

内置 9 种预设，一键切换：

| 提供商 | 说明 |
|--------|------|
| Anthropic (Claude) | Opus / Sonnet / Haiku |
| OpenAI | GPT-4o / o1 / o3 |
| Azure OpenAI | 企业部署 |
| DeepSeek | deepseek-chat / deepseek-reasoner |
| 智谱 GLM | glm-4-plus / glm-4 |
| 月之暗面 Kimi | moonshot-v1-32k/128k |
| 通义千问 Qwen | qwen-max / qwen-plus |
| Ollama | 本地推理 |
| 自定义 | 任何 OpenAI 兼容的 API |

每个配置可独立设置 `batch_max_chars`（解决本地模型中文 tokenization 效率低的场景）。

### 8 · LLM 调用统计

- 总调用次数、成功率、平均响应时间、估算 Tokens
- 按模型 / 提供商分组统计
- 最近 20 条调用明细（含 prompt/response 预览）
- 调用趋势柱状图

### 9 · 其他

- **M3 规范校验** — 数据类型合法性、枚举引用、关联端点、多重性
- **M1 版本管理** — 快照与还原
- **LLM 对话流** — 提取/推导过程中可实时展开每次 AI 调用的完整 prompt + 原始响应
- **失败批次重试** — 某批 AI 调用失败时单独标记，完成后可一键重试失败批次
- **可最小化进度面板** — 提取过程中切后台继续工作，右下角浮动徽章显示进度
- **实体级别取消** — 随时中止任务，已完成的类/属性保留在成果区等待保存

---

## 技术栈

### 后端

- Python 3.10+（推荐 3.12-3.14）
- FastAPI + Uvicorn (with StatReload)
- Pydantic 2.x
- anthropic SDK + openai SDK（通过统一的 `llm_client.py` 抽象）
- pdfplumber · python-docx · openpyxl（文档解析）
- reportlab + svglib（PDF / PNG 渲染，CJK 字体嵌入）
- aiofiles

### 前端

- 纯原生 HTML / CSS / JavaScript，**无构建无框架**
- ES Modules
- SVG（关系图、关系图集渲染）
- 仅一个 `api.js` 和一个 `app.js`（~5000 行），浏览器直接加载

### 存储

- 本地 JSON 文件（`backend/data/`）
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

浏览器打开 http://localhost:8000

### 3. 配置 LLM

右上角 `⚙ LLM` → `+ 新增`：

1. 选服务商（预设自动填 Base URL / 模型名）
2. 填 API Key
3. 可调节 `max_tokens` / `超时` / `单批字符上限`
4. `⚡ 测试连接` 验证
5. 保存并激活

**本地模型（LM Studio / Ollama）**：如果用 `qwen3.6-plus` 这类中文 tokenization 效率较低的模型，建议把 `单批字符上限` 调到 4000 以避免 context 溢出。

### 4. 典型工作流

```
上传业务文档 (左侧面板)
  ↓
⚙ AI 提取 M1
  ↓  观察进度 / 看 LLM 对话流 / 可最小化
  ↓
审查面板 勾选要保留的实体 → 确认导入
  ↓
↑ 反推 M2 → 范围选择器勾选参与的 M1 类 → 执行 3 阶段推导
  ↓
⟶ 关系图 观察层级 / 拖动节点 / 点球看详情
  ↓
任意类 ✎ 编辑  添加删改属性/关联 → 预览级联变更 → 确认保存
  ↓
⬇ 导出 → 📄 审查包 (.zip)  发给业务人员评审
```

---

## 项目结构

```
E:\MOFGenerator\
├── run.py                             # 启动入口
├── requirements.txt                   # Python 依赖
├── backend/
│   ├── app.py                         # FastAPI 应用 + 无缓存中间件
│   ├── config.py                      # 配置（数据目录、端口等）
│   ├── models/                        # Pydantic 数据模型
│   │   ├── m3_schema.py               # M3 元元模型（Class/Attribute/Association/...）
│   │   ├── m2_template.py
│   │   ├── m1_model.py                # M1 模型结构（含版本）
│   │   ├── llm_config.py              # LLM 配置
│   │   └── api_schemas.py             # API 请求/响应
│   ├── services/
│   │   ├── document_parser.py         # 多格式文档解析
│   │   ├── ai_extractor.py            # ★ 核心 AI 逻辑 — M1 单趟提取 + M2 三阶段推导
│   │   ├── llm_client.py              # 统一 LLM 抽象（anthropic + openai-compatible）
│   │   ├── llm_stats.py               # 调用统计
│   │   ├── model_validator.py         # M3 规范校验
│   │   ├── model_exporter.py          # 原始数据导出 (JSON/YAML/MOF Text)
│   │   ├── review_exporter.py         # ★ 审查包打包 (Word + Excel + zip)
│   │   ├── diagram_exporter.py        # ★ 图集生成 (drawio + SVG + PDF + PNG)
│   │   └── version_manager.py
│   ├── routers/                       # API 路由
│   │   ├── documents.py
│   │   ├── extraction.py              # 提取任务 (并行/中止/心跳/重试)
│   │   ├── models.py                  # M1/M2 CRUD + 版本 + 验证
│   │   ├── m2_templates.py
│   │   ├── export.py                  # 原始导出 + 审查包导出
│   │   └── llm_config.py              # LLM 配置 + 统计
│   ├── storage/
│   │   └── file_store.py              # JSON 文件存储
│   └── data/                          # 运行时数据 (已 gitignore)
│       ├── documents/                 # 上传的文档
│       ├── models/                    # 保存的 M1 / M2 模型
│       ├── m3_fixed.json              # M3 固定定义
│       ├── llm_providers.json         # LLM 配置
│       └── llm_stats.json             # 调用统计
└── frontend/
    ├── index.html                     # 单页应用
    ├── css/style.css                  # 样式 (~3700 行)
    └── js/
        ├── api.js                     # API 客户端
        └── app.js                     # 应用逻辑 (~5600 行)
```

---

## 性能对比（以 18 份业务文档、365 个 M1 类的真实场景为例）

| 维度 | 初版 | 当前版本 |
|------|------|----------|
| M1 提取总 LLM 调用数 | ~15,000（类 × 文档矩阵）| ~100-200（每文档批 1 次）|
| M1 提取耗时 | 理论 ~14 小时 / 实际超时 | **~10-15 分钟** |
| M2 推导总 LLM 调用数 | 1（巨型 prompt，多半爆上下文）| ~27（聚类 + N 组抽象 + 层级 + 去重）|
| M2 推导耗时 | 超时或退化输出 | **~3-4 分钟** |
| M2 类数量 | 1-5 个空壳 Entity/Object | 15-40 个业务主题化基类 |

---

## 关键架构

### 单趟 M1 提取核心流程

```
1. 文档按 batch_max_chars 切批（单文件过大则 overlap=500 字符滑窗分块）

2. 首批 (batch 0) 串行执行:
   LLM(doc_text, known_context=空) → {classes[含attrs], enumerations, hierarchy_hints}
   写入 merged_classes / merged_enums (加锁)

3. 后续批次 (batch 1..N) 3 路并行执行:
   对每批:
     snapshot = 当前 merged_classes (加锁读)
     LLM(doc_text, known_context=snapshot) → {classes[含attrs], ...}
     合并去重 (加锁写)

4. 关联提取 (3 路并行, 首批串行种子):
   对每 doc batch:
     snapshot = 当前 known_assoc_names (加锁读)
     LLM(doc_text, 所有 M1 classes, known_assocs=snapshot) → {associations}
     合并去重

5. 构建 MOFClass / Attribute / Association / Enumeration 对象
6. 生成最终 Package → save_model
```

### M2 三阶段推导核心流程

```
已有 M1 (可能 365 个类)
    │
    ▼
Phase 1 聚类 (1 次 LLM, 只传 M1 骨架 — name/label/desc/attrs-names)
    → 15-40 个业务组
    │ 硬约束:
    │   ❌ 禁用 Entity/Object/Item/Thing
    │   ❌ 禁止跨业务域强行合并 (设备 vs 文档)
    │   ❌ 禁止因专业差异拆分同业务的类 (机电/建筑/闸门都是"设备台账")
    │   ✓ 孤立类独立成组
    ▼
Phase 2 组内抽象 (N 次并行 LLM, 每组看完整 M1 详情)
    → 每组产出 1 个扁平 M2 类
    │ 共性属性 (≥ 50% 组内类拥有的才上升到 M2)
    │ 差异属性留 M1
    │ self-associations 指向自身 (保持混合子类的业务树)
    │ 本地校验: 禁用名替换, 漏掉的 M1 mapping 自动补齐
    ▼
Phase 2.5 层级结构探测 (N 次并行 LLM)
    对每个 M2 基类: 是否有纵向包含层级?
    │ 判定依据: M1 类名语义 + M1 属性里的 parent/所属字段 + hierarchy_hint
    │ ✓ 好: [设施, 功能分组, 设备, 部件]      (包含关系)
    │ ❌ 差: [机电, 水工, 闸门]              (专业分类, 非层级)
    │ 2-8 层合理, 超出 6 层视为过度细分
    │ 每个 M1 类分配一个 level 或标 "whole_tree"
    ▼
Phase 3 跨组去重 (1 次 LLM)
    仅合并业务含义实质相同的 M2 (如 "会议材料" ≈ "会务资料")
    合并后仍扁平, 绝不做多级抽象
    ▼
物化 M2 Package
    对有 hierarchy 的 M2:
      • 生成 Enumeration<ClassName>Level (literals = 发现的层级)
      • 给 M2 加 `level: Enum [1..1]` 属性
      • 加 contains{role}Parent/Children 自关联 (aggregation, 0..1 / 0..*)
    对扁平 M2: 只有基础共性属性
    │
    ▼
save-m2 触发时:
    对每个 M1 子类回写:
      • parent_class_name / parent_class_ref 指向 M2
      • 共名属性标 is_inherited=True
      • 追加 level 属性, default_value=分配的层级名 (whole_tree 不设默认)
```

### 审查包 CJK 字体处理链

```
1. module import 时执行 _register_cjk_font():
   扫描 C:/Windows/Fonts/msyh.ttc / PingFang.ttc / NotoSansCJK.ttc
   TTFont.register("CJKSans", first_match)

2. SVG 生成时:
   所有 <text> 元素 font-family='CJKSans,"Microsoft YaHei","Segoe UI",sans-serif'
   svglib 取第一个 → 命中 reportlab 注册表

3. svglib.svg2rlg() 后、renderPDF/renderPM 前:
   _force_font_recursive(drawing, "CJKSans")
   遍历 Drawing 树, 把每个 String 节点的 fontName 强制改成 CJKSans
   (svglib 内部字体映射不查 pdfmetrics registry, 必须手动 override)

4. 渲染:
   PDF 经 renderPDF.draw → 嵌入 MicrosoftYaHei subset
   PNG 经 renderPM.drawToString(fmt="PNG") → 嵌入到 docx word/media/

5. docx 的 .md 使用说明文件加 UTF-8 BOM (\ufeff), 保证 Windows Notepad 识别
```

### 中止机制（3 层防御）

- **前端** — 设置 `_pollAborted` 立即停止轮询
- **后端** — `_cancel_flags[task_id] = True`，各批次 `_check()` 优雅退出
- **不使用 `asyncio.Task.cancel()`** — 避免在 httpx/openai SDK 请求中途强制打断导致 event loop 崩溃

### JSON 容错修复（多策略）

1. 清理 markdown 代码围栏 / 注释 / 尾逗号 / 缺失逗号
2. 定位报错位置（`Expecting ',' delimiter` 等），尝试在该字符前插入缺失逗号或引号
3. 最多 10 轮迭代修复（每轮处理一个错误）
4. 自动平衡不匹配的花括号/方括号
5. 最后兜底正则提取最外层 JSON 对象

---

## 已知限制

- **强依赖 LLM 理解能力** — 建议用 Claude Sonnet 4 / GPT-4o / Qwen-Plus 级别模型；本地 8B 以下模型效果会明显退化
- **无数据库 · 单用户** — 所有数据落本地 JSON，不适合多人协同生产环境（这是设计选择）
- **中文业务场景优化** — prompts 和 UI 主要针对中文业务文档；英文场景需要调整 prompt 示例
- **审查反馈回收是手工的** — 业务人员填完 Excel 后需手动回录系统（导入反馈功能未做）
- **单次推导上限** — M2 推导依赖 LLM 能一次处理 M1 骨架 (~70KB)，如果 M1 类数 > 1000，可能需要进一步分批

---

## License

MIT
