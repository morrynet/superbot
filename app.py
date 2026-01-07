import os
from flask import Flask, jsonify
import threading
import time
import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
PORT = int(os.getenv("PORT", "10000"))

# Import and run the bot in a separate thread
def run_bot():
    """Run the Telegram bot"""
    try:
        from bot import main as bot_main
        bot_main()
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")

# Start bot in background thread
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# Keep-alive ping to prevent spin-down
def keep_alive():
    """Ping our own service to keep it alive"""
    while True:
        try:
            # Ping our own health endpoint
            if PORT:
                requests.get(f"http://localhost:{PORT}/health", timeout=5)
            time.sleep(300)  # Every 5 minutes
        except:
            time.sleep(60)

# Start keep-alive in background
keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
keep_alive_thread.start()

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "Viral Music Bot",
        "message": "Bot is running in background",
        "endpoints": {
            "/": "This page",
            "/health": "Health check",
            "/keepalive": "Prevent spin-down"
        }
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "bot": "running" if bot_thread.is_alive() else "stopped",
        "timestamp": time.time()
    })

@app.route('/keepalive')
def keepalive():
    return jsonify({
        "status": "awake",
        "message": "Instance kept alive"
    })

if __name__ == "__main__":
    logger.info(f"Starting web service on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
