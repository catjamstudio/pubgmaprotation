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
DEFAULTS = {"report_url":"https://pubg.com/en/news/11019", "github_username":"catjamstudio", "github_repo":"pubgmaprotation", "github_branch":"docker-app", "github_token":"", "discord_webhooks":[], "discord_regions":["combined","SEA"], "rollover_timestamp":1788915600, "schedule_weekday":2, "schedule_time":"01:00", "automatic_updates":True}
app = FastAPI(title="PUBG Map Rotation", version="1.0.0 build 10")
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
class Settings(BaseModel): report_url:str=""; github_username:str=""; github_repo:str=""; github_branch:str="docker-app"; github_token:str=""; rollover_timestamp:int=1788915600; schedule_weekday:int=2; schedule_time:str="01:00"; automatic_updates:bool=True; discord_webhooks:list[dict]=[]; discord_regions:list[str]=["combined","SEA"]
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
    dates={int(n):d.replace("(Thu)","").replace("(Wed)","").strip() for n,d in re.findall(r"Week\s+(\d+)\s*(?:\|\s*)?([A-Z][a-z]+\s+\d+(?:\([A-Za-z]+\))?)",plain)}
    if not dates:
        dates={int(n):d.strip() for n,d in re.findall(r"Week\s+(\d+)\s+([A-Z][a-z]+\s+\d+)(?:\([A-Za-z]+\))?", text)}
    result={n:{"week":n,"date":dates.get(n,"Not used this season"),"NA":[],"EU":[],"SEA":[],"AS":[]} for n in sorted(dates)}
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
            result.setdefault(int(m.group(1)),{"week":int(m.group(1)),"date":"Not used this season","NA":[],"EU":[],"SEA":[],"AS":[]})[region]=maps
    # Also accept copied plain-text reports where each map is on its own line.
    # The report contains several other regions, so only collect the regions
    # used by this app and ignore headings such as AS, KAKAO, SA, and RU.
    plain_regions = {"NA", "EU", "SEA", "AS"}
    ignored_regions = {"KAKAO", "SA", "RU"}
    map_names = {"Erangel", "Taego", "Miramar", "Sanhok", "Paramo", "Vikendi", "Rondo", "Karakin", "Deston"}
    plain_region = None
    plain_week = None
    for raw_line in text.splitlines():
        item = clean(raw_line).replace("\u00a0", " ").strip("*:_-").strip()
        upper = item.upper()
        if upper in plain_regions:
            plain_region = upper
            plain_week = None
            continue
        if upper == "CONSOLE (ALL REGIONS)":
            plain_region = None
            plain_week = None
            continue
        if upper in ignored_regions:
            plain_region = None
            plain_week = None
            continue
        week_match = re.fullmatch(r"Week\s+(\d+)", item, re.I)
        if week_match and plain_region:
            plain_week = int(week_match.group(1))
            result.setdefault(plain_week, {"week": plain_week, "date": "Not used this season", "NA": [], "EU": [], "SEA": [], "AS": []})
            continue
        if plain_region and plain_week:
            matched_map = next((name for name in map_names if item.casefold() == name.casefold()), None)
            if matched_map:
                result[plain_week][plain_region].append(matched_map)
    # Safety fallback for copied text that has unusual line separators.
    if not result:
        normalized = re.sub(r"\r\n?", "\n", text.replace("\u00a0", " "))
        current_region = None
        current_week = None
        for item in (clean(line).strip("*:_-").strip() for line in normalized.split("\n")):
            if item.upper() in plain_regions:
                current_region, current_week = item.upper(), None
            elif item.upper() == "CONSOLE (ALL REGIONS)":
                current_region, current_week = None, None
            elif item.upper() in ignored_regions:
                current_region, current_week = None, None
            elif (match := re.fullmatch(r"Week\s+(\d+)", item, re.I)) and current_region:
                current_week = int(match.group(1)); result.setdefault(current_week, {"week": current_week, "date": "Not used this season", "NA": [], "EU": [], "SEA": [], "AS": []})
            elif current_region and current_week:
                matched_map = next((name for name in map_names if item.casefold() == name.casefold()), None)
                if matched_map: result[current_week][current_region].append(matched_map)
    # PUBG's live article consistently orders the normal-match tables as
    # schedule, AS, SEA, KAKAO, NA, SA, EU. Use table boundaries as a
    # fallback when the CMS omits semantic heading tags in its response.
    table_regions = {1: "AS", 2: "SEA", 4: "NA", 6: "EU"}
    for table_index, table_region in table_regions.items():
        tables = soup.select("table")
        if table_index >= len(tables): continue
        for tr in tables[table_index].select("tr"):
            vals = [clean(c.get_text(" ", strip=True)) for c in tr.select("th,td")]
            if len(vals) >= 2 and re.match(r"Week\s+\d+", vals[0], re.I):
                week = int(re.search(r"\d+", vals[0]).group())
                result.setdefault(week, {"week": week, "date": "Not used this season", "NA": [], "EU": [], "SEA": [], "AS": []})[table_region] = vals[1:]
    return {"weeks":list(result.values())}
def short_date(value):
    for full, short in {"January":"Jan","February":"Feb","March":"Mar","April":"Apr","May":"May","June":"Jun","July":"Jul","August":"Aug","September":"Sep","October":"Oct","November":"Nov","December":"Dec"}.items(): value = value.replace(full, short)
    return value
def maps(item, region): return ", ".join(item.get(region) or ["Not used this season"])
def line(item, region, prefix): return f"{prefix} ({short_date(item['date'])}) Map Rotation: {region} - {maps(item, region)}"
def combined_line(item, prefix): return f"{prefix} ({short_date(item['date'])}) Map Rotation: EU - {maps(item, 'EU')} | NA - {maps(item, 'NA')}"
def region_line(item, region, prefix, label=None): return f"{prefix} ({short_date(item['date'])}) Map Rotation: {label or region} - {maps(item, region)}"
async def fetch(url):
    async with httpx.AsyncClient(timeout=30,follow_redirects=True) as c: r=await c.get(url); r.raise_for_status(); return r.text
async def publish(parsed):
    cfg=settings(); weeks=parsed["weeks"]; index=min(max(int((time.time()-int(cfg["rollover_timestamp"]))//604800),0),max(0,len(weeks)-1)); nxt=index+1
    cur=weeks[index]; files={"maparray":combined_line(cur,f"Week {cur['week']}"),"maparray_sea":line(cur,"SEA",f"Week {cur['week']}")}
    for region, suffix, label in [("NA", "na", "NA"), ("EU", "eu", "EU"), ("AS", "as", "AS")]:
        files[f"maparray_{suffix}"] = region_line(cur, region, f"Week {cur['week']}", label)
    if nxt<len(weeks):
        next_item=weeks[nxt]; files.update(nextweek=combined_line(next_item,f"Next Week {next_item['week']}"),nextweek_sea=line(next_item,"SEA",f"Next Week {next_item['week']}"))
        for region, suffix, label in [("NA", "na", "NA"), ("EU", "eu", "EU"), ("AS", "as", "AS")]:
            files[f"nextweek_{suffix}"] = region_line(next_item, region, f"Next Week {next_item['week']}", label)
    else:
        files.update(nextweek="Next Week Map Rotation: EU - Not used this season",nextweek_sea="Next Week Map Rotation: SEA - Not used this season")
        for suffix, label in [("na", "NA"), ("eu", "EU"), ("as", "AS")]: files[f"nextweek_{suffix}"] = f"Next Week Map Rotation: {label} - Not used this season"
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
                regions = cfg.get("discord_regions", ["combined", "SEA"])
                selected = []
                labels = {"combined": ("maparray", "nextweek"), "SEA": ("maparray_sea", "nextweek_sea"), "NA": ("maparray_na", "nextweek_na"), "EU": ("maparray_eu", "nextweek_eu"), "AS": ("maparray_as", "nextweek_as")}
                for selected_region in regions:
                    if selected_region in labels:
                        selected.extend([files[labels[selected_region][0]], files[labels[selected_region][1]]])
                payload={"content":"PUBG map rotation updated:\n"+"\n".join(selected or files.values())}
                if isinstance(hook, dict) and hook.get("username"): payload["username"] = hook["username"]
                if isinstance(hook, dict) and hook.get("avatar_url"): payload["avatar_url"] = hook["avatar_url"]
                response=await c.post(url,json=payload)
                if not response.is_success: raise HTTPException(502, f"Discord webhook '{hook.get('name') or 'unnamed'}' returned {response.status_code}: {response.text[:300]}")
    return files
@app.get("/api/settings")
async def get_settings():
    data=settings(); data["github_token_set"]=bool(data.get("github_token", "")); return data
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
async def home():
    page = (ROOT/"index.html").read_text()
    page = page.replace("<style>", '<link rel="stylesheet" href="/theme.css"><style>.brand-logo{width:64px;height:64px;object-fit:contain;vertical-align:middle;filter:drop-shadow(0 4px 5px #000)}.secret-field{display:flex;align-items:stretch;gap:6px}.secret-field input{flex:1;min-width:0}.secret-field .eye{margin:0;min-width:44px;padding:8px}.secret-field .eye:focus{outline:2px solid #31c48d;outline-offset:2px}#discordRegions{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:8px}#discordRegions label{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}#discordRegions input{width:auto;margin:0}</style><style>', 1)
    page = page.replace("🪂 PUBG Map Rotation", '<img class="brand-logo" src="https://raw.githubusercontent.com/catjamstudio/pubgmaprotation/55f4513df6eec9261f1719363f70843d15c1ad38/pubghelmetlogo.png" alt="PUBG helmet logo"> PUBG Map Rotation', 1)
    page = page.replace('<input id="token" type="password" autocomplete="new-password">', '<div class="secret-field"><input id="token" type="password" autocomplete="new-password"><button type="button" class="secondary eye" onclick="toggleToken()" aria-label="Show or hide GitHub token">👁</button></div>', 1)
    page = page.replace("if(s.github_token_set){$('token').value='••••••••';$('tokenState').textContent='Token saved';}", "if(s.github_token_set){$('token').value=s.github_token;$('tokenState').textContent='Token saved';}", 1)
    page = page.replace('<h2>Discord webhooks</h2>', '<h2>Discord webhooks</h2><label>Regions sent to Discord</label><div id="discordRegions"><label><input type="checkbox" value="combined" checked> EU + NA combined</label><label><input type="checkbox" value="SEA" checked> SEA</label><label><input type="checkbox" value="NA"> NA</label><label><input type="checkbox" value="EU"> EU</label><label><input type="checkbox" value="AS"> AS</label></div>', 1)
    page = page.replace("discord_webhooks:hooks.map(({saved,...x})=>x)", "discord_webhooks:hooks.map(({saved,...x})=>x),discord_regions:[...document.querySelectorAll('#discordRegions input:checked')].map(e=>e.value)", 1)
    page = page.replace("hooks=s.discord_webhooks||[];draw()", "hooks=s.discord_webhooks||[];document.querySelectorAll('#discordRegions input').forEach(e=>e.checked=(s.discord_regions||['combined','SEA']).includes(e.value));draw()", 1)
    page = page.replace("function show(x){p=x;$('preview').innerHTML=(x.weeks||[]).map(w=>`<div class=\"week\"><b>Week ${w.week} (${w.date})</b>\\nEU: ${(w.EU.length?w.EU:['Not used this season']).join(', ')}\\nNA: ${(w.NA.length?w.NA:['Not used this season']).join(', ')}\\nSEA: ${(w.SEA.length?w.SEA:['Not used this season']).join(', ')}</div>`).join('')}", "function show(x){p=x;$('preview').innerHTML=(x.weeks||[]).map(w=>{const date=w.date&&w.date!=='Not used this season'?` (${w.date})`:'';const eu=(w.EU&&w.EU.length?w.EU:['Not used this season']).join(', ');const na=(w.NA&&w.NA.length?w.NA:['Not used this season']).join(', ');const sea=(w.SEA&&w.SEA.length?w.SEA:['Not used this season']).join(', ');return `<div class=\"week\"><b>Week ${w.week}${date} Map Rotation</b><br>EU - ${eu} | NA - ${na}<br>SEA - ${sea}</div>`}).join('')||'No weeks found.'}", 1)
    page = page.replace("let p=null,hooks=[];", "let p=null,hooks=[];function toggleToken(){const t=$(\"token\");const b=document.querySelector(\".secret-field .eye\");t.type=t.type===\"password\"?\"text\":\"password\";b.textContent=t.type===\"password\"?\"👁\":\"🙈\";} ", 1)
    page = page.replace(";const sea=(w.SEA&&w.SEA.length?w.SEA:['Not used this season']).join(', ');return `<div class=\\\"week\\\"><b>Week ${w.week}${date} Map Rotation</b><br>EU - ${eu} | NA - ${na}<br>SEA - ${sea}</div>`", ";const sea=(w.SEA&&w.SEA.length?w.SEA:['Not used this season']).join(', ');const as=(w.AS&&w.AS.length?w.AS:['Not used this season']).join(', ');return `<div class=\\\"week\\\"><b>Week ${w.week}${date} Map Rotation</b><br>EU - ${eu} | NA - ${na}<br>SEA - ${sea}<br>AS - ${as}</div>`", 1)
    return page
@app.get("/theme.css")
async def theme(): return FileResponse(ROOT/"theme.css", media_type="text/css")
