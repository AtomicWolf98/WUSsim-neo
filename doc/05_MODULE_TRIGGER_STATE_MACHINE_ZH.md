# 模块建模：触发、timer 与状态机

## 1. 代码位置

- `wus_next/policy6g/axes.py`：把 Case 注册表变成明确的触发/测量轴。
- `wus_next/policy6g/strategies.py`：把业务 C-DRX 与冻结配置合成运行参数。
- `wus_next/policy6g/machine.py`：纯状态机，输入一个事件并输出动作。
- `wus_next/integration/runner.py`：把动作落实为监测区间、检测结果和服务窗口。

状态机不直接积分能量；它只决定“何时监测、何时启动/重启 timer、何时允许主接收机活动、何时申请睡眠”。能量由 `core` 按动作结果统一计账。

## 2. Case 的触发几何

### 1-1：C-DRX only

每个 C-DRX cycle 开始有 8 ms on-duration。实际新传后从 Tx 开始/重启 100 ms inactivity timer。只要队列和服务窗口允许，窗口内可连续服务；每个新传再次延长 timer。

### 1-2：C-DRX + DCP

每个周期用两个 2-symbol DCP occasion 进行低功耗监测，两个 occasion 位于连续 0.5 ms slot。检测为 wake 后，从完整监测窗结束再加 3 ms，打开 C-DRX on-duration。模型不同时无条件打开原周期 on-duration，避免基线监听和门控监听重复计费。

### 1-3/1-4：耦合 DL WUS

几何与 1-2 相同，但监测类型为 WUS。1-3 使用 non-EE 测量，1-4 使用 EE 测量。当前冻结参数下 1-2 与 1-3 使用相同检测概率和相同 proxy 功耗，所以数值相同；这不表示 DCP 与 WUS 在真实 PHY 上等价。

### 1-5/1-6：独立 DL WUS

不再使用 C-DRX 周期 on-grid。每 5 ms 监测一个 2-symbol WUS occasion；有目标包且检测成功时，等待 10 ms 后打开 8 ms PDCCH/服务窗口。实际新传会把 8 ms post-data timer 重新计时。1-5 使用 non-EE 测量；1-6 使用 EE 测量。

## 3. timer 的因果规则

gNB 看到包到达不等于 UE 已经接收新传，所以包到达不能直接延长 UE timer。只有服务窗口内确实调度成功、产生 `NEW_TRANSMISSION` 时才重启：

- 1-1～1-4：C-DRX inactivity timer = 100 ms；
- 1-5/1-6：WUS/post-data timer = 8 ms。

这是当前收益最敏感的结构差异。反事实实验把 1-5 的 timer 改回 100 ms 后，独立 WUS 反而更耗电，说明“独立 WUS”本身不是主收益来源。

## 4. 监测条件冻结

WUS occasion 开始时记录：

- `H0`：没有本 UE 目标；
- `H1`：有本 UE 目标；
- `H2`：有其他 WUS 目标。

条件在 occasion 开始时冻结。检测结果到达之前的新包只影响后续 occasion，不能把一个已经开始的 H0 改成 H1。这样避免使用未来信息。

## 5. 测量事件

测量日历每 160 ms 开一个 2 ms 窗口，每个周期只产生一次测量动作。non-EE 测量是 MR 活动：与 PDCCH/PDSCH 重叠时按同一 MR 上的最大功率合并，并切断睡眠间隙。EE 测量进入独立增量能量账本，不强制 MR 醒来。

## 6. 状态机不代表完整协议栈

状态机实现的是 Case 评估所需的受限动作集合。它没有完整 TS 38.321 MAC 状态、RRC 配置流程、paging、HARQ、测量报告触发或异常恢复。文档中的 “C-DRX/DCP/WUS” 是机制级抽象，不能替代规范一致性状态机。
