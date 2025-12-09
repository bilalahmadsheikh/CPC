"""
WhatsApp Button Bot - Performance Optimized
FastAPI + Supabase + Railway Deployment
v2.1.0 - Performance Edition
"""

import os
import json
import hashlib
import hmac
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
from functools import lru_cache
import asyncio

import httpx
from fastapi import FastAPI, Request, Query, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables (for local development)
load_dotenv()

# ============================================================
# LOGGING CONFIGURATION
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("whatsapp_bot")

# ============================================================
# CONFIGURATION
# ============================================================
class Config:
    # WhatsApp API
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "cpc")
    WHATSAPP_APP_SECRET: str = os.getenv("WHATSAPP_APP_SECRET", "")
    
    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    
    # Performance Settings
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "300"))  # 5 minutes
    RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "30"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    ENABLE_MESSAGE_LOGGING: bool = os.getenv("ENABLE_MESSAGE_LOGGING", "false").lower() == "true"
    
    # App Settings
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    
    @classmethod
    def validate(cls) -> list[str]:
        """Validate required configuration."""
        missing = []
        if not cls.WHATSAPP_ACCESS_TOKEN:
            missing.append("WHATSAPP_ACCESS_TOKEN")
        if not cls.WHATSAPP_PHONE_NUMBER_ID:
            missing.append("WHATSAPP_PHONE_NUMBER_ID")
        if not cls.SUPABASE_URL:
            missing.append("SUPABASE_URL")
        if not cls.SUPABASE_SERVICE_KEY:
            missing.append("SUPABASE_SERVICE_KEY")
        return missing


config = Config()

# ============================================================
# SUPABASE CLIENT
# ============================================================
supabase: Optional[Client] = None
_http_client: Optional[httpx.AsyncClient] = None

def get_supabase() -> Client:
    global supabase
    if supabase is None:
        if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
            logger.error("Supabase credentials not configured")
            raise RuntimeError("Supabase credentials not configured")
        try:
            supabase = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
            logger.info("Supabase client initialized")
        except Exception as e:
            logger.error(f"Failed to create Supabase client: {e}")
            raise
    return supabase


def get_http_client() -> httpx.AsyncClient:
    """Get reusable HTTP client for better connection pooling."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
        )
    return _http_client


def is_supabase_configured() -> bool:
    """Check if Supabase is properly configured."""
    return bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY)


# ============================================================
# IN-MEMORY CACHE
# ============================================================
class Cache:
    """Simple in-memory cache with TTL."""
    
    def __init__(self):
        self._cache: Dict[str, tuple[Any, float]] = {}
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        if key in self._cache:
            value, expires_at = self._cache[key]
            if datetime.now().timestamp() < expires_at:
                return value
            else:
                del self._cache[key]
        return None
    
    def set(self, key: str, value: Any, ttl_seconds: int = None):
        """Set value in cache with TTL."""
        if ttl_seconds is None:
            ttl_seconds = config.CACHE_TTL_SECONDS
        expires_at = datetime.now().timestamp() + ttl_seconds
        self._cache[key] = (value, expires_at)
    
    def delete(self, key: str):
        """Delete key from cache."""
        if key in self._cache:
            del self._cache[key]
    
    def clear(self):
        """Clear all cache."""
        self._cache.clear()


cache = Cache()

# ============================================================
# BUTTON / LIST IDs
# ============================================================
BTN_MENU = "BTN_MENU"
BTN_ORDER = "BTN_ORDER"
BTN_MORE = "BTN_MORE"
BTN_BACK_HOME = "BTN_BACK_HOME"
BTN_CONTACT = "BTN_CONTACT"
BTN_HISTORY = "BTN_HISTORY"

ITEM_ZINGER = "ITEM_ZINGER"
ITEM_PIZZA = "ITEM_PIZZA"
ITEM_FRIES = "ITEM_FRIES"


# ============================================================
# DATABASE OPERATIONS (OPTIMIZED)
# ============================================================
class Database:
    """Optimized database operations using Supabase."""
    
    @staticmethod
    async def get_or_create_user(wa_id: str, phone: str = None) -> dict:
        """Get existing user or create new one (cached)."""
        cache_key = f"user:{wa_id}"
        cached_user = cache.get(cache_key)
        
        if cached_user:
            # Update last_active in background (non-blocking)
            asyncio.create_task(Database._update_user_activity(wa_id))
            return cached_user
        
        db = get_supabase()
        
        # Try to get existing user
        result = db.table("users").select("*").eq("wa_id", wa_id).execute()
        
        if result.data:
            user = result.data[0]
            cache.set(cache_key, user, 600)  # Cache for 10 minutes
            # Update last_active in background
            asyncio.create_task(Database._update_user_activity(wa_id))
            return user
        
        # Create new user
        new_user = {
            "wa_id": wa_id,
            "phone": phone or wa_id,
            "first_seen_at": datetime.now(timezone.utc).isoformat(),
            "last_active_at": datetime.now(timezone.utc).isoformat(),
        }
        result = db.table("users").insert(new_user).execute()
        user = result.data[0] if result.data else new_user
        cache.set(cache_key, user, 600)
        logger.info(f"New user created: {wa_id}")
        return user
    
    @staticmethod
    async def _update_user_activity(wa_id: str):
        """Update user last_active in background."""
        try:
            db = get_supabase()
            db.table("users").update({
                "last_active_at": datetime.now(timezone.utc).isoformat()
            }).eq("wa_id", wa_id).execute()
        except Exception as e:
            logger.error(f"Failed to update user activity: {e}")

    @staticmethod
    async def is_user_blocked(wa_id: str) -> bool:
        """Check if user is blocked (cached)."""
        cache_key = f"blocked:{wa_id}"
        cached_blocked = cache.get(cache_key)
        
        if cached_blocked is not None:
            return cached_blocked
        
        db = get_supabase()
        result = db.table("users").select("is_blocked").eq("wa_id", wa_id).execute()
        
        is_blocked = False
        if result.data:
            is_blocked = result.data[0].get("is_blocked", False)
        
        cache.set(cache_key, is_blocked, 300)  # Cache for 5 minutes
        return is_blocked

    @staticmethod
    async def already_processed(message_id: str) -> bool:
        """Check if message was already processed (in-memory cache first)."""
        cache_key = f"processed:{message_id}"
        
        if cache.get(cache_key):
            return True
        
        db = get_supabase()
        result = db.table("processed_messages").select("id").eq("message_id", message_id).execute()
        
        is_processed = len(result.data) > 0
        if is_processed:
            cache.set(cache_key, True, 3600)  # Cache for 1 hour
        
        return is_processed

    @staticmethod
    async def mark_processed(message_id: str, wa_id: str, message_type: str = None):
        """Mark message as processed (async in background)."""
        cache_key = f"processed:{message_id}"
        cache.set(cache_key, True, 3600)
        
        # Insert to database in background
        asyncio.create_task(Database._insert_processed_message(message_id, wa_id, message_type))
    
    @staticmethod
    async def _insert_processed_message(message_id: str, wa_id: str, message_type: str):
        """Insert processed message to database."""
        try:
            db = get_supabase()
            db.table("processed_messages").insert({
                "message_id": message_id,
                "wa_id": wa_id,
                "message_type": message_type,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            logger.error(f"Failed to mark message as processed: {e}")

    @staticmethod
    async def create_order(wa_id: str, customer_phone: str, item_id: str, item_name: str, item_price: int = None) -> dict:
        """Create a new order."""
        db = get_supabase()
        
        # Get user_id from cache or database
        user = await Database.get_or_create_user(wa_id, customer_phone)
        user_id = user.get("id")
        
        order_data = {
            "user_id": user_id,
            "wa_id": wa_id,
            "customer_phone": customer_phone,
            "item_id": item_id,
            "item_name": item_name,
            "item_price": item_price,
            "status": "placed",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = db.table("orders").insert(order_data).execute()
        logger.info(f"Order created for {wa_id}: {item_name}")
        
        # Invalidate order history cache
        cache.delete(f"order_history:{wa_id}")
        
        return result.data[0] if result.data else order_data

    @staticmethod
    async def get_order_history(wa_id: str, limit: int = 10) -> list:
        """Get order history for a user (cached)."""
        cache_key = f"order_history:{wa_id}"
        cached_orders = cache.get(cache_key)
        
        if cached_orders:
            return cached_orders
        
        db = get_supabase()
        result = db.table("orders")\
            .select("order_number, item_name, status, created_at")\
            .eq("wa_id", wa_id)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        
        orders = result.data
        cache.set(cache_key, orders, 60)  # Cache for 1 minute
        return orders

    @staticmethod
    async def get_menu_items() -> list:
        """Get available menu items (heavily cached)."""
        cache_key = "menu_items:all"
        cached_menu = cache.get(cache_key)
        
        if cached_menu:
            return cached_menu
        
        db = get_supabase()
        result = db.table("menu_items")\
            .select("*")\
            .eq("is_available", True)\
            .order("sort_order")\
            .execute()
        
        menu_items = result.data
        cache.set(cache_key, menu_items, 600)  # Cache for 10 minutes
        return menu_items

    @staticmethod
    async def log_message(wa_id: str, direction: str, message_type: str, content: dict, status: str = "success", error: str = None):
        """Log message (only if enabled, async in background)."""
        if not config.ENABLE_MESSAGE_LOGGING:
            return
        
        # Log to database in background (non-blocking)
        asyncio.create_task(Database._insert_message_log(wa_id, direction, message_type, content, status, error))
    
    @staticmethod
    async def _insert_message_log(wa_id: str, direction: str, message_type: str, content: dict, status: str, error: str):
        """Insert message log to database."""
        try:
            db = get_supabase()
            db.table("message_logs").insert({
                "wa_id": wa_id,
                "direction": direction,
                "message_type": message_type,
                "content": content,
                "status": status,
                "error_message": error,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            logger.error(f"Failed to log message: {e}")


# ============================================================
# RATE LIMITING (OPTIMIZED WITH IN-MEMORY)
# ============================================================
class RateLimiter:
    """Optimized rate limiter using in-memory cache + Supabase backup."""
    
    _rate_limits: Dict[str, tuple[int, float]] = {}  # {wa_id: (count, window_start_timestamp)}
    
    @staticmethod
    async def check_rate_limit(wa_id: str) -> tuple[bool, int]:
        """
        Check if user is within rate limit (in-memory first for speed).
        Returns (is_allowed, remaining_requests)
        """
        now = datetime.now(timezone.utc)
        window_start = now.replace(second=0, microsecond=0)
        window_timestamp = window_start.timestamp()
        
        # Check in-memory first
        if wa_id in RateLimiter._rate_limits:
            count, stored_window = RateLimiter._rate_limits[wa_id]
            
            # Check if window expired
            if stored_window < window_timestamp:
                # New window
                RateLimiter._rate_limits[wa_id] = (1, window_timestamp)
                return True, config.RATE_LIMIT_REQUESTS - 1
            
            # Same window
            if count >= config.RATE_LIMIT_REQUESTS:
                return False, 0
            
            RateLimiter._rate_limits[wa_id] = (count + 1, window_timestamp)
            return True, config.RATE_LIMIT_REQUESTS - count - 1
        
        # First request in this window
        RateLimiter._rate_limits[wa_id] = (1, window_timestamp)
        
        # Sync to database in background (for persistence across restarts)
        asyncio.create_task(RateLimiter._sync_to_db(wa_id, window_start.isoformat(), 1))
        
        return True, config.RATE_LIMIT_REQUESTS - 1
    
    @staticmethod
    async def _sync_to_db(wa_id: str, window_start: str, count: int):
        """Sync rate limit to database in background."""
        try:
            db = get_supabase()
            # Upsert (update or insert)
            db.table("rate_limits").upsert({
                "wa_id": wa_id,
                "window_start": window_start,
                "request_count": count,
            }).execute()
        except Exception:
            pass  # Silently fail, in-memory is primary


# ============================================================
# WHATSAPP API HELPERS (OPTIMIZED)
# ============================================================
class WhatsAppAPI:
    """Optimized WhatsApp Cloud API wrapper with connection pooling."""
    
    BASE_URL = "https://graph.facebook.com/v21.0"
    
    @classmethod
    def _get_url(cls, path: str) -> str:
        return f"{cls.BASE_URL}/{config.WHATSAPP_PHONE_NUMBER_ID}/{path}"
    
    @classmethod
    async def send(cls, payload: dict) -> dict:
        """Send a message via WhatsApp API (reuses HTTP client)."""
        if not config.WHATSAPP_ACCESS_TOKEN:
            raise RuntimeError("WHATSAPP_ACCESS_TOKEN is not set")
        
        headers = {
            "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        
        client = get_http_client()
        response = await client.post(
            cls._get_url("messages"),
            headers=headers,
            json=payload
        )
        
        if response.status_code >= 400:
            logger.error(f"WhatsApp API error: {response.status_code} - {response.text}")
            response.raise_for_status()
        
        return response.json()
    
    @classmethod
    async def send_text(cls, to: str, text: str) -> dict:
        """Send a text message."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
        result = await cls.send(payload)
        
        # Log in background if enabled
        if config.ENABLE_MESSAGE_LOGGING:
            asyncio.create_task(Database.log_message(to, "outbound", "text", {"body": text}))
        
        return result
    
    @classmethod
    async def send_buttons(cls, to: str, body_text: str, buttons: list[dict]) -> dict:
        """Send interactive buttons (max 3)."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                        for b in buttons[:3]
                    ]
                },
            },
        }
        result = await cls.send(payload)
        
        # Log in background if enabled
        if config.ENABLE_MESSAGE_LOGGING:
            asyncio.create_task(Database.log_message(to, "outbound", "buttons", payload["interactive"]))
        
        return result
    
    @classmethod
    async def send_list(cls, to: str, body_text: str, button_text: str, sections: list[dict]) -> dict:
        """Send interactive list."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body_text},
                "action": {
                    "button": button_text,
                    "sections": sections,
                },
            },
        }
        result = await cls.send(payload)
        
        # Log in background if enabled
        if config.ENABLE_MESSAGE_LOGGING:
            asyncio.create_task(Database.log_message(to, "outbound", "list", payload["interactive"]))
        
        return result
    
    @staticmethod
    def verify_signature(payload: bytes, signature: str) -> bool:
        """Verify webhook signature from Meta."""
        if not config.WHATSAPP_APP_SECRET:
            logger.warning("WHATSAPP_APP_SECRET not set, skipping signature verification")
            return True
        
        expected_signature = hmac.new(
            config.WHATSAPP_APP_SECRET.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        if signature.startswith("sha256="):
            signature = signature[7:]
        
        return hmac.compare_digest(expected_signature, signature)


# ============================================================
# UI FLOWS
# ============================================================
class BotFlows:
    """Bot conversation flows."""
    
    @staticmethod
    async def show_home(to: str):
        """Show home menu."""
        await WhatsAppAPI.send_buttons(
            to,
            "Welcome!!👋 What would you like to do?",
            [
                {"id": BTN_MENU, "title": "📋 Menu"},
                {"id": BTN_ORDER, "title": "🛒 Order"},
                {"id": BTN_MORE, "title": "⚙️ More"},
            ],
        )
    
    @staticmethod
    async def show_menu(to: str):
        """Show menu with items."""
        items = await Database.get_menu_items()
        
        if items:
            lines = ["🧾 *Menu*\n"]
            for item in items:
                price_display = f"Rs {item['price'] // 100}"
                lines.append(f"• {item['name']} — {price_display}")
            lines.append("\nTap *Order* to place an order.")
            menu_text = "\n".join(lines)
        else:
            menu_text = (
                "🧾 *Menu*\n"
                "• Zinger Burger — Rs 450\n"
                "• Pizza Slice — Rs 350\n"
                "• Fries — Rs 200\n\n"
                "Tap *Order* to place an order."
            )
        
        await WhatsAppAPI.send_buttons(
            to,
            menu_text,
            [
                {"id": BTN_ORDER, "title": "🛒 Order"},
                {"id": BTN_BACK_HOME, "title": "🔙 Back"},
                {"id": BTN_MORE, "title": "⚙️ More"},
            ],
        )
    
    @staticmethod
    async def show_order_list(to: str):
        """Show order selection list."""
        items = await Database.get_menu_items()
        
        if items:
            rows = []
            for item in items:
                price_display = f"Rs {item['price'] // 100}"
                rows.append({
                    "id": item["item_id"],
                    "title": item["name"],
                    "description": price_display,
                })
        else:
            rows = [
                {"id": ITEM_ZINGER, "title": "Zinger Burger", "description": "Rs 450"},
                {"id": ITEM_PIZZA, "title": "Pizza Slice", "description": "Rs 350"},
                {"id": ITEM_FRIES, "title": "Fries", "description": "Rs 200"},
            ]
        
        sections = [{"title": "Available items", "rows": rows}]
        
        await WhatsAppAPI.send_list(
            to,
            "Select an item to place your order:",
            "View items",
            sections,
        )
    
    @staticmethod
    async def show_more(to: str):
        """Show more options."""
        await WhatsAppAPI.send_buttons(
            to,
            "More options:",
            [
                {"id": BTN_HISTORY, "title": "📦 Order history"},
                {"id": BTN_CONTACT, "title": "📞 Contact us"},
                {"id": BTN_BACK_HOME, "title": "🔙 Back"},
            ],
        )
    
    @staticmethod
    async def show_contact(to: str):
        """Show contact information."""
        await WhatsAppAPI.send_text(
            to,
            "📞 *Contact Us*\n"
            "Support: +92-XXX-XXXXXXX\n"
            "Email: support@yourbrand.com\n"
            "Hours: 10am–10pm"
        )
        await BotFlows.show_home(to)
    
    @staticmethod
    async def show_history(to: str, wa_id: str):
        """Show order history."""
        orders = await Database.get_order_history(wa_id, limit=10)
        
        if not orders:
            await WhatsAppAPI.send_text(
                to,
                "No orders yet. Tap *Order* to place your first order! 🛒"
            )
            await BotFlows.show_home(to)
            return
        
        lines = ["📦 *Your Recent Orders*\n"]
        for order in orders:
            order_num = order.get("order_number", "N/A")
            item_name = order.get("item_name", "Unknown")
            status = order.get("status", "unknown")
            created = order.get("created_at", "")[:10]
            
            status_emoji = {
                "placed": "🆕",
                "confirmed": "✅",
                "preparing": "👨‍🍳",
                "ready": "📦",
                "delivered": "✔️",
                "cancelled": "❌",
            }.get(status, "❓")
            
            lines.append(f"{status_emoji} #{order_num} {item_name} — {status}")
        
        await WhatsAppAPI.send_text(to, "\n".join(lines))
        await BotFlows.show_home(to)
    
    @staticmethod
    async def show_rate_limited(to: str):
        """Show rate limit message."""
        await WhatsAppAPI.send_text(
            to,
            "⏳ You're sending messages too quickly. Please wait a moment and try again."
        )


# ============================================================
# MESSAGE EXTRACTION
# ============================================================
def extract_message(data: dict) -> Optional[dict]:
    """Extract the first inbound message and metadata."""
    try:
        value = data["entry"][0]["changes"][0]["value"]
        messages = value.get("messages", [])
        
        if not messages:
            return None
        
        msg = messages[0]
        from_ = msg.get("from")
        msg_id = msg.get("id")
        msg_type = msg.get("type")
        
        result = {
            "from": from_,
            "id": msg_id,
            "type": msg_type,
        }
        
        if msg_type == "interactive":
            interactive = msg.get("interactive", {})
            itype = interactive.get("type")
            
            if itype == "button_reply":
                result["kind"] = "button"
                result["reply_id"] = interactive["button_reply"]["id"]
                result["title"] = interactive["button_reply"]["title"]
            elif itype == "list_reply":
                result["kind"] = "list"
                result["reply_id"] = interactive["list_reply"]["id"]
                result["title"] = interactive["list_reply"]["title"]
            else:
                result["kind"] = "interactive_other"
            
            return result
        
        if msg_type == "text":
            result["kind"] = "text"
            result["text"] = msg.get("text", {}).get("body", "")
            return result
        
        result["kind"] = "other"
        return result
    
    except Exception as e:
        logger.error(f"Error extracting message: {e}")
        return None


# ============================================================
# FASTAPI APP
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan events."""
    # Startup
    logger.info("Starting WhatsApp Bot (Performance Optimized)...")
    
    missing = config.validate()
    if missing:
        logger.warning(f"⚠️ Missing configuration: {', '.join(missing)}")
        logger.warning("Some features may not work until environment variables are set")
    else:
        logger.info("✅ Configuration validated successfully")
    
    # Test Supabase connection
    if is_supabase_configured():
        try:
            db = get_supabase()
            db.table("users").select("id").limit(1).execute()
            logger.info("✅ Supabase connection established")
        except Exception as e:
            logger.warning(f"⚠️ Supabase connection test failed: {e}")
    else:
        logger.warning("⚠️ Supabase not configured - database features disabled")
    
    logger.info(f"🚀 WhatsApp Bot started in {config.ENVIRONMENT} mode")
    logger.info(f"⚡ Performance optimizations: Caching enabled, Message logging: {config.ENABLE_MESSAGE_LOGGING}")
    
    yield
    
    # Shutdown
    logger.info("WhatsApp Bot shutting down")
    if _http_client:
        await _http_client.aclose()


app = FastAPI(
    title="WhatsApp Button Bot",
    description="Production-ready WhatsApp bot with Supabase (Performance Optimized)",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ENDPOINTS
# ============================================================
@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "WhatsApp Button Bot",
        "version": "2.1.0",
        "status": "running",
        "optimizations": "enabled"
    }


@app.get("/health")
async def health():
    """Health check endpoint for Railway."""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": config.ENVIRONMENT,
        "version": "2.1.0",
        "checks": {}
    }
    
    try:
        db = get_supabase()
        db.table("users").select("id").limit(1).execute()
        health_status["checks"]["database"] = "ok"
    except Exception as e:
        health_status["checks"]["database"] = f"error: {str(e)}"
        health_status["status"] = "degraded"
    
    if config.WHATSAPP_ACCESS_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID:
        health_status["checks"]["whatsapp_config"] = "ok"
    else:
        health_status["checks"]["whatsapp_config"] = "missing credentials"
        health_status["status"] = "degraded"
    
    return JSONResponse(health_status, status_code=200)


@app.get("/webhook/whatsapp")
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
):
    """Webhook verification for Meta."""
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN:
        if hub_challenge:
            logger.info("Webhook verified successfully")
            return PlainTextResponse(hub_challenge, status_code=200)
    
    logger.warning(f"Webhook verification failed: mode={hub_mode}, token={hub_verify_token}")
    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/webhook/whatsapp")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming WhatsApp messages (optimized)."""
    body = await request.body()
    
    # Verify signature (if configured)
    signature = request.headers.get("X-Hub-Signature-256", "")
    if config.WHATSAPP_APP_SECRET and not WhatsAppAPI.verify_signature(body, signature):
        logger.warning("Invalid webhook signature")
        return JSONResponse({"status": "invalid_signature"}, status_code=401)
    
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse({"status": "invalid_json"}, status_code=400)
    
    msg = extract_message(data)
    
    if not msg or not msg.get("id") or not msg.get("from"):
        return JSONResponse({"status": "ignored"}, status_code=200)
    
    to = msg["from"]
    wa_id = msg["from"]
    msg_id = msg["id"]
    
    # Deduplication check (fast in-memory)
    if await Database.already_processed(msg_id):
        logger.debug(f"Duplicate message ignored: {msg_id}")
        return JSONResponse({"status": "duplicate"}, status_code=200)
    
    # Mark as processed immediately (in-memory + background DB sync)
    await Database.mark_processed(msg_id, wa_id, msg.get("kind"))
    
    # Check if user is blocked (cached)
    if await Database.is_user_blocked(wa_id):
        logger.info(f"Blocked user attempted contact: {wa_id}")
        return JSONResponse({"status": "blocked"}, status_code=200)
    
    # Rate limiting (in-memory for speed)
    is_allowed, remaining = await RateLimiter.check_rate_limit(wa_id)
    if not is_allowed:
        logger.warning(f"Rate limit exceeded for {wa_id}")
        await BotFlows.show_rate_limited(to)
        return JSONResponse({"status": "rate_limited"}, status_code=200)
    
    # Get or create user (cached)
    await Database.get_or_create_user(wa_id, to)
    
    # Log message in background if enabled
    if config.ENABLE_MESSAGE_LOGGING:
        background_tasks.add_task(Database.log_message, wa_id, "inbound", msg.get("kind", "unknown"), msg)
    
    try:
        # Handle text messages
        if msg["kind"] == "text":
            text = (msg.get("text") or "").strip().lower()
            
            if text in ("hi", "hello", "start", "hey", "hola"):
                await BotFlows.show_home(to)
            elif text == "menu":
                await BotFlows.show_menu(to)
            elif text == "order":
                await BotFlows.show_order_list(to)
            elif text == "more":
                await BotFlows.show_more(to)
            elif text in ("history", "orders"):
                await BotFlows.show_history(to, wa_id)
            elif text in ("contact", "help", "support"):
                await BotFlows.show_contact(to)
            else:
                await BotFlows.show_home(to)
            
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Handle button clicks
        if msg["kind"] == "button":
            rid = msg["reply_id"]
            
            handlers = {
                BTN_MENU: lambda: BotFlows.show_menu(to),
                BTN_ORDER: lambda: BotFlows.show_order_list(to),
                BTN_MORE: lambda: BotFlows.show_more(to),
                BTN_HISTORY: lambda: BotFlows.show_history(to, wa_id),
                BTN_CONTACT: lambda: BotFlows.show_contact(to),
                BTN_BACK_HOME: lambda: BotFlows.show_home(to),
            }
            
            handler = handlers.get(rid)
            if handler:
                await handler()
            else:
                await BotFlows.show_home(to)
            
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Handle list selection (ordering)
        if msg["kind"] == "list":
            rid = msg["reply_id"]
            title = msg.get("title", "Item")
            
            # Get menu items (cached)
            items = await Database.get_menu_items()
            item_map = {item["item_id"]: item for item in items}
            
            if not item_map:
                item_map = {
                    ITEM_ZINGER: {"name": "Zinger Burger", "price": 45000, "item_id": ITEM_ZINGER},
                    ITEM_PIZZA: {"name": "Pizza Slice", "price": 35000, "item_id": ITEM_PIZZA},
                    ITEM_FRIES: {"name": "Fries", "price": 20000, "item_id": ITEM_FRIES},
                }
            
            if rid in item_map:
                item = item_map[rid]
                price_display = f"Rs {item['price'] // 100}"
                item_display = f"{item['name']} — {price_display}"
                
                await Database.create_order(
                    wa_id=wa_id,
                    customer_phone=to,
                    item_id=rid,
                    item_name=item_display,
                    item_price=item['price'],
                )
                
                await WhatsAppAPI.send_text(
                    to,
                    f"✅ Order placed: *{item_display}*\n\n"
                    "We'll notify you when it's ready!"
                )
                await BotFlows.show_home(to)
            else:
                await WhatsAppAPI.send_text(to, "❓ I didn't recognize that item. Please try again.")
                await BotFlows.show_order_list(to)
            
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Handle other message types
        await WhatsAppAPI.send_text(
            to,
            "I can only process text messages and button selections right now. "
            "Please use the menu options below! 👇"
        )
        await BotFlows.show_home(to)
        return JSONResponse({"status": "ok"}, status_code=200)
    
    except Exception as e:
        logger.exception(f"Error handling message from {wa_id}: {e}")
        if config.ENABLE_MESSAGE_LOGGING:
            background_tasks.add_task(Database.log_message, wa_id, "inbound", msg.get("kind"), msg, status="error", error=str(e))
        return JSONResponse({"status": "error"}, status_code=200)


# ============================================================
# ADMIN ENDPOINTS
# ============================================================
@app.get("/admin/stats")
async def get_stats():
    """Get basic statistics."""
    db = get_supabase()
    
    try:
        users_result = db.table("users").select("id", count="exact").execute()
        orders_result = db.table("orders").select("id", count="exact").execute()
        
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        orders_today = db.table("orders")\
            .select("id", count="exact")\
            .gte("created_at", today_start.isoformat())\
            .execute()
        
        return {
            "total_users": users_result.count or 0,
            "total_orders": orders_result.count or 0,
            "orders_today": orders_today.count or 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cache_enabled": True,
            "message_logging": config.ENABLE_MESSAGE_LOGGING,
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/cache/clear")
async def clear_cache():
    """Clear all cached data."""
    cache.clear()
    return {"status": "cache_cleared", "timestamp": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=config.DEBUG,
    )