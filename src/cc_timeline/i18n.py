"""All user-visible text. Internal keys are English; English is the default language."""
import os

EN = {
    "app_title": "Claude Code time analysis",
    "all_projects": "all projects",
    "redacted": "(redacted)",
    "none": "none",
    "unmatched": "unmatched",
    # buckets
    "b_model": "Model generation", "b_tool": "Tool execution",
    "b_bg": "Background / other sources", "b_user": "Waiting for you",
    "b_idle": "Away (gap over threshold)",
    # background item kinds
    "origin_notify": "Background task notification",
    "origin_peer": "Message from another session/agent",
    "origin_sdk": "App auto-submit (sdk)",
    "origin_other": "Other source ({origin})",
    "item_agent": "Waiting for {agent} report",
    "item_unmatched": "{label} (unmatched)",
    "item_wake": "Wake-up (source not recorded, previous event {prev})",
    # report phase groups
    "g_think": "Thinking", "g_code": "Writing code", "g_gen": "Other generation",
    "g_test": "Test / build", "g_poll": "Polling / waiting", "g_read": "Reading", "g_other": "Other",
    # agent phases (CLI --agents)
    "p_think": "Thinking", "p_code": "Writing code (Edit/Write content)",
    "p_gen": "Other generation (commands, reports)", "p_test": "Test / build / check",
    "p_poll": "Waiting / polling (sleep loop)", "p_read": "Reading", "p_edit": "Editing files",
    "p_script": "Running scripts (node/python...)", "p_git": "git",
    "p_install": "Installing dependencies", "p_other_cmd": "Other commands", "p_other_tool": "Other tools",
    # CLI table
    "col_start": "Start", "col_project": "Project", "col_session": "session", "col_wall": "Total",
    "col_active": "Active", "col_model": "Model", "col_tool": "Tool", "col_bg": "Bg",
    "col_user": "You", "col_idle": "Away", "col_top": "Top item",
    "sum_model": "model", "sum_tool": "tools", "sum_bg": "background / other", "sum_user": "waiting for you",
    "sum_line": "{n} sessions (excluding away) {busy}: ",
    "sep": ", ",
    "api_errors_line": "API error replies (429 etc.): {n}",
    "tools_hdr": "Tools and background sources", "calls": "Calls", "total": "Total",
    "avg": "Avg", "share": "Share",
    "no_sessions": "No matching sessions found",
    "no_agent_files": "No subagent transcripts in these sessions",
    "agent_head": "{label}: {runs} runs, total {total}, avg {avg} per run",
    # CLI detail
    "d_head": "session {id}  project {project}  start {start}",
    "d_totals": "Total {wall}, user prompts {prompts}, API errors {errors}",
    "d_model": "Model", "d_tool": "Tool execution", "d_bg": "Waiting bg/other", "d_user": "Waiting for you",
    "d_idle": "Away (gaps over threshold)",
    "d_slowest": "Slowest {n} steps:",
    "report_written": "Report written to {path}",
    "csv_written": "CSV written to {path}",
    "by_day": "Day", "by_week": "Week (ISO)", "by_project": "Project", "by_session": "Session",
    "col_n": "N",
    # HTML
    "period_days": "last {days} days", "period_from": "since {since}",
    "period_until": "until {until}", "period_range": "{since} to {until}",
    "trace_head": "── {gap} {cat} {item} ({start} → {end})",
    "meta": "{project} · {period} · {n} sessions · API errors {errors} · generated {generated}",
    "tile_active": "Active time", "tile_idle": "plus {t} away",
    "daily_title": "Daily time allocation",
    "daily_sub": "Each session is assigned to its start date; excludes away time (single gaps over the threshold).",
    "items_title": "Where the time goes",
    "items_sub": "Tool: a tool was actually running. Background: the main thread was idle, waiting for an agent, "
                 "another session or the app. Top 15.",
    "agents_title": "Agent internal time",
    "agents_sub": "Read from each subagent's own transcript; parallel agents add up, so the sum can exceed "
                  "the time the main thread waited.",
    "dur_title": "Average duration per run",
    "dur_sub": "By agent start date; to compare before and after config changes.",
    "think_title": "Thinking share",
    "think_sub": "Share of agent internal time spent in model thinking.",
    "sessions_title": "Sessions", "sessions_sub": "Click a column header to sort.",
    "table": "Table", "no_data": "No data", "no_agent_data": "No agent data",
    "k_tool": "Tool", "k_bg": "Background",
    "tip_daily": "{bucket}: {t} ({p})<br>Day total {total}",
    "tip_item": "{kind} · {n} calls · total {t}<br>avg {avg}",
    "tip_agent": "{t} ({p})<br>avg per run {avg}",
    "tip_trend": "{n} runs · avg {avg}<br>thinking {think}",
    "runs": "{n} runs", "avg_x": "avg {t}", "total_x": "total {t}",
    "h_date": "Date", "h_total": "Total", "h_item": "Item", "h_type": "Type", "h_calls": "Calls",
    "h_time": "Total time", "h_avg": "Avg", "h_agent": "Agent", "h_runs": "Runs",
    "h_start": "Start", "h_project": "Project", "h_session": "session", "h_wall": "Total",
    "h_errors": "API errors", "h_top": "Top item",
}

ZH = {
    "app_title": "Claude Code 時間分析",
    "all_projects": "全部專案",
    "redacted": "（已遮蔽）",
    "none": "無",
    "unmatched": "未對應",
    "b_model": "模型生成", "b_tool": "工具執行", "b_bg": "背景／其他來源", "b_user": "等你",
    "b_idle": "離開（超過門檻的空檔）",
    "origin_notify": "背景工作完成通知",
    "origin_peer": "其他 session／agent 傳訊",
    "origin_sdk": "App 自動送出（sdk）",
    "origin_other": "其他來源（{origin}）",
    "item_agent": "等 {agent} 回報",
    "item_unmatched": "{label}（未對應）",
    "item_wake": "喚醒（來源未記錄，前一事件 {prev}）",
    "g_think": "思考", "g_code": "寫程式碼", "g_gen": "其他生成",
    "g_test": "測試／建置", "g_poll": "輪詢等待", "g_read": "讀取", "g_other": "其他",
    "p_think": "思考", "p_code": "寫程式碼（產生 Edit／Write 內容）",
    "p_gen": "其他生成（下指令、寫回報）", "p_test": "測試／建置／檢查",
    "p_poll": "等待／輪詢（sleep 迴圈）", "p_read": "讀取", "p_edit": "改檔",
    "p_script": "執行腳本（node／python…）", "p_git": "git",
    "p_install": "安裝依賴", "p_other_cmd": "其他指令", "p_other_tool": "其他工具",
    "col_start": "開始時間", "col_project": "專案", "col_session": "session", "col_wall": "總時長",
    "col_active": "活躍", "col_model": "模型", "col_tool": "工具", "col_bg": "背景",
    "col_user": "等你", "col_idle": "離開", "col_top": "最耗時項目",
    "sum_model": "模型", "sum_tool": "工具", "sum_bg": "背景／其他來源", "sum_user": "等你",
    "sum_line": "合計 {n} 個 session（扣掉離開）{busy}：",
    "sep": "、",
    "api_errors_line": "API 錯誤回覆（429 等）共 {n} 次",
    "tools_hdr": "工具與背景來源", "calls": "次數", "total": "總時間", "avg": "平均", "share": "占比",
    "no_sessions": "找不到符合條件的 session",
    "no_agent_files": "這些 session 沒有 subagent 逐字稿",
    "agent_head": "{label}：{runs} 次，合計 {total}，平均每次 {avg}",
    "d_head": "session {id}  專案 {project}  開始 {start}",
    "d_totals": "總時長 {wall}，使用者輸入 {prompts} 次，API 錯誤 {errors} 次",
    "d_model": "模型生成", "d_tool": "工具執行", "d_bg": "等背景／其他來源", "d_user": "等你回覆",
    "d_idle": "離開（超過門檻的空檔）",
    "d_slowest": "最慢的 {n} 步：",
    "report_written": "報表已寫入 {path}",
    "csv_written": "CSV 已寫入 {path}",
    "by_day": "日", "by_week": "週（ISO）", "by_project": "專案", "by_session": "Session",
    "col_n": "數量",
    "period_days": "最近 {days} 天", "period_from": "自 {since} 起",
    "period_until": "至 {until} 止", "period_range": "{since} 至 {until}",
    "trace_head": "── {gap} {cat} {item}（{start} → {end}）",
    "meta": "{project} · {period} · {n} 個 session · API 錯誤 {errors} 次 · 產生於 {generated}",
    "tile_active": "活躍時間", "tile_idle": "另有離開 {t}",
    "daily_title": "每日時間分配",
    "daily_sub": "各 session 依開始日期歸日；不含「離開」（單一空檔超過門檻）的時間。",
    "items_title": "時間花在哪",
    "items_sub": "工具：工具真的在執行。背景：主對話閒著，在等 agent、其他 session 或 App。前 15 名。",
    "agents_title": "Agent 內部時間",
    "agents_sub": "讀各 subagent 自己的逐字稿；平行執行的 agent 會疊加，所以總和可能大於主對話等待的時間。",
    "dur_title": "每次平均時長",
    "dur_sub": "依 agent 開始日期；用來看設定調整前後的變化。",
    "think_title": "思考占比",
    "think_sub": "agent 內部時間中，模型 thinking 所占的比例。",
    "sessions_title": "Sessions", "sessions_sub": "點欄位標題排序。",
    "table": "表格", "no_data": "沒有資料", "no_agent_data": "沒有 agent 資料",
    "k_tool": "工具", "k_bg": "背景",
    "tip_daily": "{bucket}：{t}（{p}）<br>當日合計 {total}",
    "tip_item": "{kind} · {n} 次 · 合計 {t}<br>平均 {avg}",
    "tip_agent": "{t}（{p}）<br>平均每次 {avg}",
    "tip_trend": "{n} 次 · 平均 {avg}<br>思考 {think}",
    "runs": "{n} 次", "avg_x": "平均 {t}", "total_x": "合計 {t}",
    "h_date": "日期", "h_total": "合計", "h_item": "項目", "h_type": "類型", "h_calls": "次數",
    "h_time": "總時間", "h_avg": "平均", "h_agent": "Agent", "h_runs": "次數",
    "h_start": "開始", "h_project": "專案", "h_session": "session", "h_wall": "總時長",
    "h_errors": "API 錯誤", "h_top": "最耗時項目",
}

LANGS = {"en": EN, "zh-TW": ZH}


def resolve_lang(value=None):
    value = value or os.environ.get("CC_TIMELINE_LANG") or "en"
    return "zh-TW" if value.lower().replace("_", "-").startswith("zh") else "en"


def labels(lang):
    d = dict(EN)
    d.update(LANGS.get(lang, {}))
    return d


def origin_label(L, kind, arg=None):
    if kind == "other":
        return L["origin_other"].format(origin=arg)
    return L["origin_" + kind]


def agent_name(L, label):
    return label if label else L["unmatched"]


def item_name(L, key):
    """Render a tool/background key from transcript.classify into display text."""
    if not isinstance(key, tuple):
        return key
    kind, arg = key
    if kind == "agent":
        return L["item_agent"].format(agent=arg)
    if kind == "unmatched":
        return L["item_unmatched"].format(label=origin_label(L, *arg))
    if kind == "wake":
        return L["item_wake"].format(prev=arg or L["none"])
    return origin_label(L, kind, arg)
