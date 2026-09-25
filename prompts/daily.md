# 今日任务 {{DATE}}

工作目录：`{{AGENT}}`
输入：`{{RUN_DIR}}/due.json` —— 今天讲义共 **{{N}}** 个词：新词 {{N_NEW}} 个 + 到期复习 {{N_REVIEW}} 个

新词（第一次学，务必讲清楚、给足语境）：{{NEW_WORDS}}
到期复习词（昨天/前几天学过，今天该巩固）：{{REVIEW_WORDS}}

请按本会话系统提示中的「作业契约」（`prompts/CONTRACT.md`）完成：

1. 读 `{{RUN_DIR}}/due.json`（每个词带 `src` 字段：`new`=新词，`review`=到期复习）
2. 写 `{{RUN_DIR}}/article.json`（标题 + slug + 段落数组 + 全部目标词的中文 gloss）
3. 自检：`./bin/selfcheck.sh {{RUN_DIR}}`
4. 报缺词 / 编译错就修正后重试，最多 3 次
5. 结束时一句话说明结果

不要打印、不要标记掌握、不要记录学习、不要运行 `run.sh`（打印成功后的「学了一遍」记录由 run.py 统一负责）。