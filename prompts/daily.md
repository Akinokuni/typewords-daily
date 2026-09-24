# 今日任务 {{DATE}}

工作目录：`/root/typewords-agent`
输入：`{{RUN_DIR}}/due.json` —— 今天有 **{{N}}** 个到期词：{{WORDS}}

请按本会话系统提示中的「作业契约」（`prompts/CONTRACT.md`）完成：

1. 读 `{{RUN_DIR}}/due.json`
2. 写 `{{RUN_DIR}}/article.json`（标题 + slug + 段落数组 + 全部目标词的中文 gloss）
3. 自检：`./bin/selfcheck.sh {{RUN_DIR}}`
4. 报缺词 / 编译错就修正后重试，最多 3 次
5. 结束时一句话说明结果

不要打印、不要标记掌握、不要运行 `run.sh`。