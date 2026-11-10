# AI-Powered Energy Offer Comparison and Savings Agent
## 商业模式深度调研、竞争格局与技术架构评估报告（2025–2026 实时市场版）

> **项目路径**：`/Users/meng/Projects/energy-savings-agent`  
> **目标初始市场**：澳大利亚新南威尔士州（NSW）居民电力与天然气市场（Ausgrid、Endeavour Energy、Essential Energy 及 Jemena Gas）  
> **核心铁律（Core Principle）**：AI 负责从账单与条款中提取和解释信息；确定性定价引擎（Deterministic Pricing Engine）负责计算所有费用与节省金额；推荐排序 100% 基于用户利益与资格条件，绝不按返佣高低排序。

---

> **Implementation status notice (2026-11-10):** This document contains strategic targets, market estimates and external regulatory claims, not proof of current implementation or verified live-market coverage. In particular, references below to **100% market coverage**, automatic daily market-wide collection, and guaranteed cheapest offers are **aspirational**, not capabilities established by the current source code. The current collector defaults to local JSON offers, live CDR is opt-in and limited in scope, and some live rates may be inferred. Do not use those claims in customer-facing materials until verified. See [Market Coverage and Validation](MARKET_COVERAGE_AND_VALIDATION.md). External facts and API versions require revalidation against official sources before production.

## 一、 2025–2026 澳洲 NSW 零售能源市场基准与监管最新动态

### 1. NSW 官方默认市场报价（Default Market Offer, DMO 7 & DMO 8）基准线
澳大利亚能源监管局（AER）每年设定 **DMO（Default Market Offer）**，既是未签约市场套餐（Standing Offer）用户的法定价格上限，也是全市场所有零售商宣传“比参考价低 X%”的法定基准价（Reference Price）：

| NSW 配电网区域（Distribution Zone） | 覆盖地理范围 | 2025–26 (DMO 7) 年度基准账单 | 2025–26 同比涨幅 | 2026–27 (DMO 8) 年度基准账单（2026年7月1日生效） | 2026–27 同比变动 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Ausgrid** | 悉尼市区、东区、北岸、中央海岸、Hunter | **~AUD \$1,965 / 年** | +8.5% ~ +9.1% | **~AUD \$1,899 / 年** | -3.4%（-\$66） |
| **Endeavour Energy** | 大悉尼西区（Parramatta 等）、蓝山、Illawarra | **~AUD \$2,411 / 年** | +8.5% ~ +9.1% | **~AUD \$2,328 / 年** | -3.4%（-\$83） |
| **Essential Energy** | NSW 偏远及乡村地区（Regional & Rural NSW） | **~AUD \$2,741 / 年** | +8.5% ~ +9.7% | **~AUD \$2,604 / 年** | -5.0%（-\$137） |

* **市场洞察**：虽然 NSW 超过 80% 的家庭处于 Market Offers（市场合同），但在经历 2025–26 财年 8.5%~9.7% 的大幅涨价后，未主动比价的老客户与市场最优新客套餐之间的价差普遍在 **AUD \$300–\$600/年**，远超本项目的 `AUD $100` 告警门槛。

---

### 2. 2025–2026 澳洲能源监管改革（AEMC & AER）对本项目的重大影响

1. **AEMC 定价审查（Pricing Review，2026 年 6 月终裁）——“熟客税（Loyalty Tax）”未被一刀切取缔**：
   * **最新进展**：澳洲能源市场委员会（AEMC）曾讨论是否强制要求零售商“对同一套餐的新老客户收取完全相同的价格（Same-price rule）”。但在 **2026 年 6 月发布的最终报告中，AEMC 放弃了强制同价令**，转而继续依赖信息披露机制（引发了 Energy Consumers Australia 与 COTA 等消费者组织对“未能终结 Loyalty Tax”的强烈批评）。
   * **对本项目的战略意义**：**监管机构没有消灭新老客户价差**，意味着零售商仍会通过“新客首年低价/高返现 + 次年涨价”的模式获利。消费者依然必须靠“定期比价与切换”自救——这直接确立了本 Agent **“全年自动盯盘（Daily Monitoring）+ 次年熟客税预警（Promo Cliff Warning）”** 的长期刚需地位。

2. **AEMC 消费者信心改革包（2025 年 9 月快速切换规则 & 2026 年 7 月 1 日生效新规）**：
   * 零售商每年最多只能上调一次价格（Once-a-year price increase limit）；
   * 禁止收取不合理的滞纳金（Late-payment fees）并强制提供免手续费支付方式；
   * 优惠期届满后，严禁自动滚入高于 Standing Offer（DMO 上限）的惩罚性费率；
   * 2025 年 9 月新规进一步简化了转网（Switching）流程。

3. **AER《Better Bills Guideline（更佳账单指南）》与“同品牌陷阱”**：
   * **法规要求**：所有澳洲电费/气费账单第一页必须印有强制性的 **“Better Offer Statement（您换个套餐能省钱吗？）”**，告知用户该零售商内部是否有更便宜的套餐，并附上政府 *Energy Made Easy* 链接。2026 年年中，AER 启动了将《Better Bills Guideline》、《Benefit Change Notice Guidelines》等四项零售指南合并为统一合规文件的改革。
   * **核心产品切入点（Wedge）**：账单上的“Better Offer”提示框 **只对比当前同一家零售商内部的套餐**（例如 AGL 账单只会告诉你 AGL 内部有没有更便宜的计划，绝不会告诉你隔壁 OVO、Red Energy 或 Globird 能多省 \$400）。我们的 AI Agent 在解析 PDF 账单时，可以直接提取账单上的“同品牌建议节省额”，并与“全市场跨品牌最优节省额”做直观对比，一眼击穿同品牌挽留话术。

4. **CDR Action Initiation（消费者数据权“写入/代客执行”权限）2025–2026 状态**：
   * 尽管《2024 年财政部法律修正案（消费者数据权）法案》（*Treasury Laws Amendment (Consumer Data Right) Act 2024*）已在法律层面确立了 **Action Initiation（允许获得授权的第三方代用户发起开户/转网指令）**，但在能源行业，由数据标准机构（DSB）制定的具体 API 写入标准与零售商强制接入时间表在 2026 年仍处于分阶段推进期，且要求极高门槛的 ACCC CDR 认证（Accreditation）。
   * **结论**：提案中将 **“代用户自动切换零售商（Automated Switching）”排除在 MVP 之外**，由用户通过官方链接/追踪链接自主完成最后一步确认，完全符合 2026 年的技术与监管现实。

---

## 二、 澳洲能源比价赛道竞争格局深度对标

通过实时调研，当前澳洲市场存在三类代表性玩家，我们的项目应当采取精准的差异化卡位：

| 对比维度 | 1. 政府官方平台 (*Energy Made Easy* / *VIC Energy Compare*) | 2. 传统商业比价巨头 (*Finder*, *Canstar Blue*, *Compare the Market*, *iSelect*) | 3. 付费订阅管家 (*Bill Hero* — "Savings-as-a-Service") | **4. 本项目 (AI Energy Savings Agent)** |
| :--- | :--- | :--- | :--- | :--- |
| **用户端收费** | 免费（纳税人出资） | 免费 | **付费年费订阅**（单电约 **AUD \$49/年**，电+气 **AUD \$79/年**） | **核心比价与盯盘免费**（后续可选高级多房产/智能电表增值服务） |
| **商业变现模式** | 无商业变现 | 向零售商收取高额 CPA 佣金（或电销坐席分成） | 拒绝零售商佣金，纯靠向消费者收年费（提供首单省不够退费承诺） | **披露式引流返佣（Model A）+ B2B 渠道白标（Model C）** |
| **市场覆盖与客观性** | 100% 覆盖全部法定公开套餐，完全中立 | 往往仅展示或优先展示签约返佣合作商（Panel 局限），常带有电销骚扰 | 100% 覆盖全市场套餐，客观排序 | **100% 抓取全市场套餐；严格按用户净利益排序，绝不因有无佣金改变排名** |
| **持续监控能力** | **无**（单次手动查询，用户极易遗忘） | 弱（多为营销邮件群发） | **强**（提供专属转发邮箱自动收账单并每期重算） | **强**（每日巡检市场费率与限时促销，仅当首年可省 $\ge$ **AUD \$100** 且版本变化时才精准提醒） |
| **官网限时促销捕捉** | 仅包含标准 CDR 申报数据，常遗漏官网限时码/积分/联名礼券 | 包含部分独家合作券（如 Finder 专属礼卡） | 侧重账单费率核算 | **CDR 官方标准数据 + LLM 零售商官网促销页富集（如开户返现、Qantas 积分折算提示）** |
| **交互形态** | 传统网页表单 | 传统网页表单 + 电话销售 | 邮件转发 + 人工辅助切换 | **PDF 智能解析 + 确定性引擎 + Web UI + Agent-Native CLI/MCP（参考 LetsFG 模式）** |

### 核心竞争策略提炼
* **对比 *Energy Made Easy***：我们赢在 **“自动账单记忆 + 每日盯盘提醒（UC-05）+ 零售商官网独家促销富集（UC-02）+ 次年熟客税拆解”**。
* **对比 *Bill Hero***：*Bill Hero* 验证了“持续账单监控”的真实需求，但其 **\$49–\$79/年的前置付费门槛** 劝退了大量对价格敏感的家庭。我们通过 **免费提供 PDF 解析与 \$100+ 阈值提醒**，获客转化率（Top-of-Funnel）将高出一个数量级。
* **对比 *Compare the Market / iSelect***：我们不收集用户手机号卖给呼叫中心（No Spam Calls），且完全公开每一分钱的计算公式，即使是没有返佣的零售商（如某些独立低价品牌）排第一也照常推荐并提供直达链接，建立极致信任。

---

## 三、 澳洲能源行业真实变现路径与 Affiliate 落地细节（关键商业洞察）

在提案 Section 3 & Section 4 中，假设了“用户通过 Referral 链接切换、每单获取约 AUD \$100 佣金”。通过调研澳洲能源 Affiliate 市场的真实运作机制，**商业落地的核心现实与执行路径如下**：

### 1. 行业残酷现实：一线零售商（AGL / Origin / EnergyAustralia）不对独立小站开放自助 CPA
* 与电商不同，澳洲三大能源巨头（Tier-1：AGL、Origin、EnergyAustralia）**极少在公共联盟网络向普通独立开发者直接开放自助式 CPA 链接**。例如 Origin 自己收购并控股了搬家接电平台 *Compare & Connect*。
* 如果在 Stage 2（小规模 Pilot）直接去找 AGL 或 Origin 商务团队谈直签 CPA 协议，对方通常会要求较高的月均转网单量（Volume Commitment）、合规审计及长周期法务合同。

### 2. 破局方案：三级火箭式变现接入路径（Stage 2 $\rightarrow$ Stage 4）

#### 第一级（MVP / Pilot 冷启动期）：接入澳洲本土聚合联盟网络（Aggregator CPA）+ 挑战者品牌双边推荐
1. **通过 Commission Factory 接入 Econnex / CIMET（或 Compare & Connect / WattUtilities）**：
   * **运作方式**：**CIMET**（旗下拥有消费端品牌 **Econnex**）是澳洲主流的 **Energy Comparison-as-a-Service (CaaS)** 基础设施提供商（为 Cashrewards、Raiz、Zip 及众多房产/理财平台提供底层交易结算），并在亚太最大的联盟平台 **Commission Factory** 上开放了标准的 Affiliate 计划。
   * **真实佣金水平（CPA）**：每成功完成一单电力或天然气切换（Approved Switch），佣金通常在 **AUD \$50 – \$200 / 单** 之间（双燃料电+气切换往往按两单或更高档位计算，完全支撑提案中 AUD \$100/switch 的财务模型假设！）。
   * **结算周期与退单期（Validation & Clawback Period）**：必须注意现金流账期——用户提交转网申请后状态为 `Pending`，需等待零售商完成电表交接、度过法定冷静期并确认用户支付首期账单（通常为 **60–90 天验证期**）后，佣金才会正式结算（Approved）。
2. **中小型挑战者品牌（OVO Energy、Amber Electric、Globird Energy、Octopus Energy）**：
   * 这类科技型/低价型零售商在 NSW 费率极有竞争力（经常排进自然省钱榜 Top 3），且普遍设有 **Partner / Friend Referral 机制**（例如双方各得 \$50–\$100 账单抵扣或合作推广奖励）。

#### 第二级（NSW 促销库富集）：捕捉 CDR 标准库之外的“隐形羊毛”
根据 2025–2026 NSW 市场调研，零售商大量促销并不直接体现在基础费率表里，而是通过官网活动页发放：
* **AGL**：NSW 新客线上开户常见 **AUD \$150 电费返现（Sign-up Credit，通常需在网满 90 天）**，或与 Finder 等平台联名赠送电子礼品卡、Netflix / 宽带捆绑减免。
* **Origin Energy**：双燃料（电+气）线上新客常见最高 **AUD \$300 账单返现**、**Everyday Rewards 积分奖励** 以及与澳洲联邦银行合作的 **CBA Yello** 专属返现。
* **Red Energy**：主打 **Qantas Frequent Flyer（澳航常旅客积分）** 开户奖励（数万积分）及 100% 澳洲本土客服。
* **Globird Energy**：主打特殊时段免费用电套餐（如 *Four4Free* 每日特定时段免电费）及季节性 \$50–\$100 优惠码。
* **OVO Energy / Octopus Energy / Amber**：主打智能电表分时电价、EV 夜间超低谷充电费率与太阳能/储能电池实时批发价套利。

#### 第三级（高毛利扩展）：B2B 渠道白标与嵌入式合作（Model C）
* 澳洲本土经验表明，**搬家连接（Moving Home Connections）与房贷经纪（Mortgage Brokers / Property Managers）** 是能源切换转化率最高的场景（转化率可达 15%–25%，远高于普通自然流量的 5%–8%）。
* 当我们的确定性计价引擎与 PDF 报告在 C 端跑通后，可向悉尼本地华人/西人房产中介、物业管理公司（Strata / Property Management）、贷款经纪人提供 **联名账单体检报告（Co-branded PDF/HTML Savings Audit）**，按成功转网分成或收取 SaaS 授权费。

---

## 三、 技术架构核验：澳洲 AER CDR 开放能源 API 真实规范与 Agent-Native 演进

### 1. 权威数据源：AER CDR Energy Product Reference Data (PRD) 接口细节
经实时核验澳洲消费者数据标准（Consumer Data Standards, CDS），AER 托管于 `cdr.energymadeeasy.gov.au` 的公开产品参考数据接口具有以下技术特性（已同步更新至项目 `src/energy_agent/offer_collector.py`）：

1. **完全公开免认证（Unauthenticated Public Access）**：
   * 产品参考数据（PRD）不涉及个人隐私，**无需申请 ACCC CDR Accreditation 牌照，也无需 OAuth 用户令牌**，任何服务端程序均可直接调用。
2. **两阶段查询架构与强制版本请求头（`x-v` Header）**：
   * **注意**：直接在浏览器粘贴 URL 会报错 `406 Not Acceptable`，必须在 HTTP Header 中携带严格的 `x-v` 和 `x-min-v` 版本号：
   * **阶段 A — 获取零售商套餐列表（Get Generic Plans `v1`）**：
     ```http
     GET https://cdr.energymadeeasy.gov.au/{retailer-slug}/cds-au/v1/energy/plans?type=MARKET&fuelType=ELECTRICITY HTTP/1.1
     Host: cdr.energymadeeasy.gov.au
     x-v: 1
     x-min-v: 1
     Accept: application/json
     ```
   * **阶段 B — 获取单个套餐完整费率与折扣明细（Get Generic Plan Detail `v3`）**：
     * *重要变更*：CDS 标准已于 2024 年 11 月启用 `v3` 接口，并于 **2025 年 3 月正式废弃了旧版 `v2` 接口**。获取具体日供费、分时单价、阶梯气价、受控负载及条件折扣必须调用 `v3`：
     ```http
     GET https://cdr.energymadeeasy.gov.au/{retailer-slug}/cds-au/v3/energy/plans/{planId} HTTP/1.1
     Host: cdr.energymadeeasy.gov.au
     x-v: 3
     x-min-v: 1
     Accept: application/json
     ```
   * **阶段 C — 零售商标识（Retailer Slugs）动态发现**：
     * 可通过 `https://api.energymadeeasy.gov.au/refdata2?keys=organisations,thirdParties` 获取全部在册零售商编码（如 `agl`、`origin`、`energyaustralia`、`redenergy`、`ovoenergy`、`globird`、`alinta`、`amber`、`engie`、`powershop`、`momentum`、`dodo`、`lumo` 等）。

### 2. 借鉴 `LetsFG` 的 Agent-Native 架构演进
提案第 10 页参考了开源项目 **[LetsFG (`github.com/LetsFG/LetsFG`)](https://github.com/LetsFG/LetsFG)**。经核查，LetsFG 是一个 **Agent-Native（原生面向 AI Agent）** 的机票与酒店搜索预订引擎，其核心设计哲学是：
* 除了提供人类可读的界面外，原生提供 **CLI 工具、Python SDK 与 MCP（Model Context Protocol）Server**，让 Claude Code、Cursor、Antigravity 等 AI 助手能够直接调用其确定性搜索引擎完成比价。
* **对本项目的启发**：我们的 `energy-savings-agent` 已经具备了零依赖 CLI（`energy_agent.cli`）与确定性计价内核（`PricingEngine`）。下一步可以轻量封装一个 **MCP Server 接口**，让任何使用 AI 助手的澳洲用户只需把电费单 PDF 拖进聊天框，即可直接调用本引擎完成分毫不差的澳洲电费核验！

---

## 四、 商业落地讨论清单（Stage 1 $\rightarrow$ Stage 3 关键决策点）

为了将项目从“可运行原型”推进到“真实 NSW 家庭产生第一笔转网收入”，我们需要共同敲定以下 **4 个核心商业与产品落地问题**：

### 议题 1：首批种子用户（15–30 户 NSW 家庭）的获取与切入点（GTM Stage 1 & 2）
* **方向 A（悉尼本地高痛点社群切入）**：聚焦悉尼独立屋（House，带光伏 Solar 或泳池受控负载，账单高、单次可省 \$400+）或悉尼华人社区（小红书/微信群/本地论坛，提供**中英双语**账单解读，解决英文复杂能源合同阅读障碍）。
* **方向 B（极客与折扣社区切入）**：在澳洲本土羊毛社区（如 **OzBargain**、Reddit `r/AusFinance` / `r/sydney`）发布免费的“一键检测你的套餐是否掉入次年熟客税陷阱（Loyalty Tax Detector）”工具。
* **方向 C（B2B2C 渠道合作）**：直接联合悉尼本地的太阳能安装商（Solar Installers）、房贷 Broker 或租房中介，作为他们送给客户的免费增值体检服务。

### 议题 2：账单持续接入体验（如何让用户不用每次手动找 PDF？）
* 借鉴 *Bill Hero* 的成功经验，除了在 Web 端手动上传 PDF 外，是否要在 Phase 2 增加 **“专属账单转发邮箱（Email Forwarding，如 `check@yourdomain.com.au`）”** 或 **“手机拍照上传（Gemini 多模态视觉直接解析纸质账单照片）”**？

### 议题 3：变现闭环的冷启动选择（如何拿到第一笔佣金？）
* **路径 1（最快见效）**：注册公司实体（ABN），申请加入 **Commission Factory** 并接入 **Econnex (CIMET)** 联盟计划，将我们系统中匹配到的合作零售商链接映射为追踪链接（同时对非合作零售商保留 `energymadeeasy.gov.au` 官方直连，坚守客观排名铁律）。
* **路径 2（去中心化冷启动）**：在未完成 B2B 联盟签约前，先挂载 OVO Energy、Amber、Globird 等挑战者品牌的公开 Referral Code 测试真实点击转化率（CTR & Switch Intent）。

### 议题 4：真实账单与实时 CDR 数据同步的工程优先级
* 下一步代码迭代，您希望优先实现：
  1. **全量拉取并缓存 AER CDR 实时 NSW 套餐库脚本**（把 NSW 三大电网的实时在线套餐同步到本地 SQLite 与 JSON）；
  2. **支持手机照片/扫描件账单的多模态直接解析（Gemini Vision）与中英双语报告切换**；
  3. **封装 MCP Server**（像 LetsFG 一样支持在 Antigravity / Claude 中直接用自然语言调用比价工具）？
