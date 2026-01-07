import os
import sqlite3
import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
            shares INTEGER DEFAULT 20,
            referrals INTEGER DEFAULT 0,
            daily_bonus_claimed TIMESTAMP,
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
    
    # Insert packages
    packages = [
        ("BASIC", 20, 200),
        ("PRO", 50, 500),
        ("VIP", 100, 1000),
        ("PREMIUM", 1000, 20000)
    ]
    
    for name, price, shares in packages:
        c.execute("INSERT OR IGNORE INTO packages (name, price, shares) VALUES (?, ?, ?)", 
                 (name, price, shares))
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")

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
        c.execute("INSERT INTO users (telegram_id, shares) VALUES (?, 20)", (telegram_id,))
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

# Bot handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = get_user(user.id)
    update_user_info(user.id, user.username, user.first_name, user.last_name)
    
    welcome_text = f"""
🎶 *Welcome to Viral Music Bot, {user.first_name}!*

*🎁 You start with 20 FREE shares!*
*💰 Daily Bonus: 10 FREE shares every day!*

*Your Stats:*
• Available Shares: *{user_data['shares']}*
• Each share promotes to 1 music group

*🔥 NEW PACKAGES:*
• BASIC: 20 KES → 200 shares
• PRO: 50 KES → 500 shares
• VIP: 100 KES → 1,000 shares
• PREMIUM: 1,000 KES → 20,000 shares

*Commands:*
/promote - Share music link
/buy - View packages  
/stats - Your statistics
/bonus - Claim 10 free shares daily
/referral - Invite friends & earn
/help - Show all commands

Start with your FREE shares! 🚀
    """
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = get_user(update.effective_user.id)
    
    if user_data['shares'] <= 0:
        await update.message.reply_text(
            "❌ No shares! Get shares with /bonus or /buy",
            parse_mode='Markdown'
        )
        return
    
    await update.message.reply_text(
        "🔗 *Send Music Link*\n\nSend your YouTube/Spotify/SoundCloud link:",
        parse_mode='Markdown'
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user(user_id)
    
    if user_data['shares'] <= 0:
        await update.message.reply_text("❌ No shares! Use /bonus")
        return
    
    link = update.message.text.strip()
    if not (link.startswith('http://') or link.startswith('https://')):
        await update.message.reply_text("❌ Invalid URL")
        return
    
    if not use_share(user_id):
        await update.message.reply_text("❌ Failed to use share")
        return
    
    updated_user = get_user(user_id)
    
    await update.message.reply_text(
        f"✅ *Promotion Sent!*\n\n"
        f"Link: {link[:50]}...\n"
        f"Cost: 1 share\n"
        f"Remaining: {updated_user['shares']} shares\n\n"
        f"Your music is being promoted! 🎵",
        parse_mode='Markdown'
    )

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    packages = get_packages()
    
    packages_text = "\n".join([
        f"• *{p['name']}*: {p['price']} KES → {p['shares']} shares"
        for p in packages
    ])
    
    await update.message.reply_text(
        f"💳 *Available Packages*\n\n{packages_text}\n\n"
        f"Contact @ViralMusicSupport to purchase",
        parse_mode='Markdown'
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = get_user(update.effective_user.id)
    
    stats_text = f"""
📊 *Your Statistics*

*Shares:* {user_data['shares']}
*Referrals:* {user_data['referrals']}
*Member since:* {user_data['created_at'][:10] if user_data['created_at'] else 'Today'}

*Earn more:*
• /bonus - 10 free shares daily
• /referral - Invite friends
• /buy - Purchase packages
    """
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def bonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_shares(user_id, 10)
    updated_user = get_user(user_id)
    
    await update.message.reply_text(
        f"🎁 *10 FREE Shares Claimed!*\n\n"
        f"New total: {updated_user['shares']} shares\n"
        f"Come back tomorrow for more!",
        parse_mode='Markdown'
    )

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = get_user(update.effective_user.id)
    await update.message.reply_text(
        f"🤝 *Referral Program*\n\n"
        f"Earn 20 FREE shares per friend!\n\n"
        f"Your link:\n"
        f"`https://t.me/ViralMusicPromoBot?start=ref_{user_data['telegram_id']}`",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🎶 *Viral Music Bot Help*

*Commands:*
/start - Start bot
/promote - Share music (needs shares)
/buy - View packages
/stats - Your statistics  
/bonus - 10 free shares daily
/referral - Invite friends
/help - This message

*Support:* @ViralMusicSupport
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

def setup_bot():
    """Setup bot application"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("promote", promote_command))
    application.add_handler(CommandHandler("buy", buy_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("bonus", bonus_command))
    application.add_handler(CommandHandler("referral", referral_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    
    return application

def main():
    """Main function to run bot"""
    try:
        # Initialize database
        init_db()
        logger.info("✅ Database initialized")
        
        # Setup and run bot
        application = setup_bot()
        logger.info("🤖 Starting Telegram bot...")
        
        # Run bot with polling
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"❌ Bot failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
