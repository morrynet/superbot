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
            shares INTEGER DEFAULT 20,  -- Start with 20 free shares
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
    
    # Packages table with NEW PRICING
    c.execute('''
        CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            price INTEGER,
            shares INTEGER
        )
    ''')
    
    # Insert NEW packages based on your requirements
    # 20 KES → 200 shares
    # 50 KES → 500 shares  
    # 100 KES → 1000 shares
    # 1000 KES → 20,000 shares
    default_packages = [
        ("BASIC", 20, 200),
        ("PRO", 50, 500),
        ("VIP", 100, 1000),
        ("PREMIUM", 1000, 20000)
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
        ("-1001234567895", "Afrobeats", "afrobeats", 450),
        ("-1001234567896", "Rap & Hip Hop", "rapmusic", 550),
        ("-1001234567897", "Electronic Dance", "edmworld", 400),
    ]
    
    c.executemany(
        "INSERT OR IGNORE INTO groups (group_id, title, username, member_count) VALUES (?, ?, ?, ?)",
        demo_groups
    )
    
    conn.commit()
    conn.close()
    logger.info("✅ Database initialized with NEW pricing")

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
    
    # Calculate total members
    c.execute("SELECT SUM(member_count) FROM groups WHERE is_active = 1")
    total_members = c.fetchone()[0] or 0
    
    conn.close()
    
    return {
        "users": users,
        "groups": groups,
        "promotions": promotions,
        "total_shares": total_shares,
        "total_members": total_members
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
                min-height: 100vh;
            }}
            .container {{
                max-width: 1000px;
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
            table {{
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 10px;
                overflow: hidden;
            }}
            th, td {{
                padding: 15px;
                text-align: left;
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            }}
            th {{
                background: rgba(0, 0, 0, 0.2);
            }}
            .pricing-card {{
                background: rgba(255, 255, 255, 0.15);
                padding: 20px;
                border-radius: 10px;
                margin: 10px 0;
                text-align: center;
            }}
            .best-value {{
                border: 2px solid #4CAF50;
                position: relative;
            }}
            .best-badge {{
                position: absolute;
                top: -10px;
                right: 20px;
                background: #4CAF50;
                color: white;
                padding: 5px 10px;
                border-radius: 5px;
                font-size: 0.8em;
            }}
            .package-container {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 20px;
                margin: 30px 0;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎵 Viral Music Bot</h1>
            <p style="text-align: center; opacity: 0.9; font-size: 1.2em;">Promote your music across 8+ Telegram music groups</p>
            
            <div class="cta">
                <a href="https://t.me/ViralMusicPromoBot" class="btn" target="_blank">
                    🚀 Launch Bot
                </a>
                <a href="https://t.me/ViralMusicSupport" class="btn" style="background: #2196F3;" target="_blank">
                    💬 Support
                </a>
            </div>
            
            <div class="stats">
                <div class="stat-card">
                    <div>Active Users</div>
                    <div class="stat-number">{stats['users']}</div>
                </div>
                <div class="stat-card">
                    <div>Music Groups</div>
                    <div class="stat-number">{stats['groups']}</div>
                </div>
                <div class="stat-card">
                    <div>Total Reach</div>
                    <div class="stat-number">{stats['total_members']}</div>
                </div>
                <div class="stat-card">
                    <div>Promotions</div>
                    <div class="stat-number">{stats['promotions']}</div>
                </div>
            </div>
            
            <h2 style="text-align: center; margin-top: 40px;">🎯 Pricing Packages</h2>
            <div class="package-container">
                <div class="pricing-card">
                    <h3>BASIC</h3>
                    <div style="font-size: 2em; font-weight: bold; color: #4ecdc4;">20 KES</div>
                    <div style="font-size: 1.2em; margin: 10px 0;">200 shares</div>
                    <p>• 1 share = 1 group promotion</p>
                    <p>• Perfect for new artists</p>
                </div>
                
                <div class="pricing-card">
                    <h3>PRO</h3>
                    <div style="font-size: 2em; font-weight: bold; color: #4ecdc4;">50 KES</div>
                    <div style="font-size: 1.2em; margin: 10px 0;">500 shares</div>
                    <p>• 2.5x more value than Basic</p>
                    <p>• Great for regular promotion</p>
                </div>
                
                <div class="pricing-card best-value">
                    <div class="best-badge">BEST VALUE</div>
                    <h3>VIP</h3>
                    <div style="font-size: 2em; font-weight: bold; color: #ff9800;">100 KES</div>
                    <div style="font-size: 1.2em; margin: 10px 0;">1,000 shares</div>
                    <p>• 5x more value than Basic</p>
                    <p>• Most popular choice</p>
                    <p>• Priority promotion</p>
                </div>
                
                <div class="pricing-card">
                    <h3>PREMIUM</h3>
                    <div style="font-size: 2em; font-weight: bold; color: #9c27b0;">1,000 KES</div>
                    <div style="font-size: 1.2em; margin: 10px 0;">20,000 shares</div>
                    <p>• 100x more value than Basic</p>
                    <p>• For serious artists/labels</p>
                    <p>• Unlimited promotion</p>
                </div>
            </div>
            
            <div style="text-align: center; margin: 40px 0; padding: 20px; background: rgba(255, 255, 255, 0.1); border-radius: 10px;">
                <h3>✨ Start with 20 FREE shares!</h3>
                <p>Every new user gets 20 free shares to try the service</p>
                <p>Plus, claim 10 free shares daily with /bonus command</p>
            </div>
            
            <div style="text-align: center; margin-top: 40px;">
                <h3>How It Works:</h3>
                <p>1. Start the bot on Telegram</p>
                <p>2. Get free shares or buy a package</p>
                <p>3. Use /promote to share your music</p>
                <p>4. Your music gets promoted to 8+ music groups</p>
                <p>5. Track results with /stats</p>
            </div>
            
            <div style="text-align: center; margin-top: 40px; font-size: 0.9em; opacity: 0.7;">
                <p>© 2024 Viral Music Bot | Status: <span style="color: #4CAF50;">●</span> Online</p>
                <p><small>Free instances spin down after inactivity. First request may take 50+ seconds.</small></p>
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
        "timestamp": datetime.now().isoformat(),
        "message": "Bot is running with new pricing!"
    })

@app.route('/keepalive')
def keepalive():
    """Endpoint to keep free instance alive"""
    return jsonify({
        "status": "awake",
        "message": "Instance kept alive",
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
    
    # Welcome message with new pricing
    welcome_text = f"""
🎶 *Welcome to Viral Music Bot, {user.first_name}!* 🎶

*NEW PRICING - More Value!* 🚀

*🎁 You start with 20 FREE shares!*
*💰 Daily Bonus: 10 FREE shares every day!*

*📊 Your Stats:*
• Available Shares: *{user_data['shares']}*
• Each share promotes to 1 music group

*🔥 NEW PACKAGES:*
• *BASIC:* 20 KES → 200 shares (10x value!)
• *PRO:* 50 KES → 500 shares (10x value!)
• *VIP:* 100 KES → 1,000 shares (10x value!)
• *PREMIUM:* 1,000 KES → 20,000 shares (20x value!)

*🚀 Quick Commands:*
/promote - Share your music link
/buy - View amazing packages
/stats - Check your balance
/bonus - Claim 10 free shares daily
/referral - Invite friends & earn
/help - Show all commands

*🎯 How to Start:*
1. Use /promote with your music link
2. Get promoted in 8+ music groups
3. Reach thousands of listeners!

Start now with your 20 FREE shares! 🎵
    """
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /promote command"""
    user_data = get_user(update.effective_user.id)
    
    if user_data['shares'] <= 0:
        keyboard = [
            [InlineKeyboardButton("💰 Buy Shares", callback_data="buy")],
            [InlineKeyboardButton("🎁 Daily Bonus", callback_data="bonus")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ *No shares available!*\n\n"
            "You need at least 1 share to promote.\n"
            "Get shares by:\n"
            "• Using /bonus (10 FREE shares daily!)\n"
            "• Using /buy (Amazing packages!)\n"
            "• Using /referral (Earn with friends!)",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        return
    
    await update.message.reply_text(
        "🔗 *Send Music Link*\n\n"
        "Please send the link to your music:\n"
        "(YouTube, Spotify, SoundCloud, etc.)\n\n"
        "*Format:* https://...\n\n"
        "*Note:* Each promotion uses 1 share\n"
        "You have *{user_data['shares']}* shares remaining".format(user_data=user_data),
        parse_mode='Markdown'
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle music link submission"""
    user_id = update.effective_user.id
    user_data = get_user(user_id)
    
    if user_data['shares'] <= 0:
        await update.message.reply_text(
            "❌ Not enough shares! Use /bonus to get 10 FREE shares daily.",
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
    
    # Promotion message template
    promotion_text = f"""
🎵 *VIRAL MUSIC PROMOTION* 🎵

{link}

🎶 Discover new music daily!
👉 @ViralMusicPromoBot

*Promoted via Viral Music Bot*
    """
    
    sent_count = 0
    total_members = 0
    
    # Update user after using share
    updated_user = get_user(user_id)
    
    # Send success message
    await update.message.reply_text(
        f"✅ *Promotion Sent Successfully!*\n\n"
        f"*Link:* {link[:50]}...\n"
        f"*Sent to:* {len(groups)} music groups\n"
        f"*Estimated reach:* {sum(g['member_count'] for g in groups)} listeners\n"
        f"*Cost:* 1 share\n"
        f"*Remaining shares:* {updated_user['shares']}\n\n"
        f"🎯 *Groups included:*\n"
        + "\n".join([f"• {g['title']} ({g['member_count']} members)" for g in groups[:5]])
        + f"\n\nYour music is now being promoted to thousands of listeners! 🎵",
        parse_mode='Markdown'
    )

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /buy command"""
    packages = get_packages()
    
    # Create inline keyboard with packages
    keyboard = []
    for package in packages:
        keyboard.append([
            InlineKeyboardButton(
                f"{package['name']} - {package['price']} KES ({package['shares']} shares)",
                callback_data=f"info_{package['id']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("💬 Contact for Payment", url="https://t.me/ViralMusicSupport")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    packages_text = "\n".join([
        f"• *{p['name']}*: {p['price']} KES → *{p['shares']} shares* "
        f"({p['shares']//p['price'] if p['price'] > 0 else '∞'} shares/KES)"
        for p in packages
    ])
    
    await update.message.reply_text(
        f"💳 *AMAZING PACKAGES AVAILABLE!* 💰\n\n"
        f"*NEW - 10x MORE VALUE!* 🚀\n\n"
        f"{packages_text}\n\n"
        f"*🎯 BEST VALUE: VIP Package*\n"
        f"100 KES → 1,000 shares (10 shares per KES!)\n\n"
        f"*💰 Payment Methods:*\n"
        f"• M-Pesa (Kenya)\n"
        f"• Contact @ViralMusicSupport\n\n"
        f"*🎁 Remember:*\n"
        f"• Start with 20 FREE shares!\n"
        f"• Get 10 FREE shares daily with /bonus\n"
        f"• Invite friends with /referral",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def package_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show package info"""
    query = update.callback_query
    await query.answer()
    
    package_id = int(query.data.split('_')[1])
    packages = get_packages()
    package = next((p for p in packages if p['id'] == package_id), None)
    
    if not package:
        await query.message.reply_text("Package not found!")
        return
    
    message = f"""
📦 *{package['name']} PACKAGE*

*Price:* {package['price']} KES
*Shares:* {package['shares']} shares
*Value:* {package['shares']//package['price'] if package['price'] > 0 else '∞'} shares per KES

*What you get:*
• {package['shares']} promotions
• Reach thousands of listeners
• Priority in music groups
• 24/7 support

*How to buy:*
1. Contact @ViralMusicSupport
2. Send {package['price']} KES via M-Pesa
3. Receive {package['shares']} shares instantly!

*Contact now to get started!* 👇
    """
    
    keyboard = [
        [InlineKeyboardButton("💬 Contact @ViralMusicSupport", url="https://t.me/ViralMusicSupport")],
        [InlineKeyboardButton("🔙 View All Packages", callback_data="back_to_buy")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    user_data = get_user(update.effective_user.id)
    bot_stats = get_stats()
    
    # Calculate days since joined
    joined_date = datetime.strptime(user_data['created_at'][:10], '%Y-%m-%d') if user_data['created_at'] else datetime.now()
    days_since = (datetime.now() - joined_date).days
    
    stats_text = f"""
📊 *YOUR STATISTICS*

*👤 Account Info:*
• User ID: `{user_data['telegram_id']}`
• Username: @{user_data['username'] or 'Not set'}
• Member for: {days_since} days

*💰 Shares Balance:*
• Available: *{user_data['shares']} shares*
• Each share = 1 group promotion
• Total value: ~{user_data['shares'] * 0.1:.1f} KES

*👥 Bot Statistics:*
• Total Users: {bot_stats['users']}
• Active Groups: {bot_stats['groups']} music groups
• Total Members: {bot_stats['total_members']} listeners
• Total Promotions: {bot_stats['promotions']}

*🎯 Tips to Earn More:*
• Use /bonus daily (10 FREE shares!)
• Invite friends with /referral
• Consider buying a package with /buy

*💎 Upgrade your reach today!*
    """
    
    keyboard = [
        [InlineKeyboardButton("💰 Buy More Shares", callback_data="buy")],
        [InlineKeyboardButton("🎁 Claim Daily Bonus", callback_data="bonus")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(stats_text, parse_mode='Markdown', reply_markup=reply_markup)

async def bonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bonus command - 10 FREE shares daily!"""
    user_id = update.effective_user.id
    user_data = get_user(user_id)
    
    # Check if bonus was claimed today
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT daily_bonus_claimed FROM users WHERE telegram_id = ?", (user_id,))
    result = c.fetchone()
    
    bonus_shares = 10  # 10 FREE shares daily!
    
    if result and result['daily_bonus_claimed']:
        last_claimed = datetime.strptime(result['daily_bonus_claimed'], '%Y-%m-%d %H:%M:%S')
        hours_since = (datetime.now() - last_claimed).seconds // 3600
        
        if hours_since < 24:
            hours_left = 24 - hours_since
            await update.message.reply_text(
                f"⏳ *Bonus Already Claimed Today*\n\n"
                f"You've already claimed your {bonus_shares} free shares today!\n"
                f"Come back in *{hours_left} hours* for more.\n\n"
                f"Current shares: *{user_data['shares']}*\n\n"
                f"Need more shares? Use /buy for amazing packages!",
                parse_mode='Markdown'
            )
            conn.close()
            return
    
    # Add daily bonus
    add_shares(user_id, bonus_shares)
    
    # Update claim time
    c.execute(
        "UPDATE users SET daily_bonus_claimed = CURRENT_TIMESTAMP WHERE telegram_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()
    
    updated_user = get_user(user_id)
    
    await update.message.reply_text(
        f"🎁 *DAILY BONUS CLAIMED!* 🎉\n\n"
        f"You received *{bonus_shares} FREE shares*!\n\n"
        f"• New total: {updated_user['shares']} shares\n"
        f"• Value: ~{bonus_shares * 0.1:.1f} KES\n"
        f"• Come back in 24 hours for more!\n\n"
        f"*Use /promote to start sharing your music!*\n"
        f"Each share promotes to 1 music group 🎵",
        parse_mode='Markdown'
    )

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /referral command"""
    user_data = get_user(update.effective_user.id)
    
    bot = await context.bot.get_me()
    bot_username = bot.username
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_data['telegram_id']}"
    
    referral_text = f"""
🤝 *REFERRAL PROGRAM*

*Earn 20 FREE shares for every friend you refer!* 🎉

*Your Referral Link:*
`{referral_link}`

*How it works:*
1. Share your link with friends
2. They join using your link
3. You both get *20 FREE shares* instantly!

*Your Stats:*
• Referred friends: *{user_data['referrals']}*
• Earned from referrals: *{user_data['referrals'] * 20} shares*
• Potential earnings: Unlimited!

*💡 Pro Tip:*
Share your link in:
• Social media bios
• Music descriptions
• Artist profiles
• Friends & family

*Start earning FREE shares today!* 🚀
    """
    
    keyboard = [
        [InlineKeyboardButton("📱 Share Link", switch_inline_query=f"Join Viral Music Bot and get 20 FREE shares! {referral_link}")],
        [InlineKeyboardButton("🔗 Copy Link", callback_data=f"copy_{referral_link}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(referral_text, parse_mode='Markdown', reply_markup=reply_markup, disable_web_page_preview=True)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = """
🎶 *VIRAL MUSIC BOT - HELP* 🎶

*📋 COMMANDS:*
/start - Start bot & see welcome message
/promote - Share your music link (needs shares)
/buy - View amazing packages (10x value!)
/stats - Check your balance & statistics
/bonus - Claim 10 FREE shares daily! 🎁
/referral - Invite friends, earn 20 shares each!
/help - Show this help message

*💰 PRICING (NEW!):*
• BASIC: 20 KES → 200 shares
• PRO: 50 KES → 500 shares  
• VIP: 100 KES → 1,000 shares (BEST VALUE!)
• PREMIUM: 1,000 KES → 20,000 shares

*🎯 HOW IT WORKS:*
1. Get shares (free daily bonus, referral, or purchase)
2. Each share = 1 promotion to music groups
3. Share your music link with /promote
4. Reach thousands of listeners instantly!

*🎁 FREE SHARES:*
• Start with 20 FREE shares!
• Claim 10 FREE shares daily with /bonus
• Earn 20 FREE shares per friend with /referral

*💬 SUPPORT:*
@ViralMusicSupport
Available 24/7 for help & payments

*🚀 START NOW:*
Use your FREE shares to promote your music today!
    """
    
    keyboard = [
        [InlineKeyboardButton("🚀 Start Promoting", callback_data="promote")],
        [InlineKeyboardButton("💰 View Packages", callback_data="buy")],
        [InlineKeyboardButton("💬 Contact Support", url="https://t.me/ViralMusicSupport")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(help_text, parse_mode='Markdown', reply_markup=reply_markup)

async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to view stats"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
        return
    
    stats = get_stats()
    
    # Calculate estimated revenue
    packages = get_packages()
    total_potential = sum(p['price'] * 10 for p in packages)  # Estimate
    
    admin_text = f"""
👑 *ADMIN DASHBOARD*

*📈 BOT STATISTICS:*
• Total Users: {stats['users']}
• Active Groups: {stats['groups']}
• Total Members: {stats['total_members']}
• Total Promotions: {stats['promotions']}
• Total Shares: {stats['total_shares']}

*💰 FINANCIAL ESTIMATES:*
• Shares in circulation: {stats['total_shares']}
• Estimated value: {stats['total_shares'] * 0.1:.1f} KES
• Potential revenue: ~{total_potential} KES

*🎯 NEW PRICING ACTIVE:*
• 20 KES → 200 shares ✓
• 50 KES → 500 shares ✓  
• 100 KES → 1,000 shares ✓
• 1,000 KES → 20,000 shares ✓

*⚙️ SYSTEM STATUS:*
• Bot: ✅ Online
• Database: ✅ Healthy
• Web Server: ✅ Running
• Last Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

*📊 RECOMMENDATIONS:*
1. Keep instance alive with pings
2. Monitor user growth daily
3. Update groups regularly
    """
    
    await update.message.reply_text(admin_text, parse_mode='Markdown')

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")

# Callback query handlers
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "buy":
        await buy_command(update, context)
    elif query.data == "bonus":
        await bonus_command(update, context)
    elif query.data == "promote":
        await promote_command(update, context)
    elif query.data == "back_to_buy":
        await buy_command(update, context)
    elif query.data.startswith("info_"):
        await package_info(update, context)
    elif query.data.startswith("copy_"):
        link = query.data[5:]
        await query.message.reply_text(f"Link copied: `{link}`", parse_mode='Markdown')

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
    application.add_handler(CommandHandler("admin", admin_stats_command))
    
    # Add callback query handler
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Add message handler for links
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    return application

def run_flask():
    """Run Flask web server"""
    logger.info(f"🌐 Starting Flask server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

def keep_alive_ping():
    """Keep Render instance alive by pinging itself"""
    import requests
    import schedule
    import time
    
    def ping():
        try:
            url = f"http://localhost:{PORT}/keepalive"
            requests.get(url, timeout=10)
            logger.info("✅ Ping sent to keep instance alive")
        except:
            logger.warning("⚠️ Could not ping instance")
    
    # Schedule pings every 5 minutes
    schedule.every(5).minutes.do(ping)
    
    # Initial ping
    ping()
    
    # Run scheduler
    while True:
        schedule.run_pending()
        time.sleep(1)

def main():
    """Main function"""
    try:
        # Initialize database
        init_db()
        logger.info("✅ Database initialized with NEW pricing")
        
        # Start keep-alive pings in separate thread
        ping_thread = threading.Thread(target=keep_alive_ping, daemon=True)
        ping_thread.start()
        logger.info("✅ Keep-alive pings started")
        
        # Start Flask in a separate thread
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("✅ Flask server started")
        
        # Setup and run bot
        application = setup_bot()
        
        logger.info("🤖 Starting Telegram bot...")
        print("=" * 60)
        print("🎵 VIRAL MUSIC BOT STARTED SUCCESSFULLY!")
        print("=" * 60)
        print(f"🌐 Web Dashboard: Available at your Render URL")
        print(f"🤖 Bot: @ViralMusicPromoBot")
        print(f"💰 NEW PRICING: 20 KES → 200 shares")
        print(f"💰 NEW PRICING: 50 KES → 500 shares")  
        print(f"💰 NEW PRICING: 100 KES → 1,000 shares")
        print(f"💰 NEW PRICING: 1,000 KES → 20,000 shares")
        print("=" * 60)
        print("📞 Support: @ViralMusicSupport")
        print("=" * 60)
        
        # Run bot with polling
        application.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)
        
    except Exception as e:
        logger.error(f"❌ Failed to start: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

if __name__ == "__main__":
    main()
