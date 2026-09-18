# 协议场景、Case 定义与参数来源

## 1. Case 定义

Case 轴来自 `RAN1#126 Chair Notes` 第 10.6 节的 Case 1 列表，项目在 `wus_next/evidence/case_registry.json` 中逐行登记。这里的“Case 存在及其轴”属于 `MEETING_AGREEMENT`；具体 timer、功耗和检测数值仍可能是公司提案或项目假设。

| Case | 触发路径 | 服务小区测量 | 本项目解释 |
|---|---|---|---|
| 1-1 | C-DRX only | non-EE | 基线；周期 on-duration，成功新传后重启 100 ms inactivity timer。 |
| 1-2 | C-DRX + DCP | non-EE | DCP 短监测门控 C-DRX on-duration；DCP 功耗用 WUS 功耗作 proxy。 |
| 1-3 | C-DRX + DL WUS | non-EE | 耦合 WUS 门控 C-DRX on-duration。 |
| 1-4 | C-DRX + DL WUS | EE | 与 1-3 相同触发，服务小区测量走 EE 路径。 |
| 1-5 | 独立 DL WUS | non-EE | 不使用周期 C-DRX on-grid；每 5 ms 监测独立 WUS，成功后等待 10 ms，并打开/重启 8 ms 主接收机窗口。 |
| 1-6 | 独立 DL WUS | EE | 1-5 加 EE 服务小区测量。 |

Case 1 不包含邻区 RRM 测量。Case 2 未进入本版本代码和结果。

## 2. 冻结数值及证据等级

以下为 `configs/case1_company_eval_v1.json` 实际使用值。

| 参数 | FTP3 | IM | 证据等级 | 解释 |
|---|---:|---:|---|---|
| C-DRX cycle | 160 ms | 320 ms | `MEETING_AGREEMENT` 场景输入 | 业务对应周期。 |
| C-DRX on-duration | 8 ms | 8 ms | `MEETING_AGREEMENT` 场景输入 | 周期内基线监听窗口。 |
| C-DRX inactivity | 100 ms | 100 ms | `MEETING_AGREEMENT` 场景输入 | 只由实际新传重启。 |
| 独立 WUS period | 5 ms | 5 ms | `COMPANY_PROPOSAL` | 10 个 0.5 ms slot。 |
| 独立 WUS/post-data timer | 8 ms | 8 ms | `COMPANY_PROPOSAL` | Case 1-5/1-6 高收益的核心参数。 |
| 独立 WUS 检测到主接收机可用延迟 | 10 ms | 10 ms | `COMPANY_PROPOSAL`/抽象映射 | 并非规范固定值。 |
| 耦合 WUS/DCP gap | 3 ms | 3 ms | `COMPANY_PROPOSAL` | 从完整监测窗结束计。 |
| WUS/DCP 单次接收 | 2 OFDM symbol = 0.071429 ms | 同左 | `COMPANY_PROPOSAL` | 30 kHz SCS、0.5 ms slot 的明确换算。 |
| 耦合监测次数 | 2 次、相隔 1 slot | 同左 | `COMPANY_PROPOSAL` | 两个 2-symbol occasion。 |
| 服务小区测量周期/时长 | 160/2 ms | 160/2 ms | `COMPANY_PROPOSAL` | 来自 R1-2603659 Annex E；与 Table 12 组合是项目场景。 |
| 服务率 | 800,000 bit/ms | 同左 | `ASSUMPTION` | 抽象单 UE 无误码服务率。 |
| MDR/FAR/FDR | 1%/1%/1% | 同左 | `ASSUMPTION` | 无链路仿真校准。 |
| C-DRX/WUS/测量相位 | 0/0/0 ms | 同左 | `ASSUMPTION` | 主矩阵对齐；80 ms 和 2.5 ms 偏移另做敏感性。 |

## 3. 功耗参数

功耗单位是相对值，能量单位是“相对功耗单位 × ms”。

| 项目 | 值 | 证据等级 | 说明 |
|---|---:|---|---|
| MR micro/light/deep | 45 / 20 / 1.1 | `RESEARCH_REFERENCE` + 公司调整 | 参考 TR 38.840；deep 1.1 为公司评估值。 |
| PDCCH only | 100 | `RESEARCH_REFERENCE` | 主接收机监听。 |
| PDCCH + PDSCH | 300 | `RESEARCH_REFERENCE` | 成功传输占用片段。 |
| non-EE measurement | 120 | `COMPANY_PROPOSAL` | 作为 MR 活动并参与互斥区间。 |
| EE measurement | 18 | `COMPANY_PROPOSAL` | 独立增量账本。 |
| EE 每个边沿能量 | 15 unit·ms | `COMPANY_PROPOSAL` | 每次测量进入和退出各一次。 |
| WUS/DCP monitor | 15 | `COMPANY_PROPOSAL` / proxy | DCP 没有独立值，复用 WUS 值。 |
| LR idle | 0 | `ASSUMPTION` | 监测 occasion 之外低功耗接收机视为关断。 |
| light 转换能量/时间 | 100 unit·ms / 6 ms | `RESEARCH_REFERENCE` | 转换能量与占用时间分开处理。 |
| deep 转换能量/时间 | 450 unit·ms / 20 ms | `RESEARCH_REFERENCE` | 每个以活动结束的睡眠间隙计一次完整转换。 |

## 4. 参数优先级

运行时优先级从高到低为：命令/敏感性 `options` → `configs/case1_company_eval_v1.json` → `sixg_case1_v1.json` 的基础值。主矩阵由 `evaluation_options()` 把冻结配置显式传给状态机；因此 profile 中保留的旧基础测量叶子不会覆盖本次 160/2 ms 配置。

## 5. 不能写成协议事实的内容

8 ms timer、5 ms WUS period、10 ms 唤醒延迟、120/18 测量功耗、15 的 LR 监测功耗、800,000 bit/ms 服务率和 0 相位都不是 3GPP 统一规定的真值。结果可写成“在 R1-2603659 公司参数与本项目假设组合下的数值评估”，不能写成“3GPP 已证明 WUS 节能 55.67%”。
