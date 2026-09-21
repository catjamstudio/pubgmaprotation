from __future__ import annotations
import asyncio, base64, os, re, time
from pathlib import Path
from datetime import datetime, timezone
import httpx, yaml
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

ROOT = Path(__file__).parent
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config")); CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "settings.yaml"
DEFAULTS = {"report_url":"https://pubg.com/en/news/11019", "github_username":"catjamstudio", "github_repo":"pubgmaprotation", "github_branch":"docker-app", "github_token":"", "discord_webhooks":[], "rollover_timestamp":1788915600, "schedule_weekday":2, "schedule_time":"01:00", "automatic_updates":True}
app = FastAPI(title="PUBG Map Rotation")
last_schedule_key = ""

async def scheduler():
    global last_schedule_key
    while True:
        cfg = settings(); now = datetime.now(timezone.utc)
        try: target_day, target_time = int(cfg.get("schedule_weekday", 2)), str(cfg.get("schedule_time", "01:00"))
        except (TypeError, ValueError): target_day, target_time = 2, "01:00"
        key = now.strftime("%Y-%m-%d") + target_time
        if cfg.get("automatic_updates") and now.weekday() == target_day and now.strftime("%H:%M") == target_time and key != last_schedule_key:
            try:
                parsed = parse(await fetch(cfg["report_url"])); await publish(parsed); last_schedule_key = key
            except Exception: pass
        await asyncio.sleep(30)

@app.on_event("startup")
async def start_scheduler(): app.state.scheduler = asyncio.create_task(scheduler())

@app.on_event("shutdown")
async def stop_scheduler():
    app.state.scheduler.cancel()

class TextIn(BaseModel): text: str
class Settings(BaseModel): report_url:str=""; github_username:str=""; github_repo:str=""; github_branch:str="docker-app"; github_token:str=""; rollover_timestamp:int=1788915600; schedule_weekday:int=2; schedule_time:str="01:00"; automatic_updates:bool=True; discord_webhooks:list[dict]=[]
def settings():
    data = yaml.safe_load(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    data = {**DEFAULTS, **(data or {})}
    if os.getenv("GITHUB_TOKEN"): data["github_token"] = os.getenv("GITHUB_TOKEN")
    if os.getenv("DISCORD_WEBHOOKS"): data["discord_webhooks"] = [x.strip() for x in os.getenv("DISCORD_WEBHOOKS").split(",") if x.strip()]
    if not data.get("discord_webhooks") and data.get("discord_webhook"): data["discord_webhooks"] = [data["discord_webhook"]]
    normalized=[]
    for hook in data.get("discord_webhooks", []):
        h = hook if isinstance(hook, dict) else {"name":"", "username":"", "url":hook, "avatar_url":""}
        if str(h.get("avatar_url", "")).startswith(("https://discord.com/api/webhooks/", "http://discord.com/api/webhooks/")) and not str(h.get("url", "")).startswith(("http://", "https://discord.com/api/webhooks/")):
            h["url"], h["avatar_url"] = h.get("avatar_url", ""), h.get("url", "")
        normalized.append(h)
    data["discord_webhooks"] = normalized
    return data
def save(data): CONFIG_FILE.write_text(yaml.safe_dump(data, sort_keys=False))
def clean(s): return re.sub(r"\s+", " ", s).strip()
def parse(text):
    soup=BeautifulSoup(text,"html.parser"); rows=[]
    for tr in soup.select("tr"):
        vals=[clean(c.get_text(" ",strip=True)) for c in tr.select("th,td")]
        if vals: rows.append(vals)
    plain="\n".join(" | ".join(r) for r in rows) or text
    dates={int(n):d.replace("(Thu)","").replace("(Wed)","").strip() for n,d in re.findall(r"Week\s+(\d+)\s*\|\s*([A-Z][a-z]+\s+\d+(?:\([A-Za-z]+\))?)",plain)}
    result={n:{"week":n,"date":dates.get(n,"Not used this season"),"NA":[],"EU":[],"SEA":[]} for n in sorted(dates)}
    region=None
    stream = []
    for node in soup.find_all(["h4", "tr"]):
        if node.name == "h4":
            title = clean(node.get_text(" ", strip=True)).upper()
            if title in {"NA", "EU", "SEA"}: region = title
        else:
            vals=[clean(c.get_text(" ",strip=True)) for c in node.select("th,td")]
            if vals: stream.append((region, vals))
    for active_region, row in stream or [(region, re.split(r"\s*\|\s*",x)) for x in text.splitlines()]:
        if active_region: region = active_region
        line=" | ".join(row); upper=line.upper()
        if re.search(r"\b(NA|EU|SEA)\b",upper) and not re.search(r"Week",line): region=re.search(r"\b(NA|EU|SEA)\b",upper).group(1); continue
        m=re.match(r"Week\s*(\d+)\s*\|\s*(.*)",line,re.I)
        if m and region:
            maps=[clean(x) for x in m.group(2).split("|") if clean(x)]
            result.setdefault(int(m.group(1)),{"week":int(m.group(1)),"date":"Not used this season","NA":[],"EU":[],"SEA":[]})[region]=maps
    # PUBG's live article consistently orders the normal-match tables as
    # schedule, AS, SEA, KAKAO, NA, SA, EU. Use table boundaries as a
    # fallback when the CMS omits semantic heading tags in its response.
    table_regions = {2: "SEA", 4: "NA", 6: "EU"}
    for table_index, table_region in table_regions.items():
        tables = soup.select("table")
        if table_index >= len(tables): continue
        for tr in tables[table_index].select("tr"):
            vals = [clean(c.get_text(" ", strip=True)) for c in tr.select("th,td")]
            if len(vals) >= 2 and re.match(r"Week\s+\d+", vals[0], re.I):
                week = int(re.search(r"\d+", vals[0]).group())
                result.setdefault(week, {"week": week, "date": "Not used this season", "NA": [], "EU": [], "SEA": []})[table_region] = vals[1:]
    return {"weeks":list(result.values())}
def short_date(value):
    for full, short in {"January":"Jan","February":"Feb","March":"Mar","April":"Apr","May":"May","June":"Jun","July":"Jul","August":"Aug","September":"Sep","October":"Oct","November":"Nov","December":"Dec"}.items(): value = value.replace(full, short)
    return value
def maps(item, region): return ", ".join(item.get(region) or ["Not used this season"])
def line(item, region, prefix): return f"{prefix} ({short_date(item['date'])}) Map Rotation: {region} - {maps(item, region)}"
def combined_line(item, prefix): return f"{prefix} ({short_date(item['date'])}) Map Rotation: EU - {maps(item, 'EU')} | NA - {maps(item, 'NA')}"
async def fetch(url):
    async with httpx.AsyncClient(timeout=30,follow_redirects=True) as c: r=await c.get(url); r.raise_for_status(); return r.text
async def publish(parsed):
    cfg=settings(); weeks=parsed["weeks"]; index=min(max(int((time.time()-int(cfg["rollover_timestamp"]))//604800),0),max(0,len(weeks)-1)); nxt=index+1
    cur=weeks[index]; files={"maparray":combined_line(cur,f"Week {cur['week']}"),"maparray_sea":line(cur,"SEA",f"Week {cur['week']}")}
    if nxt<len(weeks): files.update(nextweek=combined_line(weeks[nxt],f"Next Week {weeks[nxt]['week']}"),nextweek_sea=line(weeks[nxt],"SEA",f"Next Week {weeks[nxt]['week']}"))
    else: files.update(nextweek="Next Week Map Rotation: EU - Not used this season",nextweek_sea="Next Week Map Rotation: SEA - Not used this season")
    if not cfg["github_token"]: raise HTTPException(400,"GitHub token is not configured")
    headers={"Authorization":f"Bearer {cfg['github_token']}","Accept":"application/vnd.github+json","User-Agent":"pubg-map-rotation"}
    async with httpx.AsyncClient(timeout=30) as c:
        for path,content in files.items():
            url=f"https://api.github.com/repos/{cfg['github_username']}/{cfg['github_repo']}/contents/{path}"
            old=await c.get(url,params={"ref":cfg["github_branch"]},headers=headers); payload={"message":f"Update PUBG map rotation: {path}","content":base64.b64encode(content.encode()).decode(),"branch":cfg["github_branch"]}
            if old.is_success: payload["sha"]=old.json()["sha"]
            r=await c.put(url,headers=headers,json=payload); r.raise_for_status()
        for hook in cfg.get("discord_webhooks", []):
            url = hook.get("url", "") if isinstance(hook, dict) else hook
            if url and not url.startswith(("http://", "https://")): raise HTTPException(400, f"Webhook '{hook.get('name') or 'unnamed'}' URL must start with http:// or https://")
            if url:
                payload={"content":"PUBG map rotation updated:\n"+"\n".join(files.values())}
                if isinstance(hook, dict) and hook.get("username"): payload["username"] = hook["username"]
                if isinstance(hook, dict) and hook.get("avatar_url"): payload["avatar_url"] = hook["avatar_url"]
                response=await c.post(url,json=payload)
                if not response.is_success: raise HTTPException(502, f"Discord webhook '{hook.get('name') or 'unnamed'}' returned {response.status_code}: {response.text[:300]}")
    return files
@app.get("/api/settings")
async def get_settings():
    data=settings(); data["github_token_set"]=bool(data.pop("github_token", "")); return data
@app.put("/api/settings")
async def put_settings(payload:Settings):
    old=settings(); data=payload.model_dump(); data["github_token"]=payload.github_token or old.get("github_token",""); save(data); return {"saved":True}
@app.post("/api/parse/url")
async def parse_url(payload:TextIn):
    try:return parse(await fetch(payload.text))
    except Exception as e: raise HTTPException(400,str(e))
@app.post("/api/parse/text")
async def parse_text(payload:TextIn): return parse(payload.text)
@app.post("/api/publish")
async def do_publish(payload:dict): return {"published":True,"files":await publish(payload["parsed"])}
@app.post("/api/discord/test")
async def discord_test():
    cfg=settings()
    if not cfg.get("discord_webhooks"): raise HTTPException(400,"No Discord webhooks are configured")
    async with httpx.AsyncClient() as c:
        for hook in cfg["discord_webhooks"]:
            url = hook.get("url", "") if isinstance(hook, dict) else hook
            if url and not url.startswith(("http://", "https://")): raise HTTPException(400, f"Webhook '{hook.get('name') or 'unnamed'}' URL must start with http:// or https://")
            if url:
                payload={"content":"PUBG Map Rotation webhook test successful."}
                if isinstance(hook, dict) and hook.get("username"): payload["username"] = hook["username"]
                if isinstance(hook, dict) and hook.get("avatar_url"): payload["avatar_url"] = hook["avatar_url"]
                r=await c.post(url,json=payload)
                if not r.is_success: raise HTTPException(502, f"Discord webhook '{hook.get('name') or 'unnamed'}' returned {r.status_code}: {r.text[:300]}")
    return {"sent":True}
@app.get("/",response_class=HTMLResponse)
async def home(): return (ROOT/"index.html").read_text().replace("</head>", '<link rel="stylesheet" href="/theme.css"></head>')
@app.get("/theme.css")
async def theme(): return FileResponse(ROOT/"theme.css", media_type="text/css")
