#!/usr/bin/env python3
import argparse, datetime as dt, json, logging, os, smtplib, socket, ssl, sys, time
import urllib.parse, urllib.request
from email.message import EmailMessage
from pathlib import Path
import mysql.connector

UTC = dt.timezone.utc

def now_iso():
    return dt.datetime.now(UTC).isoformat(timespec="seconds")

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)

def tcp_probe(host, port, timeout=2.0, samples=2):
    vals, errs = [], []
    for i in range(samples):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                vals.append((time.perf_counter() - t0) * 1000)
        except Exception as e:
            errs.append(str(e))
        if i < samples - 1:
            time.sleep(0.1)
    if not vals:
        return {"ok": False, "avg_ms": None, "max_ms": None, "jitter_ms": None,
                "loss_pct": 100.0, "error": errs[-1] if errs else "connection failed"}
    return {"ok": True,
            "avg_ms": round(sum(vals)/len(vals),2),
            "max_ms": round(max(vals),2),
            "jitter_ms": round(max(vals)-min(vals),2),
            "loss_pct": round((samples-len(vals))*100.0/samples,1),
            "error": errs[-1] if errs else None}

def latency_state(r, t):
    if not r.get("ok"): return "CRITICAL"
    avg, jitter, loss = r.get("avg_ms") or 0, r.get("jitter_ms") or 0, r.get("loss_pct") or 0
    if loss >= t["latency_critical_loss_pct"] or avg >= t["latency_critical_ms"]: return "CRITICAL"
    if loss > 0 or jitter >= t["latency_watch_jitter_ms"] or avg >= t["latency_watch_ms"]: return "WATCH"
    if avg >= t["latency_check_ms"]: return "CHECK"
    return "OK"

def mysql_connect(cfg, host, port):
    return mysql.connector.connect(
        host=host, port=int(port), user=cfg["mysql"]["user"],
        password=os.environ.get(cfg["mysql"].get("password_env","CLMON_DB_PASSWORD"), ""),
        connection_timeout=int(cfg["mysql"].get("connect_timeout_seconds",4)),
        autocommit=True, use_pure=True, ssl_disabled=False
    )

def fetch_cluster_view(cfg):
    candidates = []
    if cfg["router"].get("enabled", True):
        candidates.append(("router", cfg["router"]["host"], cfg["router"]["rw_port"]))
    candidates += [(n["name"], n["host"], n.get("mysql_port",3306)) for n in cfg["nodes"]]
    last_error = None
    for name, host, port in candidates:
        try:
            conn = mysql_connect(cfg, host, port)
            cur = conn.cursor(dictionary=True)
            cur.execute("""SELECT MEMBER_ID,MEMBER_HOST,MEMBER_PORT,MEMBER_STATE,MEMBER_ROLE
                           FROM performance_schema.replication_group_members ORDER BY MEMBER_HOST""")
            members = cur.fetchall()
            cur.execute("""SELECT MEMBER_ID,COUNT_CONFLICTS_DETECTED,
                                  COUNT_TRANSACTIONS_REMOTE_IN_APPLIER_QUEUE
                           FROM performance_schema.replication_group_member_stats""")
            stats = cur.fetchall()
            cur.close(); conn.close()
            return {"ok":True,"source":name,"source_host":host,"members":members,"stats":stats,"error":None}
        except Exception as e:
            last_error = f"{name}@{host}:{port}: {e}"
    return {"ok":False,"source":None,"source_host":None,"members":[],"stats":[],"error":last_error}

def app_probe(cfg):
    app = cfg.get("application", {})
    if not app.get("enabled", False):
        return {"enabled":False}
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(app["url"], method="GET",
                                     headers={"User-Agent":"mysql-cluster-monitor-light/1.0"})
        with urllib.request.urlopen(req, timeout=float(app.get("timeout_seconds",4))) as resp:
            code = int(resp.getcode())
            return {"enabled":True,"ok":code in app.get("expected_http_codes",[200,301,302]),
                    "http_code":code,"latency_ms":round((time.perf_counter()-t0)*1000,2),"error":None}
    except Exception as e:
        return {"enabled":True,"ok":False,"http_code":None,"latency_ms":None,"error":str(e)}

def collect_snapshot(cfg, disable_app=False):
    snap = {"timestamp":now_iso(),"cluster":None,"router":{"enabled":False},"application":{"enabled":False},"nodes":[]}
    if cfg["router"].get("enabled", True):
        snap["router"] = {"enabled":True,"tcp":tcp_probe(cfg["router"]["host"],cfg["router"]["rw_port"],
                                                          cfg["thresholds"]["tcp_timeout_seconds"],1)}
    snap["cluster"] = fetch_cluster_view(cfg)
    for n in cfg["nodes"]:
        tcp = tcp_probe(n["host"], n.get("mysql_port",3306),
                        cfg["thresholds"]["tcp_timeout_seconds"],
                        cfg["thresholds"]["latency_samples"])
        snap["nodes"].append({"name":n["name"],"host":n["host"],"tcp":tcp,
                              "latency_state":latency_state(tcp,cfg["thresholds"])})
    if not disable_app:
        snap["application"] = app_probe(cfg)
    return snap

def evaluate(cfg, snap):
    alerts=[]; t=cfg["thresholds"]
    def add(sev,key,msg,details=None):
        alerts.append({"severity":sev,"key":key,"message":msg,"details":details or {}})
    c=snap["cluster"]
    if not c.get("ok"):
        add("CRITICAL","cluster_view_unavailable","Unable to obtain InnoDB Cluster membership view.",{"error":c.get("error")})
    else:
        expected=int(cfg.get("expected_members",3)); members=c["members"]
        online=[m for m in members if m["MEMBER_STATE"]=="ONLINE"]
        primary=[m for m in members if m["MEMBER_STATE"]=="ONLINE" and m["MEMBER_ROLE"]=="PRIMARY"]
        recovering=[m for m in members if m["MEMBER_STATE"]=="RECOVERING"]
        if len(members)<expected: add("CRITICAL","member_missing",f"Only {len(members)}/{expected} members are visible.",{"members":members})
        if len(online)<expected: add("CRITICAL" if len(online)<2 else "WARNING","not_all_online",f"Only {len(online)}/{expected} members are ONLINE.",{"members":members})
        if len(primary)!=1: add("CRITICAL","primary_count",f"Expected exactly 1 ONLINE PRIMARY; found {len(primary)}.",{"primary":primary})
        if recovering: add("WARNING","member_recovering","Member recovering: "+", ".join(m["MEMBER_HOST"] for m in recovering))
        for m in members:
            if m["MEMBER_STATE"] in ("ERROR","UNREACHABLE","OFFLINE"):
                add("CRITICAL",f"member_state_{m['MEMBER_HOST']}",f"{m['MEMBER_HOST']} is {m['MEMBER_STATE']}.",m)
        for st in c.get("stats",[]):
            q=int(st.get("COUNT_TRANSACTIONS_REMOTE_IN_APPLIER_QUEUE") or 0)
            if q>=int(t["applier_queue_critical"]): add("CRITICAL",f"queue_{st['MEMBER_ID']}",f"Replication applier queue is {q}.",st)
            elif q>=int(t["applier_queue_warning"]): add("WARNING",f"queue_{st['MEMBER_ID']}",f"Replication applier queue is {q}.",st)
            conf=int(st.get("COUNT_CONFLICTS_DETECTED") or 0)
            if conf>0: add("WARNING",f"conflicts_{st['MEMBER_ID']}",f"Replication conflicts detected: {conf}.",st)
    if snap["router"].get("enabled") and not snap["router"]["tcp"].get("ok"):
        add("CRITICAL","router_down","MySQL Router read/write endpoint is unreachable.",snap["router"]["tcp"])
    for n in snap["nodes"]:
        s=n["latency_state"]; tcp=n["tcp"]
        if s=="CRITICAL":
            add("CRITICAL",f"latency_{n['name']}",f"{n['name']} network/MySQL port is CRITICAL: avg={tcp.get('avg_ms')} ms, loss={tcp.get('loss_pct')}%.",tcp)
        elif s=="WATCH":
            add("WARNING",f"latency_{n['name']}",f"{n['name']} latency needs watching: avg={tcp.get('avg_ms')} ms, jitter={tcp.get('jitter_ms')} ms, loss={tcp.get('loss_pct')}%.",tcp)
        elif s=="CHECK" and cfg["notifications"].get("email_on_check",False):
            add("INFO",f"latency_{n['name']}",f"{n['name']} latency needs checking: {tcp.get('avg_ms')} ms.",tcp)
    app=snap["application"]
    if app.get("enabled"):
        if not app.get("ok"): add("CRITICAL","application_down","osTicket/application health check failed.",app)
        elif app.get("latency_ms") is not None and app["latency_ms"]>=t["application_latency_warning_ms"]:
            add("WARNING","application_slow",f"osTicket/application response time is {app['latency_ms']} ms.",app)
    return alerts

def summarize(cfg,snap,alerts):
    lines=[f"Cluster: {cfg['cluster_name']}",f"UTC: {snap['timestamp']}"]
    c=snap["cluster"]
    if c.get("ok"):
        p=next((m["MEMBER_HOST"] for m in c["members"] if m["MEMBER_STATE"]=="ONLINE" and m["MEMBER_ROLE"]=="PRIMARY"),"NONE")
        lines.append(f"PRIMARY: {p}"); lines.append("Members:")
        lines += [f"  {m['MEMBER_HOST']}: {m['MEMBER_STATE']} / {m['MEMBER_ROLE']}" for m in c["members"]]
    else:
        lines.append(f"Cluster view: FAILED - {c.get('error')}")
    if snap["router"].get("enabled"):
        lines.append("Router: "+("UP" if snap["router"]["tcp"].get("ok") else "DOWN"))
    lines.append("Latency:")
    for n in snap["nodes"]:
        tcp=n["tcp"]; lines.append(f"  {n['name']}: {n['latency_state']} avg={tcp.get('avg_ms')} ms max={tcp.get('max_ms')} ms jitter={tcp.get('jitter_ms')} ms loss={tcp.get('loss_pct')}%")
    if snap["application"].get("enabled"):
        a=snap["application"]; lines.append(f"Application: {'UP' if a.get('ok') else 'DOWN'} http={a.get('http_code')} latency={a.get('latency_ms')} ms")
    lines.append("Actionable conditions: none" if not alerts else "Actionable conditions:")
    if alerts:
        lines += [f"  [{a['severity']}] {a['message']}" for a in alerts]
    return "\n".join(lines)

def send_email(cfg,subject,body):
    e=cfg["email"]
    if not e.get("enabled"): return
    msg=EmailMessage(); msg["Subject"]=subject; msg["From"]=e["from"]; msg["To"]=", ".join(e.get("to",[]))
    if e.get("cc"): msg["Cc"]=", ".join(e["cc"])
    msg.set_content(body); recipients=e.get("to",[])+e.get("cc",[])
    pwd=os.environ.get(e.get("password_env","CLMON_SMTP_PASSWORD"),"")
    with smtplib.SMTP(e["smtp_host"],int(e.get("smtp_port",587)),timeout=10) as s:
        s.ehlo()
        if e.get("starttls",True): s.starttls(context=ssl.create_default_context()); s.ehlo()
        if e.get("username"): s.login(e["username"],pwd)
        s.send_message(msg,to_addrs=recipients)

def send_telegram(cfg,text):
    tg=cfg["telegram"]
    if not tg.get("enabled"): return
    token=os.environ.get(tg.get("bot_token_env","CLMON_TELEGRAM_BOT_TOKEN"),"")
    chat=os.environ.get(tg.get("chat_id_env","CLMON_TELEGRAM_CHAT_ID"),"")
    if not token or not chat: return
    payload=urllib.parse.urlencode({"chat_id":chat,"text":text[:4000]}).encode()
    req=urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",data=payload,method="POST")
    with urllib.request.urlopen(req,timeout=10) as r: r.read()

def append_jsonl(path,obj):
    with open(path,"a",encoding="utf-8") as f:
        f.write(json.dumps(obj,separators=(",",":"),default=str)+"\n")

def load_state(path):
    try: return load_json(path)
    except Exception: return {"active_alerts":{},"last_primary":None,"last_metrics_write":0}

def save_state(path,state):
    tmp=str(path)+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(state,f,separators=(",",":"))
    os.replace(tmp,path)

def current_primary(snap):
    c=snap["cluster"]
    if not c.get("ok"): return None
    return next((m["MEMBER_HOST"] for m in c["members"] if m["MEMBER_STATE"]=="ONLINE" and m["MEMBER_ROLE"]=="PRIMARY"),None)

def process_alert_transitions(cfg,snap,alerts,state,event_file,disable_email=False,disable_telegram=False):
    now={a["key"]:a for a in alerts if a["severity"] in ("CRITICAL","WARNING")}
    old=state.get("active_alerts",{})
    new_keys=set(now)-set(old); resolved=set(old)-set(now)
    np=current_primary(snap); op=state.get("last_primary")
    if op and np and op!=np:
        key=f"primary_changed_{op}_to_{np}"
        now[key]={"severity":"WARNING","key":key,"message":f"PRIMARY changed from {op} to {np}.","details":{}}
        new_keys.add(key)
    health=summarize(cfg,snap,alerts)
    if new_keys:
        arr=[now[k] for k in new_keys]; worst="CRITICAL" if any(a["severity"]=="CRITICAL" for a in arr) else "WARNING"
        subject=f"[{worst}] {cfg['cluster_name']} action required"
        body="NEW ACTIONABLE EVENT(S)\n\n"+"\n".join(f"[{a['severity']}] {a['message']}" for a in arr)+"\n\nCURRENT HEALTH SNAPSHOT\n"+health
        if not disable_email: send_email(cfg,subject,body)
        if not disable_telegram: send_telegram(cfg,subject+"\n\n"+body)
        append_jsonl(event_file,{"timestamp":snap["timestamp"],"record_type":"alert","events":arr})
    if resolved:
        arr=[old[k] for k in resolved]; subject=f"[RECOVERY] {cfg['cluster_name']} condition cleared"
        body="RESOLVED EVENT(S)\n\n"+"\n".join(f"- {a.get('message',a.get('key'))}" for a in arr)+"\n\nCURRENT HEALTH SNAPSHOT\n"+health
        if not disable_email: send_email(cfg,subject,body)
        if not disable_telegram: send_telegram(cfg,subject+"\n\n"+body)
        append_jsonl(event_file,{"timestamp":snap["timestamp"],"record_type":"recovery","events":arr})
    state["active_alerts"]={k:v for k,v in now.items() if not k.startswith("primary_changed_")}
    if np: state["last_primary"]=np

def validate_config(cfg):
    req=["cluster_name","expected_members","mysql","router","nodes","thresholds","logging","email","telegram","notifications"]
    missing=[k for k in req if k not in cfg]
    if missing: raise ValueError("Missing config keys: "+", ".join(missing))
    if not cfg["nodes"]: raise ValueError("At least one DB node is required.")
    return True

def build_arg_parser():
    p=argparse.ArgumentParser(description="Lightweight MySQL 8.4 InnoDB Cluster monitor")
    p.add_argument("--config",required=True,help="Path to JSON config")
    p.add_argument("--once",action="store_true",help="Run one cycle and exit")
    p.add_argument("--validate-config",action="store_true",help="Validate config and exit")
    p.add_argument("--test-alert",action="store_true",help="Send test alert and exit")
    p.add_argument("--print-json",action="store_true",help="Print current snapshot as JSON")
    p.add_argument("--interval",type=int,help="Override poll interval")
    p.add_argument("--disable-app-check",action="store_true",help="Skip app HTTP check")
    p.add_argument("--disable-email",action="store_true",help="Disable email alerts")
    p.add_argument("--disable-telegram",action="store_true",help="Disable Telegram alerts")
    return p

def main():
    args=build_arg_parser().parse_args()
    cfg=load_json(args.config); validate_config(cfg)
    if args.interval: cfg["interval_seconds"]=args.interval
    log_dir=Path(cfg["logging"]["directory"]); state_file=Path(cfg["state_file"])
    ensure_dir(log_dir); ensure_dir(state_file.parent)
    logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s",
                        handlers=[logging.FileHandler(log_dir/"monitor.log"),logging.StreamHandler(sys.stdout)])
    if args.validate_config:
        print("CONFIG OK"); return
    if args.test_alert:
        msg=f"Test alert from {cfg['monitor_name']} at {now_iso()}"
        if not args.disable_email: send_email(cfg,f"[TEST] {cfg['cluster_name']} monitor",msg)
        if not args.disable_telegram: send_telegram(cfg,msg)
        print(msg); return
    state=load_state(state_file)
    metrics_file=log_dir/cfg["logging"]["metrics_jsonl"]; event_file=log_dir/cfg["logging"]["events_jsonl"]
    interval=int(cfg.get("interval_seconds",120))
    metrics_every=int(cfg["logging"].get("metrics_snapshot_every_seconds",600))
    while True:
        start=time.time()
        try:
            snap=collect_snapshot(cfg,args.disable_app_check); alerts=evaluate(cfg,snap); text=summarize(cfg,snap,alerts)
            print(json.dumps(snap,indent=2,default=str)) if args.print_json else logging.info(text.replace("\n"," | "))
            process_alert_transitions(cfg,snap,alerts,state,event_file,args.disable_email,args.disable_telegram)
            if start-float(state.get("last_metrics_write",0))>=metrics_every:
                append_jsonl(metrics_file,{"record_type":"metrics",**snap}); state["last_metrics_write"]=start
            save_state(state_file,state)
        except Exception as e:
            logging.exception("Monitor cycle failed: %s",e)
            append_jsonl(event_file,{"timestamp":now_iso(),"record_type":"monitor_error","error":str(e)})
        if args.once: break
        time.sleep(max(1,interval-(time.time()-start)))

if __name__=="__main__":
    main()
