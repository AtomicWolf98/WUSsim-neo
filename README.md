# WUSsim neo

`neo` 是整理后的独立 6G DL-WUS Case 1 仿真项目，只包含当前可运行源码、项目内虚拟环境安装脚本、冻结配置、最终结果和系统化文档。旧工程、旧轮次结果与未进入现行主链的模块不在本目录。

英文说明：[README_EN.md](README_EN.md)；完整中文文档入口：[doc/00_DOCUMENT_INDEX_ZH.md](doc/00_DOCUMENT_INDEX_ZH.md)。

## 一键运行

环境：Windows 10/11，Python 3.11 或 3.12。

1. 双击 `setup.bat`：在 `neo/.venv` 创建项目专用虚拟环境并安装依赖。
2. 双击 `run_cases_1_to_6.bat`：运行 FTP3/IM × Case 1-1～1-6 × 8 配对 seed，共 96 个可审计 run。
3. 双击 `run_theory_audit.bat`：运行 64-seed 名义矩阵、十组敏感性和 128 个增益分解反事实。
4. 双击 `verify_project.bat`：检查代码、配置、哈希、全部 raw run、1248-run 理论审计和增益分解；最后应显示 `[neo] PASS`。

每个运行脚本只替换自己的固定结果目录，不会不断新增 round 文件夹。

## 关键结论

64-seed 名义配置下，Case 1-6 相对 Case 1-1 的收益为 FTP3 55.67%（95% CI 52.06～59.04%），IM 53.66%（51.34～55.76%）；完成率均为 100%。这些数字成立于 5 ms 独立 WUS period、2-symbol WUS occasion、8 ms post-data timer、1% 检测错误和公司 EE 测量功耗组合下。

反事实结果说明高收益主要不是 WUS 信号本身带来的：如果改用独立 WUS、但仍保留基线的 100 ms 活动尾巴，FTP3 功耗增加 36.60%，IM 增加 122.97%。把 tail 从 100 ms 缩至 8 ms 才贡献主要正收益；EE 测量再贡献 FTP3 7.45、IM 33.27 个基线百分点。4 ms WUS occasion 或受损检测可使稀疏 IM 变成负收益。

因此本项目适合讨论“timer、测量路径、检测和监测开销如何共同决定 UE 相对功耗”，不能把 55.67% 写成 3GPP 已同意的固定 WUS 收益。

## 结果入口

| 文件 | 内容 |
|---|---|
| `results/case1_to_6/summary.csv` | 12 行 8-seed 主矩阵摘要 |
| `results/case1_to_6/raw/*.json` | 96 个事件/动作/能量/包账本 raw run |
| `results/theory_audit/nominal_summary.csv` | 64-seed 主结论 |
| `results/theory_audit/sensitivity_summary.csv` | 十组单因素敏感性 |
| `results/gain_decomposition/gain_decomposition.csv` | 触发、100→8 ms timer、EE 测量的逐项分解 |
| `doc/10_RESULTS_GAIN_DECOMPOSITION_ZH.md` | 详细结果和非专家解释 |

## 结论边界

这是单 UE、连接态、相对功耗、抽象检测和固定服务率模型。它没有 PHY 波形/信道/BLER 校准、完整 Rel-19 协议、多 UE 调度、gNB 能耗或绝对电池寿命。Case 定义来自会议记录；很多数值来自公司提案或项目假设，文档已逐项标注。
