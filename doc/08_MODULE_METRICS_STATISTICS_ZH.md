# 模块建模：指标、统计与验收

## 1. 主要 KPI

| 指标 | 定义 |
|---|---|
| 平均功耗 | 观察窗总能量 / 观察窗时长。 |
| 节能增益 | `1 - sum(candidate energy) / sum(case 1-1 energy)`。 |
| 完成率 | 观察窗 cohort 中完成包数 / 到达包数。 |
| PDB 满足率 | 按项目 cohort 规则，有资格评估且按时完成的包数 / PDB eligible 数。 |
| 平均/P90 时延 | 已完成 cohort 的 `complete-arrival`。 |
| Goodput | 观察窗内成功服务 bit / 观察窗时长。 |
| MDR/FAR/FDR | 分别按 H1/H0/H2 计数；无分母时为 null。 |

节能必须与完成率、PDB 和时延同表。若某方案通过不服务业务来省电，完成率和 pending 会暴露该问题。

## 2. 配对估计

每个 candidate seed 与同 seed Case 1-1 配对。收益使用 ratio-of-sums，避免先算每 seed 百分比再平均造成小分母偏差。95% 置信区间通过 4000 次 seed 对 bootstrap；每次抽样同时抽取基线和候选的同一 seed 索引。

64-seed 名义矩阵用于主点估计。8-seed raw 矩阵用于事件级审计。敏感性使用 8 个独立 seed，只用于方向和风险识别，其区间通常更宽。

## 3. 增益分解

总收益包含交互，不能天然拆成唯一份额。项目使用一条明确顺序的配对反事实路径：

1. Case 1-1；
2. 独立 WUS，但保留 100 ms post-data tail；
3. 把 tail 从 100 ms 改为 8 ms，得到 Case 1-5；
4. non-EE 改为 EE，得到 Case 1-6。

每一步报告相对该步起点的降幅、配对区间和按 Case 1-1 归一的百分点贡献。因为模型非线性，负贡献或超过 100 个百分点的中间贡献并非错误，而是表示一个机制先增加成本、后续机制再抵消。只有端点总收益与路径无关。

## 4. 自动验收

`verify_neo.py` 检查：

- 96 个 raw run 和 12 行主汇总；
- 每个 raw run 的独立能量、包、检测和 goodput 重算；
- 冻结配置、profile 和关键源码哈希；
- 每周期恰好一次测量动作；
- Tx 与 `NEW_TRANSMISSION` 一一对应；
- PDSCH 时长等于成功 Tx 时长；
- 分段包 Tx 连续；
- 64-seed/敏感性行数、结果校验和与 1248 run manifest；
- 增益分解的 128 个反事实 seed、输入哈希、结果校验和和瀑布加和。

验收通过说明“当前代码可以重算并保持账本一致”，不等于 3GPP 校准通过。
