# WhatsApp Button Bot

A production-ready WhatsApp chatbot built with FastAPI and Supabase, designed for deployment on Railway.

## Features

- 🔘 Interactive buttons and list menus
- 📦 Order management with history tracking
- 👥 User tracking and analytics
- ⚡ Rate limiting to prevent abuse
- 📝 Message logging for debugging
- 🔒 Webhook signature verification
- 🏥 Health checks for monitoring
- 🗄️ Supabase for database (PostgreSQL)

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   WhatsApp      │────▶│   Railway       │────▶│   Supabase      │
│   (Meta Cloud)  │◀────│   (FastAPI)     │◀────│   (PostgreSQL)  │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## Quick Start

### 1. Set Up Supabase

1. Create a project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** and run the migration script from `supabase/migrations/001_initial_schema.sql`
3. Get your credentials from **Project Settings > API**:
   - Project URL → `SUPABASE_URL`
   - service_role key → `SUPABASE_SERVICE_KEY`

### 2. Configure Meta WhatsApp

1. Go to [developers.facebook.com](https://developers.facebook.com)
2. Create or select your app
3. Add WhatsApp product
4. Get from **WhatsApp > API Setup**:
   - Temporary access token → `WHATSAPP_ACCESS_TOKEN`
   - Phone number ID → `WHATSAPP_PHONE_NUMBER_ID`
5. Set webhook URL to `https://your-railway-app.railway.app/webhook/whatsapp`
6. Subscribe to `messages` webhook field

### 3. Deploy to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new)

1. Connect your GitHub repository
2. Add environment variables in Railway:
   ```
   WHATSAPP_ACCESS_TOKEN=your_token
   WHATSAPP_PHONE_NUMBER_ID=your_phone_id
   WHATSAPP_VERIFY_TOKEN=cpc
   SUPABASE_URL=https://xxx.supabase.co
   SUPABASE_SERVICE_KEY=your_service_key
   ENVIRONMENT=production
   ```
3. Deploy!

### 4. Verify Webhook

1. In Meta Developer Portal, configure webhook:
   - Callback URL: `https://your-app.railway.app/webhook/whatsapp`
   - Verify token: `cpc` (or whatever you set)
2. Subscribe to **messages** field

## Local Development

```bash
# Clone the repo
git clone <your-repo>
cd whatsapp-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy env template and fill in values
cp .env.example .env

# Run locally
python main.py
```

For local webhook testing, use [ngrok](https://ngrok.com):
```bash
ngrok http 8000
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `WHATSAPP_ACCESS_TOKEN` | Meta API access token | ✅ |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp phone number ID | ✅ |
| `WHATSAPP_VERIFY_TOKEN` | Webhook verification token | ✅ |
| `WHATSAPP_APP_SECRET` | App secret for signature verification | ⚠️ Recommended |
| `SUPABASE_URL` | Supabase project URL | ✅ |
| `SUPABASE_SERVICE_KEY` | Supabase service role key | ✅ |
| `RATE_LIMIT_REQUESTS` | Max requests per window (default: 30) | ❌ |
| `RATE_LIMIT_WINDOW_SECONDS` | Rate limit window (default: 60) | ❌ |
| `ENVIRONMENT` | `production` or `development` | ❌ |
| `DEBUG` | Enable debug mode | ❌ |
| `PORT` | Server port (default: 8000) | ❌ |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Root info |
| `/health` | GET | Health check |
| `/webhook/whatsapp` | GET | Webhook verification |
| `/webhook/whatsapp` | POST | Incoming messages |
| `/admin/stats` | GET | Basic statistics |

## Bot Commands

Users can type these text commands:

| Command | Action |
|---------|--------|
| `hi`, `hello`, `start` | Show main menu |
| `menu` | Show menu items |
| `order` | Start ordering |
| `more` | More options |
| `history`, `orders` | View order history |
| `help`, `contact` | Contact info |

## Database Schema

- **users** - Customer profiles
- **orders** - Order records
- **processed_messages** - Deduplication
- **rate_limits** - Rate limiting
- **message_logs** - Debugging
- **menu_items** - Dynamic menu

## Security Notes

⚠️ **Important Security Practices:**

1. **Never commit secrets** - Use environment variables
2. **Rotate tokens** - If exposed, rotate immediately in Meta Developer Portal
3. **Enable signature verification** - Set `WHATSAPP_APP_SECRET`
4. **Use service role key** - Not anon key for backend

## Troubleshooting

**Webhook not verifying?**
- Check `WHATSAPP_VERIFY_TOKEN` matches
- Ensure your app is publicly accessible

**Messages not sending?**
- Verify access token hasn't expired
- Check phone number ID is correct
- Look at Railway logs for errors

**Database errors?**
- Ensure SQL migration ran successfully
- Check Supabase credentials
- Verify RLS policies aren't blocking

## License

MIT
