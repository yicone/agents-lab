ACM Lab 每日卡点分析（ACM-342 第二层）。每天 09:00 SGT 一次，只用一条评论汇报到 ACM-216。仅 ACM Lab workspace 8ebc87ea-27f2-42c4-90f6-24a04e64d3f5，不碰其它 workspace，不打印凭证。

1. 先读 ~/multica-handoff/fu-diaodu/playbook.md（全文，第 9 节必读）。
2. 取机器数据：cd ~/Workspace/agents-lab/multica-patrol && /opt/homebrew/bin/python3 -m patrol daily-pack --format json，输出存到本 run 工作目录（不是 /tmp）的 pack.json。该目录必须是已合并 main 的 checkout。取数失败：正文只写一行「[Error] 每日分析取数失败：<脚本给出的原因>」，直接跳到第 5 步。
3. 分析（只有这一步需要判断）：
   - 以 pack.json 的 big_matters 为起点，再按主题合并成「大事」。每件写：卡在哪、原因、谁该接手、推荐做法（给 1–2 个选项）。
   - 汇总只有两类：A「需要用户拍板」，B「副调度已推进」。没有的类别写「无」，不要凑数，不要写无事提醒以外的流水账。
   - 带「搁置」标签的 issue 在取数阶段已被排除，pack.json 里不会有；你自己查询或引用其它 issue 时也不得读取、列出或派活带该标签的事项（标签 ID 6ddf2d23-8de1-4be1-975d-b112c9c91a44）。pack.json 的 unresolved 段（ACM-215）只在 A 类里写一句「待用户裁定口径」。
   - 机器层告警里 approved_unassigned 只是标签线索：必须自己读 issue 描述和评论确认「用户明确批准现在可开工」且无撤销、暂缓、依赖未满足，才算有效；判断依据写进汇总。
   - 已升级项（pack.json escalated 段）要写明至今多久无人处理和建议的下一步。
4. 能按既有授权推进的，当场做并在 B 类注明做了什么、依据、结果：补缺失的 @mention、纠正父 issue 状态、按已有批准派活（派活仍严格按 playbook 第 3 节：先核实 assignee/active run/容量/额度，不重复派，--no-start 指派 + 一条 mention）。推送、合并、删除、花钱、方向性问题只整理成 A 类决定点，写清「要用户批准什么具体动作」。动作前重读实时状态，不凭 pack.json 快照动手。你的权限不因机器层告警而扩大。
5. 把汇总写到本 run 工作目录的 daily.md。第一行必须是大白话下一步：`需要你做：<具体动作>`，或 `不需要你做，下一步由 <agent 名称> 做：<具体动作>`；第二行起才写技术细节，哈希、命令、数字放细节里。脚本会检查第一行格式，不符合会拒绝发送。正文里不得出现 mention://agent/ 或 mention://member/ 链接，issue 链接用 mention://issue/ 即可；不超过 20000 字符），然后运行一次：
   cd ~/Workspace/agents-lab/multica-patrol && /opt/homebrew/bin/python3 -m patrol daily-post --file <daily.md 的绝对路径>
   同一天重复运行会返回 duplicate，这是正常的，不要重发。退出码 3 表示身份校验拒绝，直接报告，不要换身份或凭证。
6. 最终输出：PATROL 脚本的结果行、退出码，以及你在 B 类执行过的动作清单。除 daily-post 发出的那一条评论外，不要再往 ACM-216、ACM-229 发评论，不要 mention 任何人。
