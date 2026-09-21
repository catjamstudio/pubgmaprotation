from __future__ import annotations
import base64, os, re, time
from pathlib import Path
from datetime import datetime, timezone
import httpx, yaml
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

ROOT = Path(__file__).parent
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config")); CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "settings.yaml"
DEFAULTS = {"report_url":"https://pubg.com/en/news/11019", "github_username":"catjamstudio", "github_repo":"pubgmaprotation", "github_branch":"docker-app", "github_token":"", "discord_webhook":"", "rollover_timestamp":1788915600, "automatic_updates":True}
app = FastAPI(title="PUBG Map Rotation")

class TextIn(BaseModel): text: str
class Settings(BaseModel): report_url:str=""; github_username:str=""; github_repo:str=""; github_branch:str="docker-app"; github_token:str=""; discord_webhook:str=""; rollover_timestamp:int=1788915600; automatic_updates:bool=True
def settings():
    data = yaml.safe_load(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    return {**DEFAULTS, **(data or {})}
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
    return {"weeks":list(result.values())}
def line(item, region, prefix): return f"{prefix} ({item['date']}) Map Rotation: {region} - {', '.join(item.get(region) or ['Not used this season'])}"
async def fetch(url):
    async with httpx.AsyncClient(timeout=30,follow_redirects=True) as c: r=await c.get(url); r.raise_for_status(); return r.text
async def publish(parsed):
    cfg=settings(); weeks=parsed["weeks"]; index=min(max(int(parsed.get("current_index",0)),0),max(0,len(weeks)-1)); nxt=index+1
    cur=weeks[index]; files={"maparray":line(cur,"EU",f"Week {cur['week']}"),"maparray_sea":line(cur,"SEA",f"Week {cur['week']}")}
    if nxt<len(weeks): files.update(nextweek=line(weeks[nxt],"EU",f"Next Week {weeks[nxt]['week']}"),nextweek_sea=line(weeks[nxt],"SEA",f"Next Week {weeks[nxt]['week']}"))
    else: files.update(nextweek="Next Week Map Rotation: EU - Not used this season",nextweek_sea="Next Week Map Rotation: SEA - Not used this season")
    if not cfg["github_token"]: raise HTTPException(400,"GitHub token is not configured")
    headers={"Authorization":f"Bearer {cfg['github_token']}","Accept":"application/vnd.github+json","User-Agent":"pubg-map-rotation"}
    async with httpx.AsyncClient(timeout=30) as c:
        for path,content in files.items():
            url=f"https://api.github.com/repos/{cfg['github_username']}/{cfg['github_repo']}/contents/{path}"
            old=await c.get(url,params={"ref":cfg["github_branch"]},headers=headers); payload={"message":f"Update PUBG map rotation: {path}","content":base64.b64encode(content.encode()).decode(),"branch":cfg["github_branch"]}
            if old.is_success: payload["sha"]=old.json()["sha"]
            r=await c.put(url,headers=headers,json=payload); r.raise_for_status()
        if cfg["discord_webhook"]: (await c.post(cfg["discord_webhook"],json={"content":"PUBG map rotation updated:\n"+"\n".join(files.values())})).raise_for_status()
    return files
@app.get("/api/settings")
async def get_settings():
    data=settings(); data["github_token_set"]=bool(data.pop("github_token", "")); data["discord_webhook_set"]=bool(data.get("discord_webhook")); data["discord_webhook"]=""; return data
@app.put("/api/settings")
async def put_settings(payload:Settings):
    old=settings(); data=payload.model_dump(); data["github_token"]=data["github_token"] or old.get("github_token",""); save(data); return {"saved":True}
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
    if not cfg["discord_webhook"]: raise HTTPException(400,"Discord webhook is not configured")
    async with httpx.AsyncClient() as c: r=await c.post(cfg["discord_webhook"],json={"content":"PUBG Map Rotation webhook test successful."}); r.raise_for_status()
    return {"sent":True}
@app.get("/",response_class=HTMLResponse)
async def home(): return (ROOT/"index.html").read_text()
