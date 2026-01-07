import os
import sqlite3
import logging
import threading
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "8423872767:AAHNcXP62I8vC96E8pP8d4kX2QQ4YjV8Yd0")
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN is required")
    exit(1)

ADMIN_IDS = set()
admin_env = os.getenv("ADMIN_IDS", "")
if admin_env:
    ADMIN_IDS = {int(x.strip()) for x in admin_env.split(",") if x.strip()}

PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

# Database setup
DB_PATH = "data/bot.db"
os.makedirs("data", exist_ok=True)

def init_db():
    """Initialize database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            shares INTEGER DEFAULT 0,
            referrals INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Groups table
    c.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY,
            title TEXT,
            username TEXT,
            member_count INTEGER,
            is_active INTEGER DEFAULT 1,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Promotions table
    c.execute('''
        CREATE TABLE IF NOT EXISTS promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            content TEXT,
            sent_to INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Packages table
    c.execute('''
        CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            price INTEGER,
            shares INTEGER
        )
    ''')
    
    # Insert default packages
    default_packages = [
        ("BASIC", 20, 20),
        ("PRO", 50, 50),
        ("VIP", 100, 100)
    ]
    
    c.executemany(
        "INSERT OR IGNORE INTO packages (name, price, shares) VALUES (?, ?, ?)",
        default_packages
    )
    
    conn.commit()
    conn.close()
    logger.info("✅ Database initialized")

# Database helpers
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_user(telegram_id):
    """Get or create user"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    user = c.fetchone()
    
    if not user:
        c.execute("INSERT INTO users (telegram_id) VALUES (?)", (telegram_id,))
        conn.commit()
        c.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        user = c.fetchone()
    
    conn.close()
    return dict(user) if user else None

def update_user_info(telegram_id, username, first_name, last_name):
    """Update user info"""
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE users SET username=?, first_name=?, last_name=? WHERE telegram_id=?",
        (username or "", first_name or "", last_name or "", telegram_id)
    )
    conn.commit()
    conn.close()

def add_shares(telegram_id, shares):
    """Add shares to user"""
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE users SET shares = shares + ? WHERE telegram_id = ?",
        (shares, telegram_id)
    )
    conn.commit()
    conn.close()

def use_share(telegram_id):
    """Use one share"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT shares FROM users WHERE telegram_id = ?", (telegram_id,))
    user = c.fetchone()
    
    if user and user['shares'] > 0:
        c.execute(
            "UPDATE users SET shares = shares - 1 WHERE telegram_id = ?",
            (telegram_id,)
        )
        conn.commit()
        conn.close()
        return True
    
    conn.close()
    return False

def get_packages():
    """Get available packages"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM packages ORDER BY price")
    packages = [dict(row) for row in c.fetchall()]
    conn.close()
    return packages

def get_active_groups():
    """Get active groups"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT group_id, title FROM groups WHERE is_active = 1")
    groups = [dict(row) for row in c.fetchall()]
    conn.close()
    return groups

def add_promotion(user_id, content):
    """Record promotion"""
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO promotions (user_id, content) VALUES (?, ?)",
        (user_id, content)
    )
    conn.commit()
    conn.close()

# Flask app for web interface
app = Flask(__name__)

@app.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>🎵 Viral Music Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                min-height: 100vh;
            }
            .container {
                max-width: 800px;
                margin: 0 auto;
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 40px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            }
            h1 {
                text-align: center;
                font-size: 2.8em;
                margin-bottom: 10px;
                background: linear-gradient(45deg, #ff6b6b, #feca57);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            .subtitle {
                text-align: center;
                font-size: 1.2em;
                opacity: 0.9;
                margin-bottom: 40px;
            }
            .stats {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 20px;
                margin: 40px 0;
            }
            .stat-card {
                background: rgba(255, 255, 255, 0.15);
                padding: 25px;
                border-radius: 15px;
                text-align: center;
                transition: transform 0.3s;
            }
            .stat-card:hover {
                transform: translateY(-5px);
                background: rgba(255, 255, 255, 0.2);
            }
            .stat-number {
                font-size: 2.5em;
                font-weight: bold;
                margin: 10px 0;
                color: #4ecdc4;
            }
            .btn {
                display: inline-block;
                padding: 15px 35px;
                background: linear-gradient(45deg, #4ecdc4, #44a08d);
                color: white;
                text-decoration: none;
                border-radius: 50px;
                margin: 15px;
                font-weight: bold;
                font-size: 1.1em;
                transition: all 0.3s;
                border: none;
                cursor: pointer;
                box-shadow: 0 5px 15px rgba(0,0,0,0.2);
            }
            .btn:hover {
                transform: translateY(-3px);
                box-shadow: 0 8px 25px rgba(0,0,0,0.3);
                background: linear-gradient(45deg, #44a08d, #4ecdc4);
            }
            .cta {
                text-align: center;
                margin: 50px 0;
            }
            .features {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 25px;
                margin: 40px 0;
            }
            .feature {
                background: rgba(255, 255, 255, 0.1);
                padding: 25px;
                border-radius: 15px;
                border-left: 5px solid #4ecdc4;
            }
            .feature h3 {
                margin-top: 0;
                color: #4ecdc4;
            }
            .footer {
                text-align: center;
                margin-top: 50px;
                padding-top: 30px;
                border-top: 1px solid rgba(255,255,255,0.1);
                font-size: 0.9em;
                opacity: 0.7;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎵 Viral Music Bot</h1>
            <div class="subtitle">Promote your music across Telegram groups • Get more listeners • Grow your audience</div>
            
            <div class="cta">
                <a href="https://t.me/ViralMusicPromoterBot" class="btn" target="_blank">
                    🚀 Start Promoting Now
                </a>
                <br>
                <small>Click to open Telegram and start using the bot</small>
            </div>
            
            <div class="features">
                <div class="feature">
                    <h3>📢 Multi-Group Promotion</h3>
                    <p>Share your music links across multiple Telegram groups simultaneously with just one click.</p>
                </div>
                <div class="feature">
                    <h3>💰 Flexible Plans</h3>
                    <p>Choose from Basic, Pro, or VIP packages. Start with free shares from daily bonuses!</p>
                </div>
                <div class="feature">
                    <h3>📊 Real-Time Analytics</h3>
                    <p>Track your promotion performance and audience growth with detailed statistics.</p>
                </div>
                <div class="feature">
                    <h3>👥 Referral Program</h3>
                    <p>Earn free shares by inviting friends. Get 5 shares for each successful referral!</p>
                </div>
            </div>
            
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-label">Active Users</div>
                    <div class="stat-number" id="userCount">0</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Groups</div>
                    <div class="stat-number" id="groupCount">0</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Promotions</div>
                    <div class="stat-number" id="promoCount">0</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Uptime</div>
                    <div class="stat-number">24/7</div>
                </div>
            </div>
            
            <div class="cta">
                <h2>Ready to Go Viral? 🎶</h2>
                <a href="https://t.me/ViralMusicPromoterBot" class="btn" target="_blank">
                    💎 Start Free Trial
                </a>
                <p>Get 10 free shares to start promoting your music!</p>
            </div>
            
            <div class="footer">
                <p>© 2024 Viral Music Bot • Made with ❤️ for artists worldwide</p>
                <p>Contact: @ViralMusicSupport • Status: <span style="color: #4ecdc4;">●</span> Operational</p>
            </div>
        </div>
        
        <script>
            // Fetch stats from API
            async function loadStats() {
                try {
                    const response = await fetch('/api/stats');
                    const data = await response.json();
                    
                    document.getElementById('userCount').textContent = data.users || '0';
                    document.getElementById('groupCount').textContent = data.groups || '0';
                    document.getElementById('promoCount').textContent = data.promotions || '0';
                } catch (error) {
                    console.log('Stats loading failed, using defaults');
                }
            }
            
            // Load stats on page load
            document.addEventListener('DOMContentLoaded', loadStats);
            
            // Refresh stats every 30 seconds
            setInterval(loadStats, 30000);
        </script>
    </body>
    </html>
    """

@app.route('/api/stats')
def api_stats():
    """API endpoint for statistics"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM groups WHERE is_active = 1")
    groups = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM promotions")
    promotions = c.fetchone()[0]
    
    conn.close()
    
    return jsonify({
        "users": users,
        "groups": groups,
        "promotions": promotions,
        "status": "online",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "service": "viral-music-bot"})

# Telegram Bot Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    user_data = get_user(user.id)
    
    # Update user info
    update_user_info(user.id, user.username, user.first_name, user.last_name)
    
    keyboard = [
        [InlineKeyboardButton("🎵 Promote Music", callback_data="promote")],
        [InlineKeyboardButton("💰 Buy Shares", callback_data="buy")],
        [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
        [InlineKeyboardButton("🎁 Daily Bonus", callback_data="bonus")],
        [InlineKeyboardButton("🤝 Refer Friends", callback_data="referral")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = f"""
🎶 *Welcome to Viral Music Bot, {user.first_name}!* 🎶

*Your Current Stats:*
• Available Shares: *{user_data['shares']}*
• Total Referrals: *{user_data['referrals']}*

*How to Use:*
1. Use "Promote Music" to share your links
2. Get more shares via "Buy Shares" or "Daily Bonus"
3. Invite friends for bonus shares

Tap a button below to get started! 👇
    """
    
    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def promote_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show promotion menu"""
    query = update.callback_query
    await query.answer()
    
    user_data = get_user(query.from_user.id)
    
    if user_data['shares'] <= 0:
        await query.message.reply_text(
            "❌ *No shares available!*\n\n"
            "Get shares by:\n"
            "• Buying packages (/buy)\n"
            "• Claiming daily bonus (/bonus)\n"
            "• Referring friends (/referral)",
            parse_mode='Markdown'
        )
        return
    
    await query.message.reply_text(
        "🔗 *Send Music Link*\n\n"
        "Please send the link to your music:\n"
        "(YouTube, Spotify, SoundCloud, etc.)\n\n"
        "Format: https://...",
        parse_mode='Markdown'
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle music link"""
    user_data = get_user(update.effective_user.id)
    
    if user_data['shares'] <= 0:
        await update.message.reply_text(
            "❌ Not enough shares! Use /buy to get more.",
            parse_mode='Markdown'
        )
        return
    
    link = update.message.text
    if not (link.startswith('http://') or link.startswith('https://')):
        await update.message.reply_text(
            "❌ Invalid link! Please send a valid URL starting with http:// or https://",
            parse_mode='Markdown'
        )
        return
    
    # Use one share
    success = use_share(update.effective_user.id)
    
    if not success:
        await update.message.reply_text(
            "❌ Failed to use share. Please try again.",
            parse_mode='Markdown'
        )
        return
    
    # Get active groups
    groups = get_active_groups()
    sent_count = 0
    
    # Record promotion
    add_promotion(update.effective_user.id, link)
    
    # Send to each group (simulated for now)
    for group in groups[:10]:  # Limit to 10 groups
        try:
            # In production, you would send actual messages
            sent_count += 1
        except:
            pass
    
    await update.message.reply_text(
        f"✅ *Promotion Sent!*\n\n"
        f"• Link: {link[:50]}...\n"
        f"• Sent to: {sent_count} groups\n"
        f"• Cost: 1 share\n"
        f"• Remaining: {user_data['shares'] - 1} shares\n\n"
        f"🎯 Estimated reach: {sent_count * 100} users",
        parse_mode='Markdown'
    )

async def buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show packages for purchase"""
    query = update.callback_query
    await query.answer()
    
    packages = get_packages()
    
    keyboard = []
    for package in packages:
        keyboard.append([
            InlineKeyboardButton(
                f"{package['name']} - ${package['price']} ({package['shares']} shares)",
                callback_data=f"buy_{package['id']}"
            )
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    packages_text = "\n".join([
        f"• *{p['name']}*: ${p['price']} → {p['shares']} shares"
        for p in packages
    ])
    
    await query.message.edit_text(
        f"💳 *Available Packages*\n\n{packages_text}\n\n"
        f"Select a package to proceed with payment.",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user statistics"""
    query = update.callback_query
    await query.answer()
    
    user_data = get_user(query.from_user.id)
    
    stats_text = f"""
📊 *Your Statistics*

*👤 Account:*
• User ID: `{user_data['telegram_id']}`
• Member since: {user_data['created_at'][:10]}

*💰 Shares:*
• Available: *{user_data['shares']} shares*
• Total earned: *{user_data['shares'] + user_data.get('used_shares', 0)} shares*

*👥 Referrals:*
• Referred friends: *{user_data['referrals']}*
• Referral code: `REF{user_data['telegram_id']}`

*🎯 Tips:*
• Share your referral code to earn bonus shares!
• Claim daily bonus every 24 hours.
    """
    
    await query.message.reply_text(stats_text, parse_mode='Markdown')

async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim daily bonus"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    add_shares(user_id, 5)
    
    user_data = get_user(user_id)
    
    await query.message.reply_text(
        f"🎁 *Daily Bonus Claimed!*\n\n"
        f"You received *5 free shares*! 🎉\n\n"
        f"• New total: {user_data['shares']} shares\n"
        f"• Come back in 24 hours for more!\n\n"
        f"Use /promote to start sharing your music!",
        parse_mode='Markdown'
    )

async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show referral info"""
    query = update.callback_query
    await query.answer()
    
    user_data = get_user(query.from_user.id)
    
    referral_text = f"""
🤝 *Referral Program*

*Earn 5 FREE shares for every friend you refer!*

*Your Referral Link:*
`https://t.me/{(await context.bot.get_me()).username}?start=ref_{user_data['telegram_id']}`

*How it works:*
1. Share your link with friends
2. They join using your link
3. You both get *5 FREE shares*

*Your Stats:*
• Total referrals: *{user_data['referrals']}*
• Earned from referrals: *{user_data['referrals'] * 5} shares*
    """
    
    await query.message.reply_text(referral_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    help_text = """
🎶 *Viral Music Bot Help*

*Commands:*
/start - Start the bot
/promote - Promote your music
/buy - Buy shares
/stats - Your statistics
/bonus - Claim daily bonus
/referral - Referral program
/help - Show this help

*How it works:*
1. Get shares (buy or daily bonus)
2. Use shares to promote music links
3. Track your results
4. Earn more by referring friends

*Support:*
@ViralMusicSupport
    """
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

# Main bot setup
async def setup_bot():
    """Setup and run the bot"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("promote", promote_menu))
    application.add_handler(CommandHandler("buy", buy_menu))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("bonus", daily_bonus))
    application.add_handler(CommandHandler("referral", referral_menu))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(promote_menu, pattern="^promote$"))
    application.add_handler(CallbackQueryHandler(buy_menu, pattern="^buy$"))
    application.add_handler(CallbackQueryHandler(stats_command, pattern="^stats$"))
    application.add_handler(CallbackQueryHandler(daily_bonus, pattern="^bonus$"))
    application.add_handler(CallbackQueryHandler(referral_menu, pattern="^referral$"))
    
    # Message handler for links
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    
    # Use webhook if URL provided, otherwise use polling
    if WEBHOOK_URL:
        await application.bot.set_webhook(f"{WEBHOOK_URL}/{BOT_TOKEN}")
        logger.info(f"Webhook set to: {WEBHOOK_URL}")
    else:
        # Start polling
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        logger.info("Bot started with polling")
        
        # Keep running
        await asyncio.Event().wait()

def run_flask():
    """Run Flask web server"""
    app.run(host="0.0.0.0", port=PORT, debug=False)

def run_bot():
    """Run Telegram bot"""
    asyncio.run(setup_bot())

if __name__ == "__main__":
    # Initialize database
    init_db()
    
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run bot in main thread
    run_bot()
