#!/usr/bin/env python3
"""分析 Claude Code 逐字稿，算出每個 session 的時間花在哪。

只讀時間戳、訊息類型與工具名稱，不輸出任何對話內容。

用法：
  python3 cc_session_time.py                  # 最近 7 天
  python3 cc_session_time.py --days 30 --project networth
  python3 cc_session_time.py --session <id 前綴>   # 單一 session 列出最慢的步驟
"""
import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path.home() / ".claude" / "projects"
DEFAULT_REPORT = Path.home() / ".claude" / "usage-logs" / "session-report.html"
TEMPLATE = Path(__file__).with_name("cc_session_report.html")
# 這些工具的結果要等使用者回答，時間算「等你」
WAITS_ON_USER = {"AskUserQuestion", "ExitPlanMode", "mcp:ccd_director…request_directory"}
# 非使用者開啟的回合：前面的空檔是在等這個來源
ORIGIN_LABELS = {
    None: "背景工作完成通知",
    "task_notification": "背景工作完成通知",
    "task-notification": "背景工作完成通知",
    "peer": "其他 session／agent 傳訊",
    "sdk": "App 自動送出（sdk）",
}


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def tool_label(block):
    name = block.get("name", "?")
    inp = block.get("input") or {}
    if name in ("Task", "Agent"):
        return f"{name}({inp.get('subagent_type') or 'general-purpose'})"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"mcp:{parts[1][:12]}…{parts[-1]}" if len(parts) > 2 else name
    return name


def classify(entry, tool_names, agents=None):
    """回傳 (類別, 細項)；類別為 model / tool / bg / user / other。

    agents 收集 Agent 工具結果的原文與類型，用來把之後的回報對回是哪種 agent。
    """
    t = entry.get("type")
    msg = entry.get("message") or {}
    content = msg.get("content")
    if t == "assistant":
        for b in content if isinstance(content, list) else []:
            if b.get("type") == "tool_use":
                tool_names[b.get("id")] = tool_label(b)
        return "model", msg.get("model", "")
    if t == "user":
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    label = tool_names.get(b.get("tool_use_id"), "?")
                    if agents is not None and label.startswith(("Agent(", "Task(")):
                        agents.append((json.dumps([b.get("content"), entry.get("toolUseResult")],
                                                  ensure_ascii=False), label))
                    return "tool", label
        # 新版逐字稿用 turnOrigin／origin 標出是誰開啟這個回合
        origin = entry.get("turnOrigin") or (entry.get("origin") or {}).get("kind")
        if origin == "human":
            return "user", ""
        text = user_text(content).lstrip()
        if origin or text.startswith("<task-notification"):
            label = ORIGIN_LABELS.get(origin, f"其他來源（{origin}）")
            ref = (entry.get("origin") or {}).get("from")
            if not ref:
                m = re.search(r"<task-id>([^<]+)</task-id>", text)
                ref = m and m.group(1).strip()
            if ref and agents is not None:
                agent = match_agent(ref, agents)
                label = f"等 {agent} 回報" if agent else f"{label}（未對應）"
            return "bg", label
        if entry.get("isMeta"):
            return "other", "meta"
        return "user", ""
    return "other", t or "?"


def match_agent(ref, agents):
    """在先前 Agent 工具結果裡找出提到這個 id 的那一次，回傳它的類型。"""
    keys = {ref, ref[1:]} if len(ref) > 8 else {ref}
    for text, label in reversed(agents):
        if any(k in text for k in keys):
            return label
    return None


def user_text(content):
    if isinstance(content, str):
        return content
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "text":
            return b.get("text", "")
    return ""


def msg_id(entry):
    """assistant 訊息的 id；同一則回覆串流成多行時 id 相同。其他事件回傳 None。"""
    if entry.get("type") != "assistant":
        return None
    return (entry.get("message") or {}).get("id")


def shape(entry):
    """事件的形狀，只含類型與 XML 標籤名稱，不含內容。"""
    t = entry.get("type", "?")
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        kind = content[0].get("type", "?")
    else:
        kind = "text"
    text = user_text(content).lstrip() if t == "user" else ""
    if text.startswith("<"):
        kind = text[:text.find(">") + 1][:30] if ">" in text[:40] else "<…"
    sub = entry.get("subtype") or (entry.get("attachment") or {}).get("type")
    if isinstance(sub, str):
        kind = sub[:30]
    return f"{t}:{kind}"


def load_entries(path, keep_sidechain=False):
    entries = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            # 舊版把 subagent 內部步驟寫在主檔（isSidechain），時間已算在 Task 工具裡；
            # subagent 自己的檔案則整份都是 sidechain
            if (e.get("isSidechain") and not keep_sidechain) or not e.get("timestamp"):
                continue
            entries.append(e)
    entries.sort(key=lambda e: e["timestamp"])
    return entries


# subagent 內部的階段。Bash 依指令內容分類（只用來分類，不輸出指令）
READ_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch", "WebSearch"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
TEST_RE = re.compile(r"\b(test|tests|vitest|jest|pytest|playwright|mocha|tsc|typecheck|"
                     r"lint|eslint|prettier|build|check|biome)\b")
INSTALL_RE = re.compile(r"\b(npm|pnpm|yarn|bun|pip|uv)\s+(install|ci|i|add|sync)\b")
READ_VERBS = {"cat", "sed", "head", "tail", "grep", "rg", "find", "ls", "wc", "tree", "awk", "jq", "diff"}
SCRIPT_VERBS = {"node", "python", "python3", "perl", "bash", "sh", "time", "nice", "npx", "tsx", "deno"}


def phase_of(block):
    name = block.get("name", "")
    if name in READ_TOOLS:
        return "讀取"
    if name in EDIT_TOOLS:
        return "改檔"
    if name != "Bash":
        return "其他工具"
    cmd = (block.get("input") or {}).get("command", "")
    segments = [re.sub(r"^(rtk\s+)+", "", s.strip()) for s in re.split(r"&&|\|\||;|\|", cmd)]
    segments = [s for s in segments if s and not s.startswith(("cd ", "export "))]
    if re.search(r"\bsleep\b", cmd):
        return "等待／輪詢（sleep 迴圈）"
    if any(TEST_RE.search(s) for s in segments):
        return "測試／建置／檢查"
    if any(INSTALL_RE.search(s) for s in segments):
        return "安裝依賴"
    # 依第一個指令判斷；`grep`、`tail` 接在管線後面只是過濾輸出，不算讀取
    first = re.sub(r"^(\w+=\S*\s+)+", "", segments[0]) if segments else ""
    verb = Path(first.split()[0]).name if first else ""
    if verb == "git":
        return "git"
    if verb in READ_VERBS:
        return "讀取"
    if verb in SCRIPT_VERBS or verb.startswith("python"):
        return "執行腳本（node／python…）"
    return "其他指令"


def analyze_agent(path, idle_cap):
    """subagent 逐字稿：模型時間依下一個區塊拆成思考／寫程式碼／其他生成，工具時間依階段分。"""
    entries = load_entries(path, keep_sidechain=True)
    phases = defaultdict(float)
    pending = {}
    prev = None
    for e in entries:
        t = e.get("type")
        content = (e.get("message") or {}).get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if t == "assistant":
            b = blocks[0] if blocks else {}
            if b.get("type") == "thinking":
                cat = "思考"
            elif b.get("type") == "tool_use" and b.get("name") in EDIT_TOOLS:
                cat = "寫程式碼（產生 Edit／Write 內容）"
            else:
                cat = "其他生成（下指令、寫回報）"
            for blk in blocks:
                if blk.get("type") == "tool_use":
                    pending[blk.get("id")] = phase_of(blk)
        elif t == "user" and any(b.get("type") == "tool_result" for b in blocks):
            cat = next((pending.get(b.get("tool_use_id")) for b in blocks
                        if b.get("type") == "tool_result"), None) or "其他工具"
        elif t == "user":
            # 派工 prompt 或之後 SendMessage 追加的指示：從這裡重新計時，前面的閒置不算
            prev = parse_ts(e["timestamp"])
            continue
        else:
            continue
        ts = parse_ts(e["timestamp"])
        if prev is not None:
            gap = max((ts - prev).total_seconds(), 0)
            if gap <= idle_cap:
                phases[cat] += gap
        prev = ts
    return phases, (parse_ts(entries[0]["timestamp"]) if entries else None)


def agent_files(path, agents):
    """主 session 的 subagent 逐字稿，回傳 [(類型, 路徑)]。"""
    found = []
    for f in sorted((path.parent / path.stem / "subagents").glob("agent-*.jsonl")):
        aid = f.stem[len("agent-"):]
        label = None
        meta = f.with_suffix(".meta.json")
        if meta.exists():
            try:
                m = json.loads(meta.read_text())
                t = m.get("agentType") or m.get("subagent_type")
                label = t and f"Agent({t})"
            except (json.JSONDecodeError, OSError, AttributeError):
                pass
        found.append((label or match_agent(aid, agents) or "未對應", f))
    return found


def analyze(path, idle_cap):
    entries = load_entries(path)
    if len(entries) < 2:
        return None

    tool_names = {}
    agents = []
    buckets = defaultdict(float)
    tools = defaultdict(float)
    tool_calls = defaultdict(int)
    steps = []
    def add(cat, detail, gap, ts, transition):
        if gap > idle_cap:
            # 超過門檻的空檔多半是離開、睡眠或隔天 resume，不算進任何一類
            buckets["idle"] += gap
            return
        buckets[cat] += gap
        if cat in ("tool", "bg"):
            tools[detail] += gap
            tool_calls[detail] += 1
        steps.append((gap, cat, detail, ts, transition))

    prev = parse_ts(entries[0]["timestamp"])
    prev_shape = shape(entries[0])
    prev_mid = msg_id(entries[0])
    skipped = skipped_ts = None
    classify(entries[0], tool_names, agents)
    for e in entries[1:]:
        cat, detail = classify(e, tool_names, agents)
        # 檔案快照、hook、subagent 進度等事件不是狀態轉換，空檔留給下一個有意義的事件
        if cat == "other":
            skipped, skipped_ts = shape(e), parse_ts(e["timestamp"])
            continue
        ts = parse_ts(e["timestamp"])
        transition = f"{prev_shape} → {shape(e)}"
        mid = msg_id(e)
        # 兩則不同的 assistant 訊息之間沒有使用者輸入或工具結果，代表是被喚醒的新回合。
        # 以最後一個附屬事件（喚醒來源或 API 請求）為界：之前算等待，之後算模型生成
        if cat == "model" and prev_mid and mid and mid != prev_mid:
            split = min(max(skipped_ts or ts, prev), ts)
            add("bg", f"喚醒（來源未記錄，前一事件 {skipped or '無'}）",
                (split - prev).total_seconds(), split, f"{prev_shape} → {skipped}")
            prev = split
        if cat == "tool" and detail in WAITS_ON_USER:
            cat = "user"
        add(cat, detail, max((ts - prev).total_seconds(), 0), ts, transition)
        prev, prev_shape, prev_mid = ts, shape(e), mid
        skipped = skipped_ts = None

    first, last = parse_ts(entries[0]["timestamp"]), parse_ts(entries[-1]["timestamp"])
    cwd = next((e.get("cwd") for e in entries if e.get("cwd")), "") or path.parent.name
    return {
        "id": path.stem,
        "project": Path(cwd).name,
        "start": first,
        "wall": (last - first).total_seconds(),
        "buckets": buckets,
        "tools": tools,
        "tool_calls": tool_calls,
        "entries": entries,
        "api_errors": sum(1 for e in entries if e.get("isApiErrorMessage")),
        "steps": steps,
        "prompts": sum(1 for e in entries if classify(e, {})[0] == "user"),
        "agent_files": agent_files(path, agents),
    }


def pct(part, whole):
    return f"{part / whole * 100:4.0f}%" if whole else "   -"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7, help="只看最近 N 天（預設 7）")
    ap.add_argument("--project", help="專案資料夾名稱包含這段文字")
    ap.add_argument("--session", help="session id 前綴，列出該 session 明細")
    ap.add_argument("--idle-cap", type=float, default=30,
                    help="單一空檔超過幾分鐘視為離開（預設 30）")
    ap.add_argument("--top", type=int, default=10, help="工具排行列幾名")
    ap.add_argument("--root", type=Path, default=ROOT, help="逐字稿根目錄")
    ap.add_argument("--trace", type=int, default=0,
                    help="搭配 --session：列出最慢 N 步之間所有事件的類型與欄位名稱（不含內容）")
    ap.add_argument("--agents", action="store_true",
                    help="改看 subagent 內部：各類 agent 的時間花在思考、讀取、改檔、測試…")
    ap.add_argument("--html", nargs="?", const=DEFAULT_REPORT, type=Path,
                    help=f"輸出視覺化報表（預設 {DEFAULT_REPORT}）")
    ap.add_argument("--open", action="store_true", help="搭配 --html：寫完用瀏覽器打開")
    args = ap.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    files = [p for p in args.root.glob("*/*.jsonl")
             # 資料夾名稱是把路徑裡的非英數字元換成 -，比對前用同樣規則轉換
             if (not args.project or re.sub(r"[^A-Za-z0-9]", "-", args.project) in p.parent.name)
             and (not args.session or p.stem.startswith(args.session))
             and (args.session or datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) >= since)]
    results = [r for r in (analyze(p, args.idle_cap * 60) for p in files) if r]
    if not results:
        sys.exit("找不到符合條件的 session")
    results.sort(key=lambda r: r["start"])

    if args.html:
        out = write_html(results, args, args.idle_cap * 60)
        print(f"報表已寫入 {out}")
        if args.open:
            subprocess.run(["open", str(out)], check=False)
        return

    if args.agents:
        print_agents(results, args.idle_cap * 60)
        return

    if args.session:
        for r in results:
            print_detail(r, args.top)
            print_trace(r, args.trace)
        return

    print(f"{'開始時間':<12} {'專案':<22} {'session':<9} {'總時長':>7} {'活躍':>7} "
          f"{'模型':>5} {'工具':>5} {'背景':>5} {'等你':>5} {'離開':>7}  最耗時項目")
    total = defaultdict(float)
    all_tools = defaultdict(float)
    all_calls = defaultdict(int)
    api_errors = 0
    for r in results:
        b = r["buckets"]
        active = b["model"] + b["tool"] + b["bg"] + b["other"]
        busy = active + b["user"]
        top = max(r["tools"].items(), key=lambda kv: kv[1], default=("", 0))
        print(f"{r['start'].astimezone():%m-%d %H:%M}  {r['project'][:22]:<22} {r['id'][:8]:<9} "
              f"{fmt(r['wall']):>7} {fmt(active):>7} {pct(b['model'], busy)} {pct(b['tool'], busy)} "
              f"{pct(b['bg'], busy)} {pct(b['user'], busy)} {fmt(b['idle']):>7}  "
              f"{top[0]} {fmt(top[1]) if top[0] else ''}")
        for k, v in b.items():
            total[k] += v
        for k, v in r["tools"].items():
            all_tools[k] += v
            all_calls[k] += r["tool_calls"][k]
        api_errors += r["api_errors"]

    busy = sum(total[k] for k in ("model", "tool", "bg", "user", "other"))
    print(f"\n合計 {len(results)} 個 session（扣掉離開）{fmt(busy)}：" + "、".join(
        f"{label} {fmt(total[k])}（{pct(total[k], busy).strip()}）"
        for k, label in [("model", "模型"), ("tool", "工具"), ("bg", "背景／其他來源"), ("user", "等你")]))
    if api_errors:
        print(f"API 錯誤回覆（429 等）共 {api_errors} 次")
    print_tools(all_tools, all_calls, total["tool"] + total["bg"], args.top)


# 報表裡 agent 內部階段合併成這幾組，順序即配色順序
PHASE_GROUPS = ["思考", "寫程式碼", "其他生成", "測試／建置", "輪詢等待", "讀取", "其他"]


def phase_group(phase):
    for key, group in [("思考", "思考"), ("寫程式碼", "寫程式碼"), ("其他生成", "其他生成"),
                       ("測試", "測試／建置"), ("輪詢", "輪詢等待"), ("讀取", "讀取")]:
        if phase.startswith(key):
            return group
    return "其他"


def build_report(results, args, idle_cap):
    """整理成報表用的資料；只含時間、名稱與 id，不含對話內容。"""
    keys = ("model", "tool", "bg", "user")
    day = lambda ts: f"{ts.astimezone():%Y-%m-%d}"
    totals = defaultdict(float)
    daily = defaultdict(lambda: defaultdict(float))
    items = defaultdict(lambda: {"n": 0, "sec": 0.0, "kind": "tool"})
    sessions = []
    for r in results:
        b = r["buckets"]
        for k in (*keys, "idle"):
            totals[k] += b[k]
        for k in keys:
            daily[day(r["start"])][k] += b[k]
        for name, sec in r["tools"].items():
            it = items[name]
            it["n"] += r["tool_calls"][name]
            it["sec"] += sec
            # 背景來源都是 classify 回傳 bg 時產生的標籤
            if name.startswith(("等 ", "背景", "喚醒", "其他 session", "App ", "其他來源")):
                it["kind"] = "bg"
        top = max(r["tools"].items(), key=lambda kv: kv[1], default=("", 0))
        sessions.append({"start": f"{r['start'].astimezone():%Y-%m-%d %H:%M}", "project": r["project"],
                         "id": r["id"][:8], "wall": r["wall"], "idle": b["idle"],
                         **{k: b[k] for k in keys}, "top": top[0], "errors": r["api_errors"]})

    agents = defaultdict(lambda: {"runs": 0, "phases": defaultdict(float)})
    agent_daily = defaultdict(lambda: {"runs": 0, "total": 0.0, "think": 0.0})
    for r in results:
        for label, f in r["agent_files"]:
            phases, start = analyze_agent(f, idle_cap)
            total = sum(phases.values())
            if not total:
                continue
            a = agents[label]
            a["runs"] += 1
            for k, v in phases.items():
                a["phases"][phase_group(k)] += v
            d = agent_daily[(day(start or r["start"]), label)]
            d["runs"] += 1
            d["total"] += total
            d["think"] += phases.get("思考", 0)

    return {
        "generated": f"{datetime.now():%Y-%m-%d %H:%M}",
        "days": args.days, "project": args.project or "全部專案",
        "sessionCount": len(results),
        "apiErrors": sum(r["api_errors"] for r in results),
        "totals": dict(totals),
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
        "items": sorted(({"name": k, **v} for k, v in items.items()), key=lambda x: -x["sec"])[:15],
        "phaseGroups": PHASE_GROUPS,
        "agents": sorted(({"type": k, "runs": v["runs"], "phases": dict(v["phases"])}
                          for k, v in agents.items()), key=lambda x: -sum(x["phases"].values())),
        "agentDaily": [{"date": d, "type": t, **v} for (d, t), v in sorted(agent_daily.items())],
        "sessions": sorted(sessions, key=lambda s: s["start"], reverse=True),
    }


def write_html(results, args, idle_cap):
    data = json.dumps(build_report(results, args, idle_cap), ensure_ascii=False)
    # 防止資料裡的 </script> 提前結束 script 區塊
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data.replace("</", "<\\/"))
    out = args.html.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def print_agents(results, idle_cap):
    by_type = defaultdict(lambda: defaultdict(float))
    runs = defaultdict(int)
    for r in results:
        for label, f in r["agent_files"]:
            for k, v in analyze_agent(f, idle_cap)[0].items():
                by_type[label][k] += v
            runs[label] += 1
    if not by_type:
        sys.exit("這些 session 沒有 subagent 逐字稿")
    for label, phases in sorted(by_type.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(phases.values())
        print(f"\n{label}：{runs[label]} 次，合計 {fmt(total)}，平均每次 {fmt(total / runs[label])}")
        for k, v in sorted(phases.items(), key=lambda kv: -kv[1]):
            bar = "█" * round(v / total * 30) if total else ""
            print(f"  {k:<24} {fmt(v):>8} {pct(v, total)}  {bar}")


def print_tools(tools, calls, tool_total, top):
    print(f"\n{'工具與背景來源':<40} {'次數':>5} {'總時間':>8} {'平均':>7} {'占比':>6}")
    for name, sec in sorted(tools.items(), key=lambda kv: -kv[1])[:top]:
        n = calls[name]
        print(f"{name[:40]:<40} {n:>5} {fmt(sec):>8} {fmt(sec / n):>7} {pct(sec, tool_total):>6}")


def print_detail(r, top):
    b = r["buckets"]
    print(f"session {r['id']}  專案 {r['project']}  開始 {r['start'].astimezone():%Y-%m-%d %H:%M}")
    print(f"總時長 {fmt(r['wall'])}，使用者輸入 {r['prompts']} 次，API 錯誤 {r['api_errors']} 次")
    for k, label in [("model", "模型生成"), ("tool", "工具執行"), ("bg", "等背景／其他來源"),
                     ("user", "等你回覆"), ("idle", "離開（超過門檻的空檔）")]:
        print(f"  {label:<12} {fmt(b[k]):>8}  {pct(b[k], r['wall'])}")
    print_tools(r["tools"], r["tool_calls"], b["tool"] + b["bg"], top)
    print(f"\n最慢的 {top} 步：")
    for gap, cat, detail, ts, transition in sorted(r["steps"], key=lambda s: -s[0])[:top]:
        print(f"  {ts.astimezone():%H:%M:%S}  {fmt(gap):>7}  {cat:<6} {detail[:30]:<30}  {transition}")


def print_trace(r, n):
    for gap, cat, detail, ts, _ in sorted(r["steps"], key=lambda s: -s[0])[:n]:
        start = ts - timedelta(seconds=gap)
        print(f"\n── {fmt(gap)} {cat} {detail[:40]}（{start.astimezone():%H:%M:%S} → {ts.astimezone():%H:%M:%S}）")
        for e in r["entries"]:
            t = parse_ts(e["timestamp"])
            if start - timedelta(seconds=5) <= t <= ts + timedelta(seconds=5):
                vals = " ".join(f"{k}={json.dumps(e[k], ensure_ascii=False)[:60]}"
                                for k in TRACE_VALUE_KEYS if k in e)
                print(f"   {(t - start).total_seconds():+8.1f}s  {shape(e):<32} {vals}")


# 這些欄位是列舉或來源標記，印出值也不會洩漏對話內容
TRACE_VALUE_KEYS = ["isMeta", "origin", "promptSource", "turnOrigin", "turnPosition",
                    "operation", "reason", "querySource", "preventedContinuation",
                    "apiErrorStatus", "isApiErrorMessage"]


if __name__ == "__main__":
    main()
