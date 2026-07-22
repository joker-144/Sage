# External Search Skill

外部学术检索技能包 — Sage 系统多重验证机制的"外部验证层"。

## 1. 角色定位

本技能是 Sage 系统"突破本地文献库边界"的关键能力。当本地索引不足、需要查证引用真实性、或追踪最新研究进展时，本技能通过四大权威学术数据源（Google Scholar/arXiv/CrossRef/Semantic Scholar）为智能体提供外部文献支撑。在 Sage 多重验证机制中承担**外部验证层**：所有引用必须能在本地或外部检索中找到对应文献，否则视为伪造引用。

## 2. 核心能力

| 能力 | 工具 | 数据源 | 适用场景 |
|------|------|--------|---------|
| 综合检索 | `search_scholar` | Google Scholar | 跨学科综合检索 |
| 预印本检索 | `search_arxiv` | arXiv | 理工科前沿/未发表论文 |
| DOI 验证 | `search_crossref` | CrossRef | 引用真实性核验 |
| 语义检索 | `search_semantic_scholar` | Semantic Scholar | 智能推荐相关文献 |

## 3. 详细执行手册

### 3.1 检索源选择策略

#### 按学科选择
| 学科 | 首选源 | 备选源 | 理由 |
|------|--------|--------|------|
| 计算机/物理/数学 | arXiv | Semantic Scholar | 预印本优先发表 |
| 工程 (EI) | Scholar | Semantic Scholar | 综合检索覆盖广 |
| 医学/生物 | Scholar | CrossRef (DOI) | PubMed 互补 |
| 社科/人文 (SSCI) | Scholar | Semantic Scholar | 综合性最强 |
| 中文期刊 (CSSCI) | Scholar | — | 主要依赖Scholar |

#### 按目的选择
| 目的 | 推荐源 | 理由 |
|------|--------|------|
| 文献综述扩展 | Semantic Scholar | 语义推荐相关文献 |
| 最新进展追踪 | arXiv | 预印本时效性强 |
| 引用真实性验证 | CrossRef | DOI 官方注册库 |
| 作者/期刊查询 | Scholar | 元数据最全 |
| 综合+全面 | 多源并行 | 交叉验证 |

### 3.2 Google Scholar 检索规范（search_scholar）

#### 查询构造
- **关键词组合**：`"deep learning" "medical imaging" 2023`
- **作者限定**：`author:"Andrew Ng"`
- **标题限定**：`"transformer attention"`
- **时间范围**：`2020..2024`

#### 返回字段
```
- 标题 (title)
- 作者 (authors)
- 年份 (year)
- 摘要 (snippet)
- 引用数 (citations)
- 来源 (publisher/venue)
- 链接 (url)
```

#### 使用建议
- 引用数 > 100 通常是经典文献，优先纳入综述
- 引用数 < 10 的近1年文献可能是新兴方向，注意甄别
- Scholar 返回的摘要可能不完整，需配合 `extract_metadata` 补全

### 3.3 arXiv 检索规范（search_arxiv）

#### 适用学科
- cs.* (计算机科学)
- physics.* (物理学)
- math.* (数学)
- stat.* (统计学)
- q-bio.* (定量生物学)

#### 检索策略
- **关键词**：`attention mechanism transformer`
- **分类限定**：`cat:cs.CL`
- **时间排序**：按提交日期/相关性

#### 返回字段
```
- arXiv ID (如 2401.12345)
- 标题、作者、摘要
- 提交日期、最后修改日期
- 主分类、交叉分类
- PDF 链接
```

#### 注意事项
- arXiv 文献未经同行评审，引用时需标注 "[preprint]"
- 优先引用已正式发表版本（arXiv 上常标注 "Published in ..."）
- 部分 arXiv 论文后续被期刊拒稿，引用前应查证最新状态

### 3.4 CrossRef DOI 验证规范（search_crossref）

#### 主要用途
1. **DOI 验证**：给定 DOI 查询文献是否存在
2. **元数据补全**：从 DOI 获取完整书目信息
3. **引用核验**：验证引用的作者/年份/标题是否准确

#### 调用方式
- **按 DOI 查询**：`query="10.1038/s41586-023-06647-8"`
- **按元数据查询**：`query="attention is all you need 2017"`

#### 验证流程（多重验证机制）
1. 撰写员产出引用 `(Vaswani et al., 2017)`
2. 审校核查员提取 DOI（如有）或标题
3. 调用 `search_crossref` 查询
4. 比对返回的元数据与引用信息：
   - [ ] 作者列表匹配
   - [ ] 年份匹配
   - [ ] 标题匹配（容许大小写差异）
   - [ ] 期刊/会议匹配
5. 任何字段不匹配 → 标记为"待核验引用"

#### 验证结果分级
| 验证结果 | 含义 | 处理 |
|---------|------|------|
| 完全匹配 | 所有字段一致 | 通过 |
| 部分匹配 | 主要字段一致，次要字段差异 | 标注但通过 |
| 不匹配 | 关键字段不一致 | 必须修正 |
| 未找到 | CrossRef 无此 DOI | 转用 Scholar 二次验证 |

### 3.5 Semantic Scholar 检索规范（search_semantic_scholar）

#### 特色能力
- **语义检索**：基于论文语义相似度推荐
- **引用图**：查询文献的引用与被引关系
- **影响力评分**：S2 影响力分数（替代简单引用数）

#### 查询构造
- 自然语言查询：`methods for reducing hallucination in LLMs`
- 字段限定：`title:transformer AND year:2023`
- 排序：按影响力/相关性/年份

#### 返回字段
```
- paperId (S2 内部 ID)
- 标题、作者、年份
- 摘要
- 影响力分数 (influentialCitationCount)
- 总引用数 (citationCount)
- 外部链接 (DOI/arXiv/PDF)
```

#### 推荐使用场景
- 文献综述时按"语义相似度"扩展相关研究
- 查找某篇论文的"影响力引用"（被重要论文引用的次数）
- 通过引用图构建研究脉络

## 4. 多源交叉验证流程

### 4.1 引用真实性验证（标准流程）
```
输入：一条引用 (Author, Year, Title, [DOI])

Step 1: 若有 DOI
        → 调用 search_crossref 验证
        → 若完全匹配 → 通过
        → 若不匹配 → 标记问题字段
        
Step 2: 若无 DOI 或 CrossRef 未找到
        → 调用 search_scholar 按标题+作者查询
        → 若找到 → 通过（标注"非DOI验证"）
        → 若未找到 → 转Step 3
        
Step 3: 调用 search_semantic_scholar 语义查询
        → 若找到相似文献 → 提示作者确认是否为同一篇
        → 若仍未找到 → 标记为"可疑引用"，需人工核实
```

### 4.2 文献综述扩展流程
```
Step 1: 调用 search_literature 检索本地库
Step 2: 若本地不足 → 调用 search_semantic_scholar 语义扩展
Step 3: 若需最新 → 调用 search_arxiv 查预印本
Step 4: 若需综合 → 调用 search_scholar 补充
Step 5: 去重合并 → 输出推荐文献列表
```

## 5. 与其他技能的协作

| 协作技能 | 协作方式 |
|---------|---------|
| `literature-index` | 本地不足时补充检索；引用验证时配合 |
| `writing-assistant` | 撰写时实时检索支撑文献 |
| `paper-processing` | 检索结果通过元数据提取后入库 |
| `ai-pattern-reducer` | 改写时验证引用真实性 |

## 6. 多重验证机制中的角色

本技能在 Sage 多重验证机制中承担**外部验证层**：

```
验证层次1：文献库验证 (literature-index.check_plagiarism)
           ↓ 失败
验证层次2：外部检索验证 (本技能 search_crossref + search_scholar)
           ↓ 失败
验证层次3：审校智能体复核 (Reviewer Agent 人工判断)
           ↓ 失败
结论：标记为伪造引用，必须删除或修正
```

## 7. 质量标准

- 引用验证准确率 ≥ 95%（与人工核验对比）
- 多源检索去重率 ≥ 90%
- 元数据完整度 ≥ 85%（标题/作者/年份/DOI）
- 检索响应时间 < 10秒/次
- 假阳性率 < 5%（标记为真实但实际伪造）

## 8. 智能体自主判断事项

以下决策由智能体根据上下文自主判断：
- 检索源选择（基于学科/目的）
- 检索深度（top_k 数量）
- 是否触发多源交叉验证
- 验证不通过时的处理（修正/删除/人工核实）
- 是否纳入本地文献库（下载后索引）
- 预印本是否引用（基于目标期刊要求）

## 9. 注意事项

- 外部检索依赖网络，离线环境下仅返回本地缓存
- Google Scholar 有反爬限制，频繁调用可能被限流
- arXiv 论文未经同行评审，引用需谨慎
- CrossRef 仅覆盖有 DOI 的文献，部分中文文献无 DOI
- Semantic Scholar 影响力分数仅供参考，不等于学术质量
- 所有外部检索结果纳入本地库前需用户/智能体确认
- 验证失败的引用必须修正或删除，不得保留
