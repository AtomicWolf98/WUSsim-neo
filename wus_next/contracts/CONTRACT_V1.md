# wus_next contract-v1

`contract_version` 为 `1.0`。跨任务边界只传 JSON-compatible `dict/list`；公共记录使用整数 `time_ns`，区间是 `[start_ns,end_ns)`，功率是相对 `power_unit`，能量是 `energy_unit_ms`。

## 安装与最小使用

在项目根目录创建全新 Python 3.11 或 3.12 虚拟环境，然后安装根目录 `requirements.lock`，再以 editable 模式安装本包：

```powershell
py -3.11 -m venv .venv-wus
.\.venv-wus\Scripts\python.exe -m pip install --upgrade pip
.\.venv-wus\Scripts\python.exe -m pip install -r requirements.lock
.\.venv-wus\Scripts\python.exe -m pip install -e . --no-deps
```

W00 不提供 `cli.py`，因此不应把上述命令误解为已经具备运行仿真的入口。W09 负责主入口。

```python
from wus_next import load_profile, validate_record

profile = load_profile("wus_next/profiles/sixg_case1_v1.json")
validate_record("Packet", packet_dict)
```

## 冻结规则

- 只允许 `metadata` 承载扩展字段；在记录顶层增加字段必须走 `contract_change.md` 和次版本迁移。
- 缺失指标使用 `null` 加原因，不使用 `0` 冒充无事件或未评估。
- `PacketOutcome` 的完成、丢弃、pending 语义和 bit 守恒由验证器保护。
- `TEST_ONLY` 只能用于模块和手工 fixture；发布/正式模式不得把它当成证据。
- `formal` profile 必须显式 `ENABLED`、所有 traffic `formal_eligible=true`，否则加载失败。

