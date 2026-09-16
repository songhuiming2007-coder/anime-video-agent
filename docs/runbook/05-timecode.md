# Runbook: 05 审时间码（停机点 3）

【🛑 强制物理停机点】：整条流水线唯一拥有硬阻断的人工质量闸门。未经 approve，渲染器拒绝启动。

## 核心操作
1. **生成并打开审看页**：
   ```bash
   python -m pipeline.review data/episodes/<期号>
   open data/episodes/<期号>/04-review.html
   ```
2. **人类审查要点（约 5 分钟）**：
   - 抓**“台词对了但画面不对”**：排查画外音错配（台词在说话，但镜头切给了路人或非在场角色）；
   - 检查片段抽帧（入点、中点、出点）是否平滑，有无切断关键动作。
3. **批准封板（显式动作）**：
   ```bash
   python -m pipeline.review data/episodes/<期号> --approve
   ```

## 产物与铁律
- 产物：`data/episodes/<期号>/04-clips.approved.json`
- **铁律**：`--approve` 必须是人类看后的显式执行命令。**严禁 Agent 自动跑 approve**！无 approved 文件后续渲染立即报错退出。
