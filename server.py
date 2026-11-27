import os
import json
import time
import threading
import logging
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

server_logger = logging.getLogger("render_server")

class HealthStatus:
    start_time = time.time()
    bot_app = None
    scraper = None
    bot_online = False
    bot_error = None
    scraper_error = None
    startup_stage = "Initializing..."
    build_version = "v3.3-buttons-removed-clean-cards"

class RenderHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        from config import TG_SESSION_STRING, TG_API_ID, BOT_TOKEN

        if self.path in ("/health", "/healthz"):
            uptime = int(time.time() - HealthStatus.start_time)
            scraper = HealthStatus.scraper
            ub_online = bool(scraper and getattr(scraper, "is_online", False))
            bot_online = bool(HealthStatus.bot_online and (not HealthStatus.bot_app or not getattr(HealthStatus.bot_app, "updater", None) or getattr(HealthStatus.bot_app.updater, "running", True)))
            data = {
                "status": "healthy",
                "service": "KOLInsights Alpha Tracker",
                "version": HealthStatus.build_version,
                "startup_stage": HealthStatus.startup_stage,
                "uptime_seconds": uptime,
                "userbot_connected": ub_online,
                "userbot_status": getattr(scraper, "status_reason", "Not Initialized") if scraper else "Not Started",
                "userbot_error": HealthStatus.scraper_error,
                "bot_api_connected": bot_online,
                "bot_api_error": HealthStatus.bot_error,
                "bot_token_configured": bool(BOT_TOKEN),
                "bot_token_preview": f"{BOT_TOKEN[:6]}...{BOT_TOKEN[-4:]}" if (BOT_TOKEN and len(BOT_TOKEN) > 10) else None,
                "session_configured": bool(TG_SESSION_STRING),
                "session_length": len(TG_SESSION_STRING) if TG_SESSION_STRING else 0,
                "timestamp": int(time.time())
            }
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/":
            uptime = int(time.time() - HealthStatus.start_time)
            h, rem = divmod(uptime, 3600)
            m, s = divmod(rem, 60)
            uptime_str = f"{h}h {m}m {s}s"
            
            scraper = HealthStatus.scraper
            ub_online = bool(scraper and getattr(scraper, "is_online", False))

            if ub_online:
                uname = getattr(scraper, "status_reason", "Connected")
                ub_badge = f'<span style="color:#00ff88;font-weight:bold;">● ONLINE (MTProto: {uname})</span>'
            else:
                if not TG_API_ID:
                    reason = "TG_API_ID missing in Render Environment"
                elif not TG_SESSION_STRING:
                    reason = "TG_SESSION_STRING missing in Render Environment"
                elif HealthStatus.scraper_error:
                    reason = HealthStatus.scraper_error
                else:
                    reason = getattr(scraper, "status_reason", "Initializing...") if scraper else "Not Started"
                ub_badge = f'<span style="color:#ffcc00;font-weight:bold;">● STANDBY ({reason})</span>'

            bot_online = bool(HealthStatus.bot_online and (not HealthStatus.bot_app or not getattr(HealthStatus.bot_app, "updater", None) or getattr(HealthStatus.bot_app.updater, "running", True)))
            if bot_online:
                bot_badge = '<span style="color:#00ff88;font-weight:bold;">● ONLINE (@kolinsightsbot)</span>'
            else:
                err_detail = f" ({HealthStatus.bot_error})" if HealthStatus.bot_error else " (Check BOT_TOKEN)"
                bot_badge = f'<span style="color:#ff4444;font-weight:bold;">● OFFLINE{err_detail}</span>'

            html = f"""<!DOCTYPE html>
<html>
<head>
    <title>KOLInsights Alpha Tracker - Live Status</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{
            background: #0d1117;
            color: #c9d1d9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
        }}
        .card {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 14px;
            padding: 32px;
            max-width: 540px;
            width: 100%;
            box-shadow: 0 12px 36px rgba(0,0,0,0.55);
        }}
        h1 {{
            color: #58a6ff;
            margin-top: 0;
            font-size: 24px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .badge {{
            display: inline-block;
            background: #238636;
            color: white;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
        }}
        .row {{
            display: flex;
            justify-content: space-between;
            padding: 12px 0;
            border-bottom: 1px solid #21262d;
            font-size: 14px;
            gap: 10px;
        }}
        .label {{ color: #8b949e; min-width: 130px; }}
        .val {{ font-weight: 600; text-align: right; word-break: break-word; }}
        a {{ color: #58a6ff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .footer {{
            margin-top: 20px;
            font-size: 12px;
            color: #484f58;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🚀 KOLInsights Bot <span class="badge">ACTIVE</span></h1>
        <p style="color:#8b949e;margin-bottom:20px;">High-Frequency Crypto KOL & Alpha Channel Tracker</p>
        <div class="row"><span class="label">System Status</span><span class="val" style="color:#00ff88;">🟢 Running Healthy</span></div>
        <div class="row"><span class="label">Engine Stage</span><span class="val" style="color:#58a6ff;">{HealthStatus.startup_stage}</span></div>
        <div class="row"><span class="label">Telegram Bot API</span><span class="val">{bot_badge}</span></div>
        <div class="row"><span class="label">Telethon Userbot</span><span class="val">{ub_badge}</span></div>
        <div class="row"><span class="label">System Uptime</span><span class="val">{uptime_str}</span></div>
        <div class="row"><span class="label">Host Platform</span><span class="val">Render Cloud (Web Service)</span></div>
        <div class="row"><span class="label">Health Check</span><span class="val"><a href="/health">/health (JSON)</a></span></div>
        <div class="footer">KOLInsights &bull; 0ms Latency Engine &bull; Auto-Pacing Rate Limiter</div>
    </div>
</body>
</html>"""
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Keep stdout clean from high-frequency health checks
        pass

def start_render_web_server(port: int = None, bot_app=None, scraper=None):
    """Starts embedded HTTP Web Service on 0.0.0.0:$PORT for Render Health Checks."""
    if port is None:
        port = int(os.getenv("PORT", 8080))
    HealthStatus.bot_app = bot_app
    HealthStatus.scraper = scraper

    server = ThreadingHTTPServer(("0.0.0.0", port), RenderHealthHandler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    server_logger.info(f"🌐 [RENDER WEB SERVICE] HTTP Web Service & Health Check listening on 0.0.0.0:{port}")
    return server
