# 模块建模：业务、队列与服务

## 1. 代码位置

- `wus_next/traffic/generators.py`：Poisson 固定包长到达。
- `wus_next/traffic/streams.py`：按 `(run_seed, ue_id, stream_name)` 派生稳定随机流。
- `wus_next/traffic/profiles/FTP3.json`、`IM.json`：业务参数、来源和单位。
- `wus_next/core/service.py`：固定容量 FIFO 服务。
- `wus_next/core/ledger.py`：Packet、Tx 和 PacketOutcome 守恒。

## 2. 到达过程

FTP3 和 IM 都使用 Poisson 到达，等价地相邻间隔服从指数分布：

\[
\Delta T=-\bar T\ln U,\qquad U\sim Uniform(0,1).
\]

连续时间以双精度生成，使用 round-half-up 量化到整数 ns，单次到达量化误差不超过 0.5 ns。到达不吸附到 WUS、C-DRX 或测量网格。

| 业务 | 平均到达间隔 | 应用层抽象包长 | PDB | 观察窗内 64-seed 到达数 |
|---|---:|---:|---:|---:|
| FTP3 | 200 ms | 500,000 byte | 100 ms | 212 |
| IM | 2,000 ms | 100,000 byte | 300 ms | 103 |

这些包长是应用层抽象输入，不是一个 NR transport block。当前模型不加入 IP/RLC/MAC 重传、分段头开销、HARQ 或 BLER。

## 3. 随机流和配对

SHA-256 对 `namespace|seed|ue|stream` 派生 63-bit seed，再构造 NumPy `Generator`。`traffic`、`detection` 和 `other_wus` 使用不同流名，所以改变检测条件不会改变业务到达。对同一业务和 seed，六个 Case 的 Packet JSON 做规范化哈希；配对比较要求哈希完全相同。

## 4. FIFO 服务

服务窗口由协议状态机授权。窗口容量为：

\[
C=R_{service}\Delta t,
\]

其中 `R_service = 800,000 bit/ms`。500,000 byte FTP3 包在无中断时需要 5 ms；100,000 byte IM 包需要 1 ms。该速率是 `ASSUMPTION`，不包含调度开销和无线错误。

队首包可被部分服务；剩余 bit 留在队首，下一段从前一段结束处继续。窗口中有剩余容量时继续服务后续包。每条成功 Tx 同时建立 PDSCH 区间，并由验收检查：

- 成功 Tx 总时长等于 PDSCH 总时长；
- 每条 Tx 有且只有一个 `NEW_TRANSMISSION` 事件；
- 分段包的 Tx 段连续；
- Tx 的 bit 区间不重叠；
- `served_bits + remaining_bits = packet_size_bits`。

## 5. 到达、完成和 PDB

统计 cohort 是观察窗内到达的包。包可在排空期完成，完成时刻仍用于 delay/PDB；观察窗之前到达的预热包不进入最终 KPI。一个包满足 PDB 当且仅当成功完成且：

\[
t_{complete}-t_{arrival}\le PDB.
\]

没有完成的包不能被当作零时延样本；完成率必须与节能一起报告。
