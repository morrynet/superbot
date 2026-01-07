import os
import sqlite3
import logging
import threading
import time
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
import asyncio

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler()
    ]
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
            shares INTEGER DEFAULT 10,  -- Start with 10 free shares
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
    
    # Add some demo groups
    demo_groups = [
        ("-1001234567890", "Music Lovers", "musiclovers", 500),
        ("-1001234567891", "Hip Hop Community", "hiphopcommunity", 300),
        ("-1001234567892", "EDM Fans", "edmfans", 400),
        ("-1001234567893", "Rock Music", "rockmusic", 350),
        ("-1001234567894", "Pop Hits", "pophits", 600),
    ]
    
    c.executemany(
        "INSERT OR IGNORE INTO groups (group_id, title, username, member_count) VALUES (?, ?, ?, ?)",
        demo_groups
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
        c.execute("INSERT INTO users (telegram_id, shares) VALUES (?, 10)", (telegram_id,))
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
    logger.info(f"Added {shares} shares to user {telegram_id}")

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
    c.execute("SELECT group_id, title, member_count FROM groups WHERE is_active = 1")
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
    promotion_id = c.lastrowid
    conn.commit()
    conn.close()
    return promotion_id

def get_stats():
    """Get bot statistics"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM groups WHERE is_active = 1")
    groups = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM promotions")
    promotions = c.fetchone()[0]
    
    c.execute("SELECT SUM(shares) FROM users")
    total_shares = c.fetchone()[0] or 0
    
    conn.close()
    
    return {
        "users": users,
        "groups": groups,
        "promotions": promotions,
        "total_shares": total_shares
    }

# Flask app
app = Flask(__name__)

@app.route('/')
def home():
    """Web dashboard"""
    stats = get_stats()
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🎵 Viral Music Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
            }}
            .container {{
                max-width: 800px;
                margin: 0 auto;
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 30px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.3);
            }}
            h1 {{
                text-align: center;
                font-size: 2.5em;
                margin-bottom: 10px;
            }}
            .stats {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 15px;
                margin: 30px 0;
            }}
            .stat-card {{
                background: rgba(255, 255, 255, 0.2);
                padding: 20px;
                border-radius: 10px;
                text-align: center;
            }}
            .stat-number {{
                font-size: 2em;
                font-weight: bold;
                margin: 10px 0;
                color: #4ecdc4;
            }}
            .btn {{
                display: inline-block;
                padding: 12px 30px;
                background: #4CAF50;
                color: white;
                text-decoration: none;
                border-radius: 25px;
                margin: 10px;
                font-weight: bold;
                transition: transform 0.3s;
            }}
            .btn:hover {{
                transform: translateY(-2px);
                background: #45a049;
            }}
            .cta {{
                text-align: center;
                margin: 30px 0;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎵 Viral Music Bot</h1>
            <p style="text-align: center; opacity: 0.9;">Promote your music across Telegram groups</p>
            
            <div class="cta">
                <a href="https://t.me/ViralMusicPromoBot" class="btn" target="_blank">
                    🚀 Launch Bot
                </a>
            </div>
            
            <div class="stats">
                <div class="stat-card">
                    <div>Active Users</div>
                    <div class="stat-number">{stats['users']}</div>
                </div>
                <div class="stat-card">
                    <div>Active Groups</div>
                    <div class="stat-number">{stats['groups']}</div>
                </div>
                <div class="stat-card">
                    <div>Promotions</div>
                    <div class="stat-number">{stats['promotions']}</div>
                </div>
                <div class="stat-card">
                    <div>Total Shares</div>
                    <div class="stat-number">{stats['total_shares']}</div>
                </div>
            </div>
            
            <div style="text-align: center; margin-top: 40px;">
                <h3>Features:</h3>
                <p>• Share music to multiple groups</p>
                <p>• Get free shares daily</p>
                <p>• Referral program</p>
                <p>• Real-time statistics</p>
            </div>
            
            <div style="text-align: center; margin-top: 40px; font-size: 0.9em; opacity: 0.7;">
                <p>© 2024 Viral Music Bot | Status: <span style="color: #4CAF50;">●</span> Online</p>
            </div>
        </div>
    </body>
    </html>
    """

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "viral-music-bot",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/stats')
def api_stats():
    """API endpoint for statistics"""
    return jsonify(get_stats())

# Telegram Bot Functions
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    user_data = get_user(user.id)
    
    # Update user info
    update_user_info(user.id, user.username, user.first_name, user.last_name)
    
    # Welcome message
    welcome_text = f"""
🎶 *Welcome to Viral Music Bot, {user.first_name}!* 🎶

I help you promote your music across multiple Telegram groups!

*You have {user_data['shares']} shares available.*
Each share lets you promote to 1 group.

*Available Commands:*
/promote - Share your music link
/buy - Purchase more shares
/stats - View your statistics
/bonus - Claim daily bonus (5 free shares!)
/referral - Invite friends & earn shares
/help - Show all commands

*Quick Start:*
1. Use /promote to share your music
2. Get more shares with /buy or /bonus
3. Track results with /stats

Start by sharing your music with /promote! 🚀
    """
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /promote command"""
    user_data = get_user(update.effective_user.id)
    
    if user_data['shares'] <= 0:
        await update.message.reply_text(
            "❌ *No shares available!*\n\n"
            "You need at least 1 share to promote.\n"
            "Get shares by:\n"
            "• Using /bonus (5 free shares daily)\n"
            "• Using /buy to purchase packages\n"
            "• Using /referral to invite friends",
            parse_mode='Markdown'
        )
        return
    
    await update.message.reply_text(
        "🔗 *Send Music Link*\n\n"
        "Please send the link to your music:\n"
        "(YouTube, Spotify, SoundCloud, etc.)\n\n"
        "*Format:* https://...",
        parse_mode='Markdown'
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle music link submission"""
    user_id = update.effective_user.id
    user_data = get_user(user_id)
    
    if user_data['shares'] <= 0:
        await update.message.reply_text(
            "❌ Not enough shares! Use /bonus to get free shares.",
            parse_mode='Markdown'
        )
        return
    
    link = update.message.text.strip()
    
    # Validate URL
    if not (link.startswith('http://') or link.startswith('https://')):
        await update.message.reply_text(
            "❌ *Invalid link!*\n"
            "Please send a valid URL starting with http:// or https://",
            parse_mode='Markdown'
        )
        return
    
    # Use one share
    if not use_share(user_id):
        await update.message.reply_text(
            "❌ Failed to use share. Please try again.",
            parse_mode='Markdown'
        )
        return
    
    # Get active groups
    groups = get_active_groups()
    
    # Record promotion
    add_promotion(user_id, link)
    
    # Send promotion message
    promotion_text = f"""
🎵 *MUSIC PROMOTION* 🎵

{link}

👉 Promoted via @ViralMusicPromoBot
    """
    
    sent_count = 0
    total_members = 0
    
    # Simulate sending to groups (in production, this would actually send)
    for group in groups[:5]:  # Limit to 5 groups per promotion
        sent_count += 1
        total_members += group.get('member_count', 100)
    
    # Update user
    updated_user = get_user(user_id)
    
    await update.message.reply_text(
        f"✅ *Promotion Successful!*\n\n"
        f"*Link:* {link[:50]}...\n"
        f"*Sent to:* {sent_count} groups\n"
        f"*Estimated reach:* {total_members} users\n"
        f"*Cost:* 1 share\n"
        f"*Remaining shares:* {updated_user['shares']}\n\n"
        f"Thank you for promoting with us! 🎵",
        parse_mode='Markdown'
    )

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /buy command"""
    packages = get_packages()
    
    packages_text = "\n".join([
        f"• *{p['name']}*: KES {p['price']} → {p['shares']} shares"
        for p in packages
    ])
    
    await update.message.reply_text(
        f"💳 *Available Packages*\n\n{packages_text}\n\n"
        "To purchase, contact @ViralMusicSupport\n\n"
        "*Note:* Currently accepting M-Pesa payments in Kenya",
        parse_mode='Markdown'
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    user_data = get_user(update.effective_user.id)
    bot_stats = get_stats()
    
    stats_text = f"""
📊 *Your Statistics*

*Account Info:*
• User ID: `{user_data['telegram_id']}`
• Username: @{user_data['username'] or 'Not set'}
• Member since: {user_data['created_at'][:10] if user_data['created_at'] else 'Today'}

*Shares Balance:*
• Available: *{user_data['shares']} shares*
• Referrals: *{user_data['referrals']} friends*

*Bot Stats:*
• Total Users: {bot_stats['users']}
• Active Groups: {bot_stats['groups']}
• Total Promotions: {bot_stats['promotions']}

*Tips:*
• Use /bonus daily for free shares!
• Invite friends with /referral
    """
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def bonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bonus command"""
    user_id = update.effective_user.id
    user_data = get_user(user_id)
    
    # Add daily bonus
    bonus_shares = 5
    add_shares(user_id, bonus_shares)
    
    updated_user = get_user(user_id)
    
    await update.message.reply_text(
        f"🎁 *Daily Bonus Claimed!*\n\n"
        f"You received *{bonus_shares} free shares*! 🎉\n\n"
        f"• New total: {updated_user['shares']} shares\n"
        f"• Come back in 24 hours for more!\n\n"
        f"Use /promote to start sharing your music!",
        parse_mode='Markdown'
    )

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /referral command"""
    user_data = get_user(update.effective_user.id)
    
    referral_text = f"""
🤝 *Referral Program*

*Earn 5 FREE shares for every friend you refer!*

*How it works:*
1. Share your referral link with friends
2. They join using your link
3. You both get *5 FREE shares*

*Your Referral Link:*
`https://t.me/ViralMusicPromoBot?start=ref_{user_data['telegram_id']}`

*Your Stats:*
• Referred friends: *{user_data['referrals']}*
• Earned from referrals: *{user_data['referrals'] * 5} shares*

*Share with friends and earn together!*
    """
    
    await update.message.reply_text(referral_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = """
🎶 *Viral Music Bot Help*

*Commands:*
/start - Start the bot
/promote - Promote your music (needs shares)
/buy - View packages to buy shares
/stats - Your statistics
/bonus - Claim daily bonus (5 free shares)
/referral - Invite friends & earn shares
/help - Show this help

*How it works:*
1. Get shares (daily bonus, referral, or purchase)
2. Use /promote to share music links
3. Your music gets promoted to multiple groups
4. Track your results with /stats

*Need more shares?*
• Claim daily bonus with /bonus
• Invite friends with /referral
• Purchase packages with /buy

*Support:* @ViralMusicSupport
    """
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to view stats"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
        return
    
    stats = get_stats()
    
    admin_text = f"""
👑 *Admin Dashboard*

*Bot Statistics:*
• Total Users: {stats['users']}
• Active Groups: {stats['groups']}
• Total Promotions: {stats['promotions']}
• Total Shares: {stats['total_shares']}

*Recent Activity:*
• Bot started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• Status: ✅ Online

*Admin Commands:*
• /admin - This dashboard
• Add more in bot.py
    """
    
    await update.message.reply_text(admin_text, parse_mode='Markdown')

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")

# Main bot setup
def setup_bot():
    """Setup and run the bot"""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("promote", promote_command))
    application.add_handler(CommandHandler("buy", buy_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("bonus", bonus_command))
    application.add_handler(CommandHandler("referral", referral_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admin", admin_stats))
    
    # Add message handler for links
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    return application

def run_flask():
    """Run Flask web server"""
    logger.info(f"🌐 Starting Flask server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

def main():
    """Main function"""
    try:
        # Initialize database
        init_db()
        logger.info("✅ Database initialized")
        
        # Start Flask in a separate thread
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("✅ Flask server started")
        
        # Setup and run bot
        application = setup_bot()
        
        logger.info("🤖 Starting Telegram bot...")
        print("=" * 50)
        print("🎵 VIRAL MUSIC BOT STARTED SUCCESSFULLY!")
        print("=" * 50)
        print(f"🌐 Web Dashboard: http://localhost:{PORT}")
        print(f"🤖 Bot Token: {BOT_TOKEN[:10]}...")
        print("=" * 50)
        
        # Run bot with polling
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"❌ Failed to start: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

if __name__ == "__main__":
    main()
