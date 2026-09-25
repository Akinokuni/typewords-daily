# typewords-daily

每天 07:00 自动生成一份 **TypeWords 英文记忆讲义**（连贯短文 + 中文 Ruby 注释 + 右侧手写留白）并推送打印。讲义把「**今天要记的新词**」（App 同款算法：从词书 `lastLearnIndex` 起取 `perDayStudyNumber` 个、跳过已掌握与已学过的）和「**FSRS 到期复习词**」合并成**同一份**。

打印成功 = 这组词**「学了一遍」**（不是「掌握」）：`study_record.py` 用 App 同一个 `ts-fsrs` 库给每个词打分写进 `fsrsData`，之后由 FSRS 到期时间把词自动推进复习队列 —— 也就是「照样复习」。

**成功即静默**（定时任务不发任何消息）；**只在失败时**输出 ≤10 行摘要上报，交由 07:25 的修复任务接手。

## 架构：确定性脚本管流程，LLM 只写文章

```
cron 07:00 ──> bin/run.sh ──> bin/run.py（编排 + flock 防并发 + 幂等）
                                │
      ┌─────────────────────────┼─────────────────────────┐
      ▼                         ▼                         ▼
 fetch_new.py              pi (LLM)                  write_article.py
 TypeWords API 取今日新词 ──> 只写一篇文章 ────────> typst 编译 A4 PDF
 state/runs/<date>/due.json  article.json          assets/template.typ
                                │
                                ▼
                     print_pdf.py（OSS 上传 + MQTT 下发 + 等回执）
                                │
                                ▼
                     study_record.py（打印 = 学了一遍：写 FSRS 卡进复习调度，非 known）
                                │
                                ▼
                     verify.py（独立硬校验，不信任任何自述）
```

- **LLM 的职责边界**：`pi` 只负责「把今天的目标词写成一篇连贯、简单、符合单词水平的英文短文」，输出固定 schema 的 `article.json`。取数、排版、编译、上传、打印、标记、校验全部是确定性脚本 —— 行为每天可复现，失败面最小。
- **不信任自述**：`verify.py` 独立复核 PDF 是否存在/页数/字体嵌入/词覆盖率、OSS 对象的 ETag 与本地 MD5 是否一致、每个词的 FSRS 卡是否真的落到服务端（且 due 与本地算的一致）、打印回执是否真的来自打印机（而非自己下发的回声）。
- **学了一遍 ≠ 掌握**：`study_record.py` 只写 `fsrsData`（ts-fsrs 打分），绝不标 `known` —— 标 known 会被 App 从新词池永久排除并删掉 FSRS 卡。
- **降级可用**：pi 连续失败则用例句兜底出讲义并标 `DEGRADED`（有讲义、但非连贯短文）。

## 目录

```
bin/            run.sh(唯一入口) run.py(编排) fetch_new.py(今日新词) fetch_due.py(到期复习词)
                study_record.py(打印=学了一遍 → FSRS 卡) print_pdf.py mark_known.py(默认关闭)
                verify.py check_attention.py
tools/fsrs/     ts-fsrs@5 打分器 next.mjs（与 App 同一个库/同一套参数）
                selfcheck.sh(pi 自检) fetch_fonts.sh setup_github.sh drill_exitcode.sh
prompts/         daily.md(任务模板) CONTRACT.md(pi 作业契约，经 --append-system-prompt 注入)
.pi/SYSTEM.md   pi 的固定角色：流水线操作员
config/         workflow.yaml(运行参数) print.yaml.example env.sh.example
assets/         template.typ (+ fonts/ 需 fetch_fonts.sh 就位)
state/          运行态：runs/<date>/、history.jsonl、last_run.json、needs_attention.json
logs/ out/      日志与成稿 PDF
```

## 快速开始

```bash
# 1) 依赖（自带 venv，不污染系统 Python）
uv venv .venv && uv pip install --python .venv/bin/python oss2 paho-mqtt PyYAML

# 2) 字体（体积大，不入库）
./bin/fetch_fonts.sh

# 3) 配置
cp config/print.yaml.example config/print.yaml && chmod 600 config/print.yaml   # 填 OSS/MQTT 凭据
cp config/env.sh.example  config/env.sh  && chmod 600 config/env.sh             # 填 API key（可选，见下方说明）

# 4) pi 的 provider 凭据（本仓库不含密钥，放 pi 自己的凭据文件）
#    ~/.pi/agent/models.json   声明自定义 OpenAI 兼容 provider（baseUrl + 模型）
#    ~/.pi/agent/auth.json     存该 provider 的 api_key（0600）
#    ~/.pi/agent/settings.json 设 defaultProvider / defaultModel
#    当前默认：provider=qwen-maas → https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1
#              model=deepseek-v4.1-flash

# 5) 冒烟（不打印、不标记）
./bin/run.sh --dry-run --force

# 6) 全链路
./bin/run.sh --force
```

前置组件：`typst`（≥0.15）、`pi`（编码 Agent，**Node ≥22**）、`flock`、本机 MQTT broker 与订阅 `home/printer/tasks` 的打印端。

> pi 的密钥优先级：`--api-key` > `auth.json` > 环境变量 > `models.json`。流水线走 `auth.json`，
> 因此 cron 的极简环境（`env -i`）也能取到凭据；`config/env.sh` 里的 key 仅作环境变量回退。

## 定时任务（Hermes cron）

| 时间 | 作用 |
|---|---|
| `0 7 * * *` | `no_agent` + `script=~/.hermes/scripts/typewords-daily.sh`：成功 stdout 为空 → 静默；失败输出 ≤10 行摘要 → 直接发到对话 |
| `25 7 * * *` | `monitor=~/.hermes/scripts/typewords-attention.py` 门控：输出无变化不唤醒，`ATTENTION`/`DEGRADED` 才叫醒 Agent 诊断修复 |

监督口径（确定性、不含时间，便于门控判重）：

```
OK
DEGRADED <date> <reason>
ATTENTION <date> <stage> <error>
WARN no-print-ack-streak=<n>      # 连续 n 天(3/7)无打印回执
```

## 参数（config/workflow.yaml）

```yaml
word_source: new+due   # new = 今天要记的新词；due = 到期复习词；new+due = 合并成一份（默认）
max_words: 20          # 新词上限
max_due_words: 20      # 到期复习词上限
print: true            # 生成后推送打印
mark_known: false      # 打印后是否标已掌握 —— 永远保持 false（标 known 会把词从新词池永久剔除）
study_record: true     # 打印成功 = 学了一遍 → 写 FSRS 卡进复习调度
study_rating: good     # 记录打分：good/easy/hard/again（App 是同一套 ts-fsrs）
advance_index: false   # 是否像 App 那样推进 lastLearnIndex（默认 false，避免与 App 重复推进）
exclude_known: true    # 跳过已 known 的词（新词与到期词都跳过）
print_retries: 2       # 收不到回执时重发次数
print_ack_timeout: 45  # 等回执秒数（无回执不算失败，只记 ack:null）
pi: {provider: qwen-maas, model: deepseek-v4.1-flash, thinking: low, attempts: 2, timeout_seconds: 900}
```

## 已知坑（都已在代码里规避）

- **pi 需要 Node ≥22**：Node 20 会秒崩（`node:fs` 无 `globSync`）。`config/env.sh` 里 PATH 必须让 v22 优先。
- **打印回执非 retained**：`home/printer/status` 不重放，必须**先订阅再下发**，且要读**到终态**（`downloading → printing → success`，通常 5~8s）。
- **自身下发回声**：订阅 `home/printer/#` 时也会收到自己发到 `tasks` 的消息，判回执必须排除 task 通道并比对 `job_id`。
- **`pkill -f mosquitto_sub` 会杀掉自己所在的 shell**（模式匹配到自身命令行）→ 用 `pgrep -x` + 按 pid kill。
- **`known` 的副作用（红线）**：App 拼「今日新词」时会跳过 `known` 词（`store.getIgnoreWordsSet()`），标 known 还会删掉该词的 FSRS 卡 —— 所以**绝不能**标 known，否则等于把这批词从新词池里永久剔除。`mark_known` 默认 `false`。
- **「学了一遍」怎么落地**：App 学一个词时执行 `store.fsrsData[word] = new FSRS(store.fsrsParameters).next(card ?? createEmptyCard(), now, grade).card`（源码 `_id_-C3R0jkHo.mjs` / `useWordCollectPicker.mjs`）。本仓库用**同一个** `ts-fsrs@5`（`tools/fsrs/next.mjs`，参数直接读服务端 `setting.fsrsParameters`），把讲义里的词一次性写进 dict store 的 `fsrsData` —— 于是这些词有了下次到期时间，App / 服务端的 `filter=due` 之后就会把它们推回来复习。
- **写回方式**：`PUT /api/data/dict`，body `{"value": "<envelope JSON 字符串>"}`，envelope = `{val, version, updated_at}`（与 App 的 `dataSync` 完全一致）；`updated_at` 设为当前时间 → 浏览器下次同步会拉取这份（不会覆盖我们的改动）。
- **新词不重复推**：`fetch_new.py` 会跳过**已有 FSRS 卡**的词（= 已经学过的），所以即使 `lastLearnIndex` 不动，新词也会按天自然推进；`advance_index: true` 才像 App 那样显式推进进度。
- **`known` 与 FSRS 是两套数据**：`exclude_known` 让两类词集都跳过已掌握的词。
- **「今日新词」的算法来源**：`useWordCollectPicker.mjs` 的 `getCurrentStudyWord()` —— 从 `book.lastLearnIndex` 起、跳过 ignore 集（`known` ∪ `simpleWords`，`setting.ignoreSimpleWord=false` 时只用 `known`）、取满 `perDayStudyNumber` 个；`lastLearnIndex >= length-1` 时视为学完（无新词）。`bin/fetch_new.py` 与它逐条对齐；词书全量词条可从 `GET /api/export` 的 `dict.val.word.bookList[studyIndex]` 取到（含音标/释义/例句，无需逐词请求）。
- **pi 的作业契约**放在 `prompts/CONTRACT.md`（由 `--append-system-prompt` 注入），不使用 `AGENTS.md`。
- **打分语义**：App 按答题速度/错误数自动打分（`getGradeByWrongTimes`，阈值 `fsrsEasyLimit/GoodLimit/HardLimit`）；纸面学习没有这个信号，所以默认统一 `good`（= 认真过了一遍），可用 `study_rating` 改。
- **同日幂等**：`state/runs/<date>/study.json` 存在即不再重复写卡（`--force` 可重写），避免手动重跑把同一个词重复「学」两遍。

## 安全

`config/print.yaml` 与 `config/env.sh` 内含 OSS/MQTT 凭据与模型密钥，**已被 .gitignore 排除**，仓库里只有 `.example` 模板。运行期这两文件权限为 600，摘要/日志中不打印其值。