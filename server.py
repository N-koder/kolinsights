import os
import json
import time
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

server_logger = logging.getLogger("render_server")

class HealthStatus:
    start_time = time.time()
    bot_app = None
    scraper = None

class RenderHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            uptime = int(time.time() - HealthStatus.start_time)
            scraper = HealthStatus.scraper
            ub_online = bool(
                scraper 
                and getattr(scraper, "is_authorized", False)
                and scraper.client 
                and scraper.client.is_connected()
            )
            bot_online = bool(
                HealthStatus.bot_app 
                and getattr(HealthStatus.bot_app, "bot", None)
            )
            data = {
                "status": "healthy",
                "service": "KOLInsights Alpha Tracker",
                "uptime_seconds": uptime,
                "userbot_connected": ub_online,
                "userbot_status": getattr(scraper, "status_reason", "Not Initialized") if scraper else "Not Started",
                "bot_api_connected": bot_online,
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
            ub_online = bool(
                scraper 
                and getattr(scraper, "is_authorized", False)
                and scraper.client 
                and scraper.client.is_connected()
            )

            from config import TG_SESSION_STRING, TG_API_ID
            if ub_online:
                uname = getattr(scraper, "status_reason", "Connected")
                ub_badge = f'<span style="color:#00ff88;font-weight:bold;">● ONLINE (MTProto: {uname})</span>'
            else:
                if not TG_API_ID:
                    reason = "TG_API_ID missing in Render Environment"
                elif not TG_SESSION_STRING:
                    reason = "TG_SESSION_STRING missing in Render Environment"
                else:
                    reason = getattr(scraper, "status_reason", "Bot API Fallback")
                ub_badge = f'<span style="color:#ffcc00;font-weight:bold;">● STANDBY ({reason})</span>'

            bot_online = bool(
                HealthStatus.bot_app 
                and getattr(HealthStatus.bot_app, "bot", None)
            )
            bot_badge = '<span style="color:#00ff88;font-weight:bold;">● ONLINE (@kolinsightsbot)</span>' if bot_online else '<span style="color:#ff4444;font-weight:bold;">● OFFLINE (Check BOT_TOKEN)</span>'

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

    server = HTTPServer(("0.0.0.0", port), RenderHealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    server_logger.info(f"🌐 [RENDER WEB SERVICE] HTTP Web Service & Health Check listening on 0.0.0.0:{port}")
    return server
