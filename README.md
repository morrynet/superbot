# 🎵 Viral Music Bot

A Telegram bot for promoting music across multiple groups.

## Features
- 📢 Share music links to multiple Telegram groups
- 💰 Flexible packages (Basic/Pro/VIP)
- 🎁 Daily bonus shares
- 👥 Referral program
- 📊 Real-time statistics
- 🌐 Beautiful web dashboard

## Setup on Render

1. **Fork or clone this repository**
2. **Go to [render.com](https://render.com)**
3. **Click "New +" → "Web Service"**
4. **Connect your GitHub repository**
5. **Configure:**
   - Name: `viral-music-bot`
   - Environment: `Docker`
   - Branch: `main`
   - Instance Type: `Free`
   - Root Directory: (leave empty)
6. **Add Environment Variables:**
   - `BOT_TOKEN`: Your Telegram bot token
   - `ADMIN_IDS`: Your Telegram ID (comma-separated)
   - `PORT`: 10000
7. **Click "Create Web Service"**

## Bot Commands
- `/start` - Start the bot
- `/promote` - Promote music
- `/buy` - Buy shares
- `/stats` - View statistics
- `/bonus` - Claim daily bonus
- `/referral` - Referral program
- `/help` - Show help

## Web Dashboard
After deployment, visit your Render URL to see:
- Bot statistics
- User dashboard
- Service status

## Support
Telegram: @ViralMusicSupport
