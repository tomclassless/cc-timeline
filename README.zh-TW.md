# cc-timeline

[English](README.md)

你的 Claude Code session 時間花到哪去了？`cc-timeline` 讀 Claude Code 本來就存在你電腦上的逐字稿，把 session 的每一秒歸到 **模型**、**工具**、**背景等待**（subagent、其他 session、App）、**等你**或**離開**。也能往 subagent 裡面看，各類 agent 的時間花在思考、寫程式碼、讀取、測試還是輪詢。

<!-- screenshot -->

## 跟既有工具有什麼不同

- **ccusage** 只算 token 與花費，不算時間。
- **claude-devtools**、**claude-session-dashboard** 能視覺化 subagent，但沒有時間歸因。
- **官方 OpenTelemetry** 有 duration，但要事先開啟、不能回溯，也不拆等待。
- **yonk-labs/claude-session-analyzer** 有逐回合拆分，但沒有 subagent，也不處理 `turnOrigin`。

`cc-timeline` 可以回溯分析既有逐字稿，把「模型在工作」和「什麼都沒發生、在等某個東西」分開，並推算背景等待屬於哪一種 agent。

## 安裝

需要 Python 3.9 以上，只用標準函式庫。

```bash
# 不安裝，直接跑
uvx --from git+https://github.com/tomclassless/cc-timeline cc-timeline

# 安裝成工具
uv tool install git+https://github.com/tomclassless/cc-timeline

# 或用 pip
pip install git+https://github.com/tomclassless/cc-timeline
```

也可以用 `python -m cc_timeline`。

## 用法

```bash
cc-timeline                                  # 最近 7 天，每個 session 一行
cc-timeline --days 30 --project myapp        # 依專案資料夾名稱過濾
cc-timeline --session 3f2a                   # 單一 session：分項與最慢的步驟
cc-timeline --session 3f2a --trace 3         # 最慢 3 步前後的事件類型（不含內容）
cc-timeline --agents                         # 依 agent 類型看 subagent 內部
cc-timeline --html                           # ./cc-timeline-report.html
cc-timeline --html out.html --open           # 寫出並用瀏覽器打開
cc-timeline --json | jq .totals              # 輸出報表資料給腳本處理
cc-timeline --html --redact                  # 要貼截圖時用：專案名與 session id 匿名化
cc-timeline --lang zh-TW                     # 或設 CC_TIMELINE_LANG=zh-TW
```

逐字稿讀自 `$CLAUDE_CONFIG_DIR/projects`，沒設就是 `~/.claude/projects`（可用 `--root` 指定）。其他選項：`--idle-cap 分鐘`（預設 30）、`--top N`（預設 10）。

## 時間怎麼歸類

兩個事件之間的空檔，算給**下一個有意義的事件**。

| 類別 | 空檔的意思 |
| --- | --- |
| 模型 | 等模型生成回覆 |
| 工具 | 工具執行中（直到結果回來） |
| 背景 | 主對話閒著：在等 subagent、其他 session／agent、App（`sdk`）或被喚醒。對得到的會標出 agent 類型 |
| 等你 | 等使用者的回合（`turnOrigin: human`），或 `AskUserQuestion`、`ExitPlanMode`、`request_directory` 的回答 |
| 離開 | 單一空檔超過 `--idle-cap`（睡眠、午休、隔天 resume），不算進其他類 |

細節：

- 附屬事件（`queue-operation`、`api-request`、`attachment`、`system`、`file-history-snapshot`、`progress` 等）不切斷時間，空檔留給下一個真正的事件。
- 兩則不同 message id 的 assistant 訊息之間沒有 user 訊息或工具結果，代表 session 被喚醒。空檔在最後一個附屬事件處切開：之前算背景，之後算模型。
- 背景等待用 `origin.from` 或 `<task-id>` 對回啟動它的 Agent／Task 呼叫；對不到標「未對應」。
- subagent 逐字稿（`<session>/subagents/agent-*.jsonl`）依下一個區塊分類：thinking 區塊前是思考，Edit／Write 呼叫前是寫程式碼，其他 assistant 輸出是其他生成；工具時間分成讀取、測試／建置、輪詢、腳本、git、安裝、其他。非 tool_result 的 user 訊息會重設計時。
- Bash 指令用啟發式分類：含 `sleep` 是輪詢；含 test／build／lint 等字是測試；`npm install` 之類是安裝；否則看第一個指令（git、讀取類、腳本、其他）。

## 限制

- 逐字稿格式不是公開 API，可能會變；部分歸類依賴實際觀察到的欄位。
- 平行的 subagent 會重疊，各 agent 時間加總可能大於主對話實際等待的時間。
- 權限確認其實是在等你，但記錄上算在工具時間。
- Bash 分類是依指令文字的啟發式判斷。
- 終端機輸出的欄位對齊假設字元寬度一致，中文會有些歪。

## 隱私

全部在本機執行，不會送出任何東西。只用到時間戳、事件類型、工具名稱與 id，不會輸出對話內容。Bash 指令文字只用來分類，不會輸出。要分享截圖、報表或 JSON 前，用 `--redact` 取代專案名與 session id。

## 授權

MIT
