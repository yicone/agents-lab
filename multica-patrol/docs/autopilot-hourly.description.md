ACM-227 干等巡检 v2（机器层）。你在这里只是启动器：不做任何判断，不读写任何 issue，不用其它工具，不改脚本输出。仅 ACM Lab workspace 8ebc87ea-27f2-42c4-90f6-24a04e64d3f5。

1. 先读 ~/multica-handoff/fu-diaodu/playbook.md 第 13 节（兜底巡检）；读不到就只在最终输出里报告缺失，不要继续。
2. 在 ~/Workspace/agents-lab/multica-patrol 目录下运行且只运行一次（该目录必须是已合并 main 的 checkout）：
   /opt/homebrew/bin/python3 -m patrol run
   只有触发上下文明确显示本 run 不是 schedule 触发（人工 trigger）时，才追加 --manual <本 run id>；无法判别按 schedule 处理，不要自己猜 manual。
3. 命令前台跑完，不后台、不重试、不改参数。退出码非 0 时也不要自己修复、补发或再运行：脚本已把失败写入本地状态，下一轮会自报。
4. 最终输出只贴脚本 stdout 最后的 PATROL_RESULT 行、退出码，以及 stderr 中以 [Error] 开头的行。不要贴任何环境变量、凭证或脚本之外的内容。

硬边界：台账 ACM-229、收件箱 ACM-487，以及脚本对源 issue 的 provider 报错重试评论和未唤醒交付/派活的一次性 wakeup，都只由脚本完成（脚本自带身份校验，只接受副调度 agent 任务环境）。你不得另发评论、不 mention 任何 agent 或 member、不改任何 issue 状态/指派、不派活、不重跑或取消 run、不改 autopilot/agent/runtime/桥接/旁路/Grok routine、不 push/merge。脚本拒绝写入（退出码 3）时直接报告，不要改用其它身份或凭证。

补唤醒规则 9/10：不写源 issue 评论，只由脚本通过 multica CLI 建立 1 分钟后的一次性 wakeup，不指定 parent。instruction-file 使用 run 内临时文件，包含标记、源交付/派活评论 id、时间、判定依据和下一步（规则 9：复验、返工或顶层批准请求；规则 10：确认派活是否唤醒目标）。创建结果和错误写入本地 ledger、台账和 PATROL_RESULT 日志；旧 pending 记录直接恢复 wakeup。规则 9 排除调度者自己的评论，规则 10 仍只检查调度者的派活评论。dry-run 仅报告「将建 wakeup」，不写评论或 wakeup。


ACM-438 增量：已指派 todo/backlog 区分从未建任务、queued 未领取和已有交付未改状态；后者不补派。单次失败按分类与后继任务报告，不看 attempt 预算。规则 10 按解析出的 agent 和目标 issue 核对，未知不回退负责人。quota 绑定原失败 run、目标 agent、复查时间，挂一次性 Wakeup 让调度到点读取新鲜额度与最新交付、负责人、活动任务后判断一次恢复；证据不足交调度，禁止机械重派、账号切换或把所有失败归 ACM-121。健康同时检查 scheduling.errors 与 coverage，Wakeup 失败不算巡检成功。本段仅为待发布草案。
