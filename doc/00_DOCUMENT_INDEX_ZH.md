# WUSsim neo 文档索引

本目录只描述 `neo` 当前交付版。旧工程、旧轮次目录和未进入本次 Case 1 主链的实验模块均不属于本版本。

建议按下列顺序阅读：

1. [01_PROJECT_STRUCTURE_ZH.md](01_PROJECT_STRUCTURE_ZH.md)：项目目标、目录结构、代码入口和结果入口。
2. [02_PROTOCOL_CASE_PARAMETERS_ZH.md](02_PROTOCOL_CASE_PARAMETERS_ZH.md)：Case 1-1～1-6 的会议定义，以及每个数值参数来自哪里。
3. [03_SYSTEM_SIMULATION_MODEL_ZH.md](03_SYSTEM_SIMULATION_MODEL_ZH.md)：一次 run 从业务到达到功耗和 KPI 的完整系统模型。
4. [04_MODULE_TRAFFIC_SERVICE_ZH.md](04_MODULE_TRAFFIC_SERVICE_ZH.md)：业务到达、队列、FIFO 服务和传输时长。
5. [05_MODULE_TRIGGER_STATE_MACHINE_ZH.md](05_MODULE_TRIGGER_STATE_MACHINE_ZH.md)：C-DRX、DCP、耦合 WUS、独立 WUS、timer 和因果事件。
6. [06_MODULE_DETECTION_PHY_BOUNDARY_ZH.md](06_MODULE_DETECTION_PHY_BOUNDARY_ZH.md)：检测随机模型、MDR/FAR/FDR，以及为什么本项目不能宣称 PHY 校准。
7. [07_MODULE_ENERGY_SLEEP_ZH.md](07_MODULE_ENERGY_SLEEP_ZH.md)：MR/LR 功耗、睡眠选择、转换能量和并行资源计费。
8. [08_MODULE_METRICS_STATISTICS_ZH.md](08_MODULE_METRICS_STATISTICS_ZH.md)：节能、时延、PDB、完成率、配对 bootstrap 和不变量检查。
9. [09_EXECUTION_PARAMETERS_OUTPUTS_ZH.md](09_EXECUTION_PARAMETERS_OUTPUTS_ZH.md)：如何安装、怎么跑、参数优先级和每个输出文件的用途。
10. [10_RESULTS_GAIN_DECOMPOSITION_ZH.md](10_RESULTS_GAIN_DECOMPOSITION_ZH.md)：最终结果、Case 1-5/1-6 的逐项增益来源、敏感性和结论解释。
11. [11_EVIDENCE_LIMITS_ZH.md](11_EVIDENCE_LIMITS_ZH.md)：本地资料索引、证据等级、允许和禁止的结论表达。

文档统一使用以下证据标签：

| 标签 | 含义 |
|---|---|
| `NORMATIVE` | 规范条文；只用于规范实际明确规定的内容。 |
| `MEETING_AGREEMENT` | 会议记录中的已记录结论或 Case 定义。 |
| `COMPANY_PROPOSAL` | 单家公司文稿中的评估参数或建议，不能写成 3GPP 已同意参数。 |
| `RESEARCH_REFERENCE` | 3GPP 技术报告中的研究用模型或参考值，不等于终端实测校准值。 |
| `ASSUMPTION` | 本项目为形成可运行实验而明确加入的假设。 |
| `PROJECT_METHOD` | 配对、预热、排空、统计和验证等项目方法。 |

结果阅读的第一原则是：节能值必须与完成率、PDB 满足率、时延、置信区间和假设边界一起报告。单独引用“55.67%”会遗漏决定该数字的 8 ms timer、EE 测量功耗、WUS 监测时长和检测假设。
