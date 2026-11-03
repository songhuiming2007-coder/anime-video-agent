# 做片常驻规则

本文件只讲做片，每次会话载入（常驻层第三份，D92）。仓库根的 `AGENTS.md` 是给开发用的 coding agent 的，你不加载它。

## 一、产物即状态

每一步的产物落在期目录 `data/episodes/<期>/`，人机交接走文件。进度看 `read_status`，不凭印象。任何一步失败，修好输入从那一步接着跑。

企划下的卷目录比普通期深一层（`data/episodes/<企划>/<卷>/`）。读仓库里的文件一律写 `"$AVA_ROOT/…"`，不用 `../` 往上数层级。

`data/` 是外置盘上的数据目录，不在 git 里，期目录里跑 `git diff` / `git log` 只会报错；比对稿件前后版本用 `_agent/script-history/` 的留底配 `diff`。

## 二、流程总图

一期 01→09。停机点 02.5 / 03.5 / 05 / 09 由人批。耗时在改稿、顺听、画面、封面四个环。选题会话人定期名，普通期这时不定 BGM。01 起草前贴人看。02 设计稿→笔记→草稿→`check_script`→02.8。03 问人本地或云端。03.5 人耳。04/05 排片、人点名 BGM。06/07 渲染质检。08 先定字再出图。09 以 `read_status` 为准。补素材不固定步骤。重开会话先 `read_status`。企划期见 `project.md`。

## 三、做片期代码只读（Code Freeze）

做片期间 `pipeline/`、`config/`、`tests/` 只读。`bash` 没有卡不等于允许。例外四条，经 `run_pipeline` 发、弹卡由人批：读音表（`corrections global`）、标定值（`calibration set`）、曲库登记（`bgm register`）、云端连接（`cloud connect`）。写错曲名改 `01-topic.md` 的 BGM 字段。确信是引擎缺陷时 `run_pipeline tickets open` 开单，不自己改代码。`--why` 必填。

## 四、失败处理

诚实失败优于凑合交付。源抓不到走升级链：`web_fetch` → `crawl`（反爬开 stealth）→ `browser`（独立 profile），不许退回简略百科。不硬塞不匹配的画面。检索落空走 `查询` → `备选` → `配音` 原文，上一级够格就停；三级都不及格就交人。

## 五、记忆两层

跨系列通用的教训进全局记忆（`write_memory`）或 `config/voice.json`；只属于本系列的用 `target="project"` 进企划记忆。
