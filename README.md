# MOF M1 Generator — AI 辅助建模工具

基于 MOF 元模型体系（M3→M2→M1→M0）的数据治理工具。通过 AI 从业务文档中自动提取 M1 领域模型，并可进一步反推 M2 元模型 (含 **元类 + 元结构**),帮助架构师从"逐字段逐属性手工建模"的工作中解放出来，专注于结构决策和质量审核。

**V3.1** 增加了 4 个质量强化功能,让真实业务文档抽取的 M1/M2 更接近"直接可交付"的质量: **(A) 文档类型感知抽取** · **(B) M1 composition 补边** · **(C) 同义类检测合并** · **(D) 质量体检**。

**V3.2** 提供**元结构可视化编辑器 + M1 级联影响预览**,让用户对 AI 抽出的 M2 元结构可以新建/编辑/删除,任何会影响 M1 的修改都会先预览 + 逐个决策,再通过原子事务保存。同时支持 M2 之间的普通关联(非层级边) CRUD 管理。

**V3.3** 面向业务用户的**可视化模型编辑 (Phase 1)**: 类卡片 hover 快捷按钮 + 图标化属性构建器 (6 种语义类型) + 向导式关联构建器 + 依赖感知的安全删除 + 3 组一键属性模板。抛弃"一堆技术参数表单",每个操作都是"所见即所得"。

---

## 核心理念

```
业务文档 (PDF/Excel/DOCX/TXT/MD/CSV)
    │
    ▼  AI 提取 M1  (单趟扫描 · 并行分批)
M1 领域模型（具体设备/台账/报告/计划 ...）
    │
    ▼  AI 反推 M2  (3 阶段抽象 · 识别元类与元结构)
M2 元模型（业务主题契约 + 层级元结构）
    │
    固定基础: M3 元元模型（Class/Attribute/Association/DataType/...）
```

| 层级 | 内容 | 可变性 |
|------|------|--------|
| **M3** 元元模型 | Class / Attribute / Association / DataType / Enumeration / Multiplicity / Constraint / Package | 固定不可变 |
| **M2** 元模型 | 业务主题契约 + 元结构 (Structural Pattern) | AI 推导 + 人工确认,支持发布生命周期 |
| **M1** 领域模型 | 具体领域类 (如"抽水蓄能电站 · 27 个领域类") | AI 提取 + 人工编辑,可随元结构分层分组 |
| **M0** 实例数据 | 业务系统里的实际台账记录 | 本工具不涉及 |

### V3.0 方法论: 元类 vs 元结构

- **元类 (Flat MetaClass)** — 单个 M2 Class,适合扁平业务概念 (如"会议纪要"、"合同文件")
- **元结构 (StructuralPattern)** — N 个 MetaClass + N-1 条层级 Association 组合成的可复用模板;表达典型的多层包含结构。例如:
  ```
  设施 (L1, root) ─contains─▶ 功能分组 (L2) ─contains─▶ 设备 (L3) ─contains─▶ 部件 (L4, leaf)
  ```
  每条层级 Association 带 `is_hierarchy=true` / `hierarchy_order=N`,由约束集 `{no_cycle, no_cross_level, no_reverse, root_fixed}` 保证结构完整性。

---

## 设计原则

1. **两个正交维度** — M2 不在"专业类型维度"上分裂 (机电/建筑/闸门合入同一类),但在"层级角色维度"上**通过元结构**显式表达 (设施 → 功能分组 → 设备 → 部件)。这样既保证跨类型业务分析能汇总,又保留了结构清晰。
2. **上下文连续的分批** — 大文档分批送 AI,但每批都带着之前批次发现的实体/属性列表作上下文,保证命名一致、不重复提取。
3. **可视 + 可编辑 + 可审查** — 模型产出后必须有直观的图形呈现 (元结构树图、卡片分层展示)、属性级别的编辑入口,以及给业务人员评审的工具链。
4. **迁移优先** — M1+M2 作为可搬运的整体,通过 `.mofpkg.zip` 完整包在不同系统间转移,保留所有依赖关系。
5. **发布生命周期** — M1/M2 有状态机 (`draft → review → published → deprecated`),防止"正式版被误改"。

---

## 主要功能

### 1 · M1 提取 (AI · 单趟合并)

- **所见即所得的批处理** — 每份文档完整送 AI (不做摘要/截断),按 `batch_max_chars` 切批 (默认 8000,可调到 2000-40000)
- **单次调用获取 类 + 属性 + 枚举** — 每个文档批只调 1 次,比旧版的 `N_classes × N_doc_batches` 矩阵调用提速 **~75×**
- **3 路并行 + 序列种子** — 首批串行获得初始实体上下文,后续批次共享该上下文并发跑 (3 并发 + 锁保护合并)
- **容错 JSON 解析** — 10 轮迭代修复 AI 返回的缺逗号/多引号/括号失衡/尾随文本等问题
- **层级线索捕获** — AI 在提取时如识别到"该类属于 XX 层"这类提示,会附带 `hierarchy_hint` 元数据传给 M2 推导阶段
- **关联提取并行** — 跨文档批次并发抽取类间 composition / aggregation / association 关系

### 2 · M2 推导 (AI · 3 阶段 · 元类 + 元结构)

```
Phase 1  业务观测维度聚类     1 次调用
         → 15-40 个业务主题

Phase 2  组内抽象                N 次并行
         每组产出 1 个候选 M2 基类 + 共性属性
         差异属性留 M1

Phase 2.5  元结构探测            N 次并行
         对每个候选主题判断:
         • 扁平 (无层级) → 产出 1 个元类 (FlatClass)
         • 多层级 → 产出 N 个 MetaClass + N-1 条 hierarchy Association
                    + 1 个 StructuralPattern 聚合 + 每层专属属性分配
         M1 类自动分配到具体层级 MetaClass (不再是 level 枚举)

Phase 3  跨组去重                1 次调用
         语义实质相同的 M2 主题合并,合并后仍保持元结构完整
```

- **M2 范围选择器** — 推导前弹出可视化卡片勾选界面,按业务域手动缩小范围
- **M1 回写** — 保存 M2 时自动给对应 M1 类设置具体的层级父类 (例如: `PumpTurbine` → `Equipment`, `PumpedStoragePlant` → `Facility`)
- **元结构完整性校验** — 验证 `is_hierarchy` 边数 = N-1、root 无入边、DFS 环检测、同层一致性

### 3 · M2 元结构面板 (M2 视图)

- **按元结构分组卡片** — 同层级 MetaClass 视觉上归为一组,色带区分 L1 橙 / L2 蓝 / L3 绿 / L4 紫
- **结构 inspector 橙色面板** — 展开显示 `L1-设施 → L2-功能分组 → L3-设备 → L4-部件` 的链式视图,列出每层的 MetaClass 参与者 + 属性数 + 约束集
- **点级别卡片** 在下方类视图中闪烁高亮定位
- **元结构名称可在线编辑** (点击标题 → 修改 → 💾 保存)

### 4 · M1 元结构面板 (M1 视图)

- **树形图呈现 composition 关系** — 每列 = 一个 M2 元结构层级,每节点 = 一个 M1 类,箭头 = composition association
  - 跨层边: 水平贝塞尔曲线
  - 同层边 (L2→L2 如"机组区域 contains 抽蓄机组"): 垂直箭头
- **分层 BFS 布局 + 父-Y 拉近**: 子节点尽量和父节点垂直对齐,父子相邻性视觉最优
- **M1 类卡片按层级分组展示** — cards 模式下不再扁平,按 L1/L2/L3/L4 分 subsection,彩色左边框 + 层级标签 + 元类名 + 计数
- **每个 M1 卡片** 显示左侧 3px 层级彩条 + 右上角 L1/L2/L3/L4 徽章

### 5 · 实体编辑器 (带级联预览)

- 全字段可编辑: 名称、标签、描述、父类下拉、是否抽象
- **属性表** 内联编辑: 名称 / 标签 / 数据类型 / 单位 / 枚举引用 / 多重性
- **关联表** 内联编辑: 出向关联支持增改删;入向关联只读,点击源类名跳转过去编辑
- **父类跨层** — M1 类可以选 M2 类作为父类,下拉带中文标签 + `[M1]`/`[M2]` 徽章
- **继承属性自动重算** — 换父类后,继承属性列表立刻按新父类链重新生成
- **预览 → 确认** — 提交前弹预览弹窗展示完整 diff (含级联: 重命名类会显示哪些子类 `parent_class_name`、哪些关联 `class_name` 会被自动同步)
- **原子提交** — 一次 `PUT /models/{id}` 整包替换,后端 Pydantic 校验

### 6 · 关系图交互画布

- **两种布局** — 分层布局 (M2 在顶部一行,M1 按父类分网格);力导向 (节点互斥 + 边吸引 + 硬位置碰撞校正 + O(n) 空间哈希)
- **层级边特殊样式** — `is_hierarchy=true` 的 M2 关联用橙色粗边 (stroke=#d79b00),一眼可见
- **无限 SVG 画板** — 鼠标拖动平移,滚轮以鼠标位置为原点缩放 (15%-400%)
- **节点** — 圆球渐变,M2 紫色、M1 蓝色,大小随属性数
- **hover 弹窗** — 显示类名/标签/描述/属性统计/父类/子类列表
- **选中高亮** — 仅相邻节点 + 连接边保持不透明,其余全部暗化到 15%
- **小地图 + 搜索 + 层级标签筛选**
- **键盘**: `F` 适应画布 · `0` 100% 缩放 · `L` 重新布局 · `/` 焦点搜索 · `Esc` 取消选中

### 7 · 发布生命周期 (V3.0)

- **状态机**: `draft → review → published → deprecated`
- **状态徽章** — M1/M2 dashboard 顶部色块 (🟡 草稿 / 🟠 评审中 / 🟢 已发布 / ⚫ 已废弃)
- **点击徽章** 弹出切换对话框,只显示当前状态允许的转换 (服务端状态机校验,非法转换返回 400)
- **已发布**记录 `published_at` 时间戳 + `published_by` 操作者

### 8 · 审查包导出 (给业务人员评审的完整包)

```
审查包_xxx_20260419.zip
├── 审查报告.docx              叙述式报告 (已嵌入 PNG 图, 业务人员直接能看)
├── 审查意见表.xlsx             5 个 sheet 的结构化反馈表 (下拉框 + id 列)
├── 审查图集.pdf                多页静态 PDF (任意阅读器能打开, 中文嵌入)
├── 审查图集.drawio             多页可编辑图 (draw.io 打开, 支持审查图章)
├── 图片快照/*.svg              单页 SVG (浏览器/Word 365 可视)
├── 附件_源数据.json            完整源数据
└── 使用说明.md                 带 UTF-8 BOM, Notepad 可读
```

- **带阶段动画的进度条** — 5 个阶段 chip (准备数据 → Word → Excel → 图集 → PDF 打包)
- **CJK 字体全链路** — 自动注册 Microsoft YaHei,中文不变 ■■■
- **RFC 5987 中文文件名** — 浏览器下载不乱码
- **按元结构分组输出** — Word 报告按 L1/L2/L3/L4 层级分章节,每层列出 MetaClass 属性 + 挂载的 M1 子类

### 9 · 完整包导入/导出 (迁移/备份/分享)

**`.mofpkg.zip` V1.0 格式** — 独立的模型迁移包,与审查包不同,设计目标是"原样还原到另一套系统":

```
xxx.mofpkg.zip
├── manifest.json       # format_version/导出时间/contents/依赖/integrity.sha256
├── README.md           # 人类可读包说明
├── models/*.json       # M1 + M2 完整模型
├── documents/{id}/     # 可选: meta + text + original 二进制
└── llm/providers.json  # 可选: provider 结构 (API Key 已剥离)
```

- **导出** — 导出对话框第三个 tab "完整包",可选包含 M2 / 历史版本 / 源文档 / LLM provider
- **导入** — 顶部工具栏 `📥 导入包` 按钮,拖拽或选择 zip
  - **预览**: 解析 manifest → 显示内容清单 + 冲突检测 + 依赖状态 (bundled/local/missing)
  - **冲突策略**:
    - 🎯 **改名导入 (默认)** — 生成 `imported_<hash>`,M2 同步为 `m2_imported_<同hash>`,内部引用自动重写
    - 🛑 跳过已存在 — 冲突 ID 不导入
    - 🔄 覆盖已存在 — 用包内数据替换本地 (危险)
  - **自动加载** — 导入完成后自动切到新导入的 M1 模型
- **SHA256 校验** 每个文件,传输损坏会被拒绝
- **API Key 永远不导出**,其他 LLM provider 结构可选带,导入后需用户重新填 Key

### 10 · 多文档、多 M1 并行管理

- 支持任意多个 M1 模型共存
- 顶部下拉切换,带数字徽章显示总数
- 每个 M1 可绑定 0 或 1 个 M2 元模型

### 11 · LLM 多提供商支持

内置 9 种预设,一键切换:

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

每个配置可独立设置 `batch_max_chars`。

### 12 · LLM 调用统计

- 总调用次数、成功率、平均响应时间、估算 Tokens
- 按模型 / 提供商分组统计
- 最近 20 条调用明细
- 调用趋势柱状图

### 13 · V3.1 质量强化 · A/B/C/D

**A) 文档类型感知抽取** — 每个上传文档可标类型:
| 图标 | 类型 | 典型 | 抽取策略 |
|---|---|---|---|
| 📘 | 制度规范 | 国标/行标/设计规范 | 积极抽类,属性严格 |
| 📗 | 技术说明书 | 产品手册/设计规格 | 属性密集,关联积极 |
| 📊 | 实例表单 | 台账/清单 | **保守** — 只抽列结构,警告行是 M0 |
| 💬 | 业务过程 | 会议/邮件/汇报 | 只抽角色+阶段,跳过具体事件 |
| 🔍 | 自动判断 | — | 上传时 LLM 200 字摘录分类 (~1 秒) |

AI Phase 1 prompt 按类型切换分支 + 强化 M0/M1 区分 (禁抽含编号/地名/年份/型号的具体对象)。抽完后启发式标出"疑似 M0 实例",审查面板**默认不勾选**,一键恢复。

**B) M1 composition 补边 (Phase 1.5)** — 常规关联抽取容易漏掉跨文档的包含关系 (电站在 A 文档,机组在 B 文档)。Phase 1 结束后增加一次专项 AI 调用:
- 输入所有 M1 类 + 合并的文档摘录
- 只抽 composition/aggregation,跳过 已有边
- 补充的边带 `[Phase1.5补边]` 标记

**C) 同义类检测 + 合并** — 工具栏 🔀 同义类 按钮:
- **规则层**: 剥离常见前后缀 (主/辅/设备/系统) + Levenshtein ≤ 2
- **LLM 层 (可选)**: 一次调用做语义分组
- 发现组后弹出合并 UI,每组单选"保留哪个",其余**全局重写引用** (parent_class_ref/parent_class_name · AssociationEnd · StructuralPattern.participating_class_ids)

**D) 质量体检 (Quality Sanity Checks)** — 模型 dashboard 顶部自动 banner (仅问题时出现):
- 抽取密度 (字符/类) 异常 → "可能误抽 M0"
- 平均属性数/类 < 2 → "大量空壳类"
- composition 密度 < 0.3 → "层级树稀疏"
- 孤儿枚举数 ≥ 3 → "AI 过度生成"
- 元结构深度 > 6 → "过度细分"

每条警告带具体 `hint_action` 指引下一步操作。

### 14 · V3.2 元结构可视化编辑器 + M1 级联影响预览

**背景**: V3.0 之后用户可以看到元结构,但只能内联改 label。真正的调整 (加/删层级、换参与类、改约束、删 pattern) 都需要手工改 JSON。V3.2 提供完整的 UI + 安全的级联处理。

**核心流程**:
```
用户在编辑器里改元结构  →  preview-impact (干跑)
                              ↓
         后端 diff + 扫描所有 M1 模型的受影响类
                              ↓
                   Impact Preview 对话框
      ┌──────────────────────┴──────────────────────┐
    [返回编辑]          [应用全部修改]          [跳过 M1 同步]
                              ↓
                   原子事务保存 (M2 + 所有 M1)
                              ↓
                   成功 or 整体回滚
```

**功能清单**:

1. **元结构编辑器 Modal** (左表右预览)
   - 基本信息: name / label / description / recommended_assoc_type
   - 层级列表: 每行 `L[N] level_name MetaClass role [↑↓×]`,可排序/增删
   - 约束: 4 个固定常量 (`no_cycle / no_cross_level / no_reverse / root_fixed`)
   - 右侧实时预览: mini 树节点 + 箭头 + role 配色
   - 实时验证面板: 本地 + 后端 validate 端点反馈

2. **Impact Preview 对话框** (级联影响呈现)
   - 🟢 零影响变更 (纯安全,可直接应用): 改层级名/标签/描述/约束
   - 🟡 新增/扩展变更: 加新层级 / 加参与类
   - 🔴 需要决策的变更: 删层级 / 换参与类 / 重排顺序
     - 每项列出受影响的 M1 类 (来自所有绑定此 M2 的 M1 模型)
     - 4 个批量策略: 🔓 保留父类 / 🔀 重挂到新类 / 🚫 清空 / 🗑 删除 M1
     - 可展开逐个 M1 类做不同决策

3. **M2 关系管理区 (P5)** — 紧接元结构面板下方
   - 筛选器: `全部` / `🏗️ 层级边` / `🔗 普通关联`
   - 每条关联: 名称 / 端点 / 多重性 / `is_hierarchy=?`
   - `+ 新增` / `✎ 编辑` / `🗑 删除` 按钮
   - 删除会自动从 StructuralPattern.hierarchy_association_ids 中清理

4. **原子事务保障**
   - `AtomicModelWrite` 上下文管理器: 多模型 snapshot → 失败回滚
   - 保存 M2 + 改 M1 同一事务,任一失败则全部恢复到保存前

5. **自动派生层级边**
   - 编辑器只让用户管理"层级列表",系统保存时自动:
     1. 用户指定的 assoc id 优先 (override)
     2. 已有 A→B Association → 升级为 hierarchy (is_hierarchy=true, order)
     3. 都没有 → 新建一条 `AHas{B}` composition 关联

**相关 API**:
| Method | Path | 作用 |
|---|---|---|
| POST | `/models/{m2}/structural-patterns/validate` | 纯校验 |
| POST | `/models/{m2}/structural-patterns/preview-impact` | 干跑,返回变更清单 + M1 影响 |
| POST | `/models/{m2}/structural-patterns` | 新建 pattern + 可选 M1 迁移 |
| PUT | `/models/{m2}/structural-patterns/{id}` | 更新 pattern + 可选 M1 迁移 |
| DELETE | `/models/{m2}/structural-patterns/{id}?keep_classes=true` | 删除, 默认保留类/关联 |
| PATCH | `/models/{m1/m2}/associations/{id}` | 关联全量字段编辑 (含 is_hierarchy / order) |

### 15 · V3.3 业务用户友好的可视化 CRUD (Phase 1)

**解决痛点**: AI 起草的模型不可能 100% 准确,业务用户必须能方便调整,但现有 CRUD 对他们太"工程师味" (String/Integer/multiplicity/composition 这些术语完全不懂)。

**4 大核心能力**:

1. **类卡片 hover 快捷操作** — 每张类卡 hover 后右上角出现 [✎ 改名] [+📋 加属性] [+🔗 加关联] [🗑] 4 个按钮,双击 label 也能 inline 改中文名
2. **图标化属性构建器** (visual attribute builder)
   - 6 个大图标卡片选数据"**语义类型**" (不是 String/Float 这种技术字眼):
     - 📝 文本 / # 数字 / 💰 金额/物理量 / 📅 日期 / ✓ 是/否 / 🏷️ 选项列表
   - 选 💰 自动弹出单位输入框 (常用单位 datalist: MW/kW/kV/°C/¥/...)
   - 选 🏷️ 弹出选项列表编辑器 (回车添加选项,× 删除)
   - 实时预览: "**额定功率**: 金额/物理量 (MW) · 1 个 · 必填"
   - 3 组一键模板: 📋 基础信息 / ⚙️ 设备属性 / 💼 工程项目 (每组 4 属性)
   - 进阶区折叠 (英文名/多重性/默认值) 不抢新手视线
3. **向导式关联构建器** (visual relationship builder) — 三步:
   - 步骤 1: 选源类 → 目标类 (下拉)
   - 步骤 2: 选关系类型 (3 张带说明的大卡片):
     - 📦 A 包含 B (强包含) - composition
     - 🤝 A 拥有 B (弱包含) - aggregation
     - ↔ A 引用 B (关联) - association
   - 步骤 3: 数量 (1 个 / 多个) - 不用填 `upper: -1`
   - 自动生成自然语言预览: "**抽蓄机组** (1 个) **包含** **水泵水轮机** (多个)"
4. **依赖感知的安全删除** - 删除类前弹出后果预览:
   - 列出 N 条出向/入向关联将悬空
   - 列出 M 个子类将失去父类
   - 3 个处理策略:
     - 🔓 仅删除保留关联 (默认) — 关联变悬空, 以后手动修
     - 🔥 级联删除 (危险) — 同步删相关关联和子类
     - 🔀 重挂父类 — 子类改挂到另一个类 (下拉选)

**技术实现**:
- 后端 `Attribute.logical_type` 新字段 (可选): "text" | "number" | "quantity" | "date" | "boolean" | "enum" - 为显示保留业务语义
- `AttributeCreateRequest` + PATCH whitelist 都接受 `logical_type`
- 旧数据无 `logical_type` 时前端 `deriveLogicalType()` 自动反推 (有 unit 的 Float 推为 quantity,有 enum_ref 的推为 enum)
- 英文 `name` 字段从中文 label 自动 camelCase 转写,业务用户一般不用管
- 选项列表自动创建 Enumeration + 引用 (`enum_ref`),如果包里已有完全同名同值枚举则复用

**Phase 2** (已完成): 5 步向导式新建类 + 属性模板库扩展至 7 组。入口在每个 M1/M2 dashboard 顶部 "🆕 新建类" 绿色按钮:
- 步骤 1: 中文标签 + (可选) 英文名
- 步骤 2: 选父类 (支持 M1 和 M2 候选,带搜索)
- 步骤 3: 勾选多个属性模板 + 自定义补充
- 步骤 4: 勾选要建立关联的现有类 + 选关系类型 (composition/aggregation/association) + 数量
- 步骤 5: 预览确认 + 一键创建 (顺序调 API: 建类 → 加属性 → 建关联)

新增模板 (从 3 组扩到 7 组): 📋 基础信息 · ⚙️ 设备 · 💼 工程 · 📝 合同 · 👥 人员 · 📅 审批流 · 🏢 组织

**未来 Phase 3** (按效果再决定):
- Undo/Redo 操作历史
- 图形画布编辑模式 (拖拽建关联 / 右键菜单)

### 16 · 其他

- **M3 规范校验** — 数据类型合法性、枚举引用、关联端点、多重性 + 元结构完整性
- **M1 版本管理** — 快照与还原
- **LLM 对话流** — 提取/推导过程中实时展开每次 AI 调用的完整 prompt + 原始响应
- **失败批次重试** — 某批 AI 调用失败时单独标记,完成后可一键重试
- **可最小化进度面板** — 提取过程中切后台继续工作,右下角浮动徽章显示进度
- **实体级别取消** — 随时中止任务,已完成的类/属性保留在成果区等待保存

---

## 技术栈

### 后端

- Python 3.10+ (推荐 3.12-3.14)
- FastAPI + Uvicorn (with StatReload)
- Pydantic 2.x
- anthropic SDK + openai SDK (通过统一的 `llm_client.py` 抽象)
- pdfplumber · python-docx · openpyxl (文档解析)
- reportlab + svglib (PDF / PNG 渲染, CJK 字体嵌入)
- aiofiles · zipfile (标准库, 完整包打包)

### 前端

- 纯原生 HTML / CSS / JavaScript,**无构建无框架**
- ES Modules
- SVG (关系图、元结构树图、审查图集)
- 仅一个 `api.js` 和一个 `app.js` (~6000 行)

### 存储

- 本地 JSON 文件 (`backend/data/`)
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

右上角 `⚙ LLM` → `+ 新增`:

1. 选服务商 (预设自动填 Base URL / 模型名)
2. 填 API Key
3. 可调节 `max_tokens` / `超时` / `单批字符上限`
4. `⚡ 测试连接` 验证
5. 保存并激活

### 4. 生成示例数据 (可选)

内置抽水蓄能电站的 M1+M2 完整示例,含 27 个 M1 类 (抽蓄电站/机组/水泵水轮机/发电电动机/...) + 4 级元结构 + 27 条 composition:

```bash
python scripts/seed_pumped_storage.py
```

然后刷新前端下拉框选"抽水蓄能电站 · M1 (27 个领域类)",可立即看到元结构树形面板。

### 5. 典型工作流

```
上传业务文档 (左侧面板)
  ↓
⚙ AI 提取 M1
  ↓  观察进度 / 看 LLM 对话流 / 可最小化
  ↓
审查面板 勾选要保留的实体 → 确认导入
  ↓
↑ 反推 M2 → 范围选择器勾选参与的 M1 类 → 执行 3 阶段推导
  ↓  (会自动识别元类 vs 元结构, 生成 StructuralPattern)
  ↓
⟶ 关系图 观察层级 / 拖动节点 / 点球看详情
⟶ M2 视图 展开元结构 inspector / M1 视图 查看分层 classes
  ↓
任意类 ✎ 编辑  添加删改属性/关联 → 预览级联变更 → 确认保存
  ↓
[🟡 draft] → 点徽章切 [🟠 review] → 评审通过切 [🟢 published]
  ↓
选择用途:
  • ⬇ 导出 → 📄 审查包 (.zip)      给业务人员评审
  • ⬇ 导出 → 📦 完整包 (.mofpkg.zip) 备份/迁移/分享
  • 📥 导入包                        还原他人/他处导出的模型
```

---

## 项目结构

```
E:\MOFGenerator\
├── run.py                             # 启动入口
├── requirements.txt                   # Python 依赖
├── scripts/
│   └── seed_pumped_storage.py         # 抽水蓄能电站示例 seed (27 M1 + 4 级元结构)
├── backend/
│   ├── app.py                         # FastAPI 应用 + 无缓存中间件
│   ├── config.py                      # 配置 (数据目录、端口等)
│   ├── models/                        # Pydantic 数据模型
│   │   ├── m3_schema.py               # M3 元元模型 (含 StructuralPattern / publish_status)
│   │   ├── m2_template.py
│   │   ├── m1_model.py                # M1 模型结构 (含版本)
│   │   ├── llm_config.py              # LLM 配置
│   │   └── api_schemas.py             # API 请求/响应
│   ├── services/
│   │   ├── document_parser.py         # 多格式文档解析
│   │   ├── ai_extractor.py            # ★ 核心 AI 逻辑 — M1 单趟提取 + M2 3 阶段推导
│   │   ├── llm_client.py              # 统一 LLM 抽象
│   │   ├── llm_stats.py               # 调用统计
│   │   ├── model_validator.py         # M3 规范校验 + 元结构完整性
│   │   ├── model_exporter.py          # 原始数据导出 (JSON/YAML/MOF Text)
│   │   ├── review_exporter.py         # ★ 审查包打包 (Word + Excel + zip)
│   │   ├── diagram_exporter.py        # ★ 图集生成 (drawio + SVG + PDF + PNG)
│   │   ├── package_io.py              # ★ 完整包 .mofpkg.zip 导入/导出
│   │   └── version_manager.py
│   ├── routers/                       # API 路由
│   │   ├── documents.py
│   │   ├── extraction.py              # 提取任务 (并行/中止/心跳/重试)
│   │   ├── models.py                  # M1/M2 CRUD + 版本 + 验证 + 发布状态
│   │   ├── m2_templates.py
│   │   ├── export.py                  # 原始导出 + 审查包导出
│   │   ├── package_io.py              # ★ 完整包 3 端点 (export/preview/import)
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
    ├── css/style.css                  # 样式 (~3900 行)
    └── js/
        ├── api.js                     # API 客户端
        └── app.js                     # 应用逻辑 (~6000 行)
```

---

## 关键架构

### 单趟 M1 提取核心流程

```
1. 文档按 batch_max_chars 切批 (单文件过大则 overlap=500 字符滑窗分块)

2. 首批 (batch 0) 串行执行:
   LLM(doc_text, known_context=空) → {classes[含attrs], enumerations, hierarchy_hints}
   写入 merged_classes / merged_enums (加锁)

3. 后续批次 (batch 1..N) 3 路并行执行:
   对每批:
     snapshot = 当前 merged_classes (加锁读)
     LLM(doc_text, known_context=snapshot) → {classes[含attrs], ...}
     合并去重 (加锁写)

4. 关联提取 (3 路并行, 首批串行种子)
5. 构建 MOFClass / Attribute / Association / Enumeration 对象
6. 生成最终 Package → save_model
```

### M2 推导 (V3.0 · 元类 + 元结构)

```
已有 M1 (可能 N 个类)
    │
    ▼
Phase 1 聚类 (1 次 LLM, 只传 M1 骨架)
    → 15-40 个业务组
    ▼
Phase 2 组内抽象 (N 次并行)
    → 每组产出 1 个候选主题 (含共性属性)
    ▼
Phase 2.5 元结构探测 (N 次并行, V3.0 核心重构)
    对每个候选主题,LLM 输出:
    {
      "has_hierarchy": true/false,
      "levels": [
        {"level_name": "L1-设施", "class_name": "Facility", "class_label": "设施",
         "description": "...", "attributes": [...]},
        {"level_name": "L2-功能分组", ...},
        ...
      ],
      "hierarchy_associations": [
        {"source_level": "L1-设施", "target_level": "L2-功能分组",
         "name": "facilityHasGroup", "label": "设施包含功能分组",
         "association_type": "composition"}
      ],
      "root_level_name": "L1-设施",
      "m1_level_assignments": [
        {"m1_class_name": "PumpedStoragePlant", "level": "L1-设施"},
        {"m1_class_name": "UnitZone", "level": "L2-功能分组"}
      ]
    }

    ✓ 有层级 → 物化为 N 个 MOFClass + N-1 条 is_hierarchy Association + 1 StructuralPattern
    ✗ 扁平 → 单个 MetaClass (元类)
    ▼
Phase 3 跨组去重 (1 次 LLM)
    ▼
物化 M2 Package
    元结构主题:
      • N 个 MOFClass, 各自带 meta_structure_id / meta_structure_role / meta_structure_level
      • N-1 条 Association, is_hierarchy=true, hierarchy_order=1..N-1
      • StructuralPattern 聚合 {participating_class_ids, hierarchy_association_ids,
        root_class_id, level_names, constraints, recommended_assoc_type}
      • publish_status="draft" (默认)
    扁平主题:
      • 单个 MOFClass
    │
    ▼
save-m2 时回写 M1:
  • 按 m1_level_assignments 设置 parent_class_name 到具体层级 MetaClass
    (如 PumpTurbine → Equipment, 而不是统一到 "设备台账" 父类)
  • 继承属性按新父类链重新计算 is_inherited
```

### 元结构完整性校验 (model_validator.py)

对每个 StructuralPattern:
- ✅ 参与类 ≥ 2 个
- ✅ 层级 Association 数量 = N - 1 (严格连接所有参与类)
- ✅ 所有 Association 两端都在 `participating_class_ids` 里
- ✅ root_fixed: 根节点 `root_class_id` 无 hierarchy 入边
- ✅ DFS 环检测 (VISITING/VISITED 状态追踪)
- ⚠️ 中间节点应同时有入边和出边 (否则告警)

### 完整包 V1.0 格式规范

**导出路径**: `PackageExporter.export(m1_id, options)` → zip bytes
- 构建 manifest (format_version=1.0, mof_system_version=V3.0, contents 清单, integrity.sha256)
- 用 pydantic `model_dump(mode="json")` 处理 datetime,再用 `json.dumps(..., default=str)` 兜底
- 可选带源文档 (meta + text + original 二进制) / LLM provider (API Key 剥离)

**导入路径**: `PackageImporter.preview / do_import`
- preview: 只解析 manifest,检查冲突 + 依赖状态,零副作用
- do_import 三策略:
  - **rename (默认)**: 
    - M1 重命名 `pumped_storage → imported_<hash8>`
    - 配套 M2 重命名 `m2_pumped_storage → m2_imported_<同hash8>` (保持前端依赖的 `m2_${m1_id}` 前缀约定)
    - M1 的 `m2_template_id` 自动重写到新 M2 id
  - **skip**: 冲突 ID 不导入,其他正常
  - **overwrite**: 用包内数据覆盖本地 (危险)
- SHA256 校验每个文件,不匹配则拒绝
- `primary_m1_id` 返回给前端用于自动加载

### 审查包 CJK 字体处理链

```
1. module import 时执行 _register_cjk_font():
   扫描 C:/Windows/Fonts/msyh.ttc / PingFang.ttc / NotoSansCJK.ttc
   TTFont.register("CJKSans", first_match)

2. SVG 生成时:
   所有 <text> 元素 font-family='CJKSans,"Microsoft YaHei","Segoe UI",sans-serif'

3. svglib.svg2rlg() 后、renderPDF/renderPM 前:
   _force_font_recursive(drawing, "CJKSans")
   遍历 Drawing 树, 把每个 String 节点的 fontName 强制改成 CJKSans
   (svglib 内部字体映射不查 pdfmetrics registry, 必须手动 override)

4. 渲染 PDF + PNG, 文本都能正确显示中文
5. 使用说明.md 加 UTF-8 BOM (\ufeff), 保证 Windows Notepad 识别
```

### 中止机制 (3 层防御)

- **前端** — 设置 `_pollAborted` 立即停止轮询
- **后端** — `_cancel_flags[task_id] = True`,各批次 `_check()` 优雅退出
- **不使用 `asyncio.Task.cancel()`** — 避免在 httpx/openai SDK 请求中途强制打断导致 event loop 崩溃

### JSON 容错修复 (多策略)

1. 清理 markdown 代码围栏 / 注释 / 尾逗号 / 缺失逗号
2. 定位报错位置 (`Expecting ',' delimiter` 等),尝试插入缺失逗号或引号
3. 最多 10 轮迭代修复
4. 自动平衡不匹配的花括号/方括号
5. 最后兜底正则提取最外层 JSON 对象

---

## 性能对比 (18 份业务文档、365 M1 类的真实场景)

| 维度 | 初版 | 当前版本 |
|------|------|----------|
| M1 提取总 LLM 调用数 | ~15,000 | ~100-200 |
| M1 提取耗时 | 理论 ~14 小时 / 实际超时 | **~10-15 分钟** |
| M2 推导总 LLM 调用数 | 1 (巨型 prompt 爆上下文) | ~27 (聚类 + N 组抽象 + 元结构探测 + 去重) |
| M2 推导耗时 | 超时或退化输出 | **~3-4 分钟** |
| M2 类数量 | 1-5 个空壳 Entity/Object | 15-40 个业务主题 + 多层元结构 |

---

## 已知限制

- **强依赖 LLM 理解能力** — 建议 Claude Sonnet 4 / GPT-4o / Qwen-Plus 级别模型;本地 8B 以下模型效果会明显退化
- **无数据库 · 单用户** — 所有数据落本地 JSON,不适合多人协同生产环境 (这是设计选择)
- **中文业务场景优化** — prompts 和 UI 主要针对中文业务文档;英文场景需要调整 prompt 示例
- **审查反馈回收是手工的** — 业务人员填完 Excel 后需手动回录系统
- **单次推导上限** — M2 推导依赖 LLM 能一次处理 M1 骨架 (~70KB),如果 M1 类数 > 1000,可能需要进一步分批
- **完整包向前兼容** — V1.0 格式,导入端系统版本 < V3.0 时元结构字段会被忽略并警告

---

## License

MIT
