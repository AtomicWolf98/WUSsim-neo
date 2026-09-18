# 项目结构与运行入口

## 1. 项目目标

`neo` 是一个单 UE、连接态、Case 1-1～1-6 的相对功耗与时延仿真项目。它适合用来讨论“不同 WUS/C-DRX 机制在一组明确假设下如何改变 UE 主接收机活动时间、低功耗接收机开销、包时延和 PDB”。它不是链路级 BLER 仿真、完整 Rel-19 协议栈、网络级调度器或一致性测试工具。

## 2. 交付目录

```text
neo/
├─ README.md / README_EN.md       中英文快速入口
├─ setup.bat                      创建项目内 .venv 并安装依赖
├─ run_cases_1_to_6.bat           运行 96 个可审计主实验
├─ run_theory_audit.bat           运行 64-seed 收敛、敏感性和增益分解
├─ verify_project.bat             编译、配置、结果和账本验收
├─ requirements.txt / pyproject.toml
├─ run_cases_1_to_6.py            主实验矩阵和汇总
├─ run_theory_audit.py            收敛与十组单因素敏感性
├─ analyze_gain_decomposition.py  1-5/1-6 反事实增益分解
├─ verify_neo.py                  独立验证入口
├─ configs/
│  └─ case1_company_eval_v1.json  当前冻结的数值评估配置
├─ wus_next/
│  ├─ contracts/                  JSON 数据合同和校验
│  ├─ core/                       时间区间、能量、睡眠、FIFO、包账本
│  ├─ evidence/                   Case 注册表和参数来源映射
│  ├─ integration/runner.py       系统事件编排和一次 run
│  ├─ metrics/                    KPI、统计、配对比较和独立重算
│  ├─ policy6g/                   Case 轴、动作、状态机和策略参数
│  ├─ profiles/sixg_case1_v1.json
│  └─ traffic/                    FTP3/IM 到达模型和随机流
├─ results/
│  ├─ case1_to_6/                 8-seed 主实验及 96 个 raw JSON
│  ├─ theory_audit/               64-seed 名义结果和敏感性摘要
│  └─ gain_decomposition/         128 个反事实 run 的紧凑分解结果
└─ doc/
   ├─ 00～11 中文文档
   └─ references/                 本文引用的本地提取文本
```

`.venv`、`__pycache__` 和 `*.egg-info` 都是本地可再生文件，受 `.gitignore` 排除，不属于交付内容。

## 3. 代码调用关系

主入口 `run_cases_1_to_6.py` 读取：

- `wus_next/profiles/sixg_case1_v1.json`：Case 列表、业务、C-DRX 和检测基础参数；
- `configs/case1_company_eval_v1.json`：本轮实际使用的 WUS、测量、功耗、相位、服务率和统计参数；
- `wus_next/evidence/case_registry.json`：Case 1-1～1-6 的触发和测量轴；
- `wus_next/integration/runner.py`：为每个业务、Case、seed 生成一条完整因果时间线。

`runner.py` 调用 `traffic` 生成同 seed 业务，调用 `policy6g` 产生监听/唤醒/测量动作，再调用 `core` 建立互斥时间区间、服务队列和能量账本，最后由 `metrics` 计算并独立复核 KPI。

## 4. 三类结果各自回答什么

| 结果集 | 用途 | 规模 |
|---|---|---:|
| `case1_to_6` | 保留完整 raw 事件、动作、区间、传输和包结果，便于审计单次仿真 | 2 业务 × 6 Case × 8 seed = 96 run |
| `theory_audit` | 降低 seed 波动并检查关键假设；只留 seed 级指标 | 768 名义 run + 480 敏感性 run = 1248 run |
| `gain_decomposition` | 在 1-1 和 1-5 之间插入“独立 WUS + 100 ms 尾巴”反事实，分开触发、timer 和 EE 测量 | 2 业务 × 64 seed = 128 新 run |

主结论优先使用 64-seed `nominal_summary.csv`；需要追查一个具体区间或包时使用 8-seed raw JSON；解释增益来源使用 `gain_decomposition.csv`。三者用途不同，不能把 8-seed 点估计当作更高精度主结论。
