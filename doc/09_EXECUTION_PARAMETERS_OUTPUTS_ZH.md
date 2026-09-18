# 安装、参数、Case 运行与输出

## 1. 一键安装

在 Windows 双击 `setup.bat`。脚本只在 `neo/.venv` 创建虚拟环境，安装 `requirements.txt`，以 editable 模式安装当前项目，并校验 `sixg_case1_v1.json`。支持 Python 3.11/3.12；`.venv` 不进入 Git 或压缩交付。

## 2. 运行顺序

1. `run_cases_1_to_6.bat`：整体替换 `results/case1_to_6`，运行 96 个主实验。
2. `run_theory_audit.bat`：整体替换 `results/theory_audit`，运行 1248 个收敛/敏感性实验；随后整体替换 `results/gain_decomposition`，运行 128 个反事实实验。
3. `verify_project.bat`：编译并验证配置、主结果、理论审计和增益分解。

主实验约为 `2 traffic × 6 case × 8 seed`。理论名义矩阵为 `2 × 6 × 64 = 768`；十个敏感性切片为 `10 × 2 × 3 × 8 = 480`。增益分解另运行 `2 × 64 = 128` 个“独立 WUS + 100 ms tail”反事实。

## 3. 每个 Case 如何配置

| Case | C-DRX grid | 低功耗监测 | MR 活动 timer | 测量 |
|---|---|---|---|---|
| 1-1 | 开 | 无 | 100 ms inactivity | non-EE |
| 1-2 | 开但由 DCP 结果门控 | 2 × 2-symbol DCP/cycle | 100 ms inactivity | non-EE |
| 1-3 | 开但由 WUS 结果门控 | 2 × 2-symbol WUS/cycle | 100 ms inactivity | non-EE |
| 1-4 | 同 1-3 | 同 1-3 | 100 ms inactivity | EE |
| 1-5 | 关 | 每 5 ms 一个 2-symbol WUS | 8 ms WUS/post-data | non-EE |
| 1-6 | 关 | 同 1-5 | 8 ms WUS/post-data | EE |

所有 Case 共享同一业务 seed、服务率、窗口和检测主参数。Case 间只按注册表和冻结配置改变触发与测量轴。

## 4. 参数修改位置

| 想修改的内容 | 文件 | 注意事项 |
|---|---|---|
| WUS period/timer/gap/MO、功耗、测量、服务率、相位、预热/排空 | `configs/case1_company_eval_v1.json` | 修改后旧结果哈希失效，必须重跑三个 BAT。 |
| FTP3/IM 到达、包长、PDB | `wus_next/traffic/profiles/*.json` 与 `sixg_case1_v1.json` | 两处来源桥接需保持一致；校验器会检查叶子元数据。 |
| Case 触发/测量轴 | `wus_next/evidence/case_registry.json` | 这是会议定义映射，不应为追求结果而改。 |
| 检测场景/敏感性列表 | `run_theory_audit.py` | 新场景应写清证据等级和只改变的一个参数。 |

不要直接编辑 CSV/JSON 结果来“修正”排序。代码、配置或来源改变后应重新运行并让 manifest 与 `SHA256SUMS.txt` 自动更新。

## 5. 输出文件

### `results/case1_to_6`

- `summary.csv`：12 行主汇总，适合表格查看。
- `raw/*.json`：96 个完整 run；含 events/actions/intervals/increments/tx/packets/trials/summary。
- `case_summary.json`：按业务和 Case 聚合，并保存 paired comparison。
- `verification.json`：每个 raw run 的独立重算状态。
- `manifest.json`：配置快照、seed、来源哈希和声明边界。
- `SHA256SUMS.txt`：目录内结果文件校验和。

### `results/theory_audit`

- `nominal_seed_metrics.csv`：768 行 seed 级名义指标。
- `nominal_summary.csv`：12 行 64-seed 主结论。
- `sensitivity_seed_metrics.csv` / `sensitivity_summary.csv`：十个单因素切片。
- `manifest.json`：1248 run 的输入、源码和限制。

### `results/gain_decomposition`

- `counterfactual_seed_metrics.csv`：128 个 100 ms tail 反事实的 seed 级功耗。
- `gain_decomposition.csv`：触发、timer、1-5 总计、EE、1-6 总计的瀑布分解。
- `energy_component_breakdown_8seed.csv`：从 raw 区间重算的 MR/LR/转换功耗构成，用于解释，不替代 64-seed主值。
- `manifest.json` / `SHA256SUMS.txt`：路径依赖警告、输入哈希和结果校验。

## 6. 成功标准

最终控制台必须出现 `[neo] PASS`。若任何哈希、行数、账本、Tx 映射或增益分解加和失败，验收返回非零。输出排序不影响 PASS；例如敏感性下 1-6 为负仍可通过，只要计算和证据链正确。
