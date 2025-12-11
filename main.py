"""
WhatsApp Button Bot - Performance Optimized with Billing & Payment
FastAPI + Supabase + Railway Deployment + Meta Catalogue Integration
v2.3.0 - Enhanced Billing & Payment Edition
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
    WHATSAPP_BUSINESS_ID: str = os.getenv("WHATSAPP_BUSINESS_ID", "")
    
    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    
    # Bank Details for Bank Transfer
    BANK_NAME: str = os.getenv("BANK_NAME", "HBL Bank")
    BANK_ACCOUNT_NUMBER: str = os.getenv("BANK_ACCOUNT_NUMBER", "1234567890123456")
    BANK_ACCOUNT_TITLE: str = os.getenv("BANK_ACCOUNT_TITLE", "CPC Store")
    BANK_IBAN: str = os.getenv("BANK_IBAN", "PK12HABB0000001234567890")
    
    # Performance Settings
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "300"))
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
        
        if not missing:
            logger.info("✅ All credentials validated (values masked)")
        
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
            logger.error(f"Failed to create Supabase client: {type(e).__name__}")
            raise RuntimeError("Database connection failed") from e
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
BTN_VIEW_STORE = "BTN_VIEW_STORE"
BTN_HISTORY = "BTN_HISTORY"
BTN_FAQ = "BTN_FAQ"
BTN_ABOUT_US = "BTN_ABOUT_US"
BTN_BACK_HOME = "BTN_BACK_HOME"
BTN_CHECKOUT = "BTN_CHECKOUT"
BTN_PAY_BANK = "BTN_PAY_BANK"
BTN_PAY_CARD = "BTN_PAY_CARD"
BTN_CONFIRM_PAYMENT = "BTN_CONFIRM_PAYMENT"

# Legacy buttons
BTN_MENU = "BTN_MENU"
BTN_ORDER = "BTN_ORDER"
BTN_MORE = "BTN_MORE"
BTN_CONTACT = "BTN_CONTACT"

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
            asyncio.create_task(Database._update_user_activity(wa_id))
            return cached_user
        
        db = get_supabase()
        
        result = db.table("users").select("*").eq("wa_id", wa_id).execute()
        
        if result.data:
            user = result.data[0]
            cache.set(cache_key, user, 600)
            asyncio.create_task(Database._update_user_activity(wa_id))
            return user
        
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
            logger.error(f"Failed to update user activity: {type(e).__name__}")

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
        
        cache.set(cache_key, is_blocked, 300)
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
            cache.set(cache_key, True, 3600)
        
        return is_processed

    @staticmethod
    async def mark_processed(message_id: str, wa_id: str, message_type: str = None):
        """Mark message as processed (async in background)."""
        cache_key = f"processed:{message_id}"
        cache.set(cache_key, True, 3600)
        
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
            logger.error(f"Failed to mark message as processed: {type(e).__name__}")

    @staticmethod
    async def create_order_from_catalogue(wa_id: str, customer_phone: str, order_data: dict) -> dict:
        """Create order from Meta catalogue purchase with proper billing."""
        db = get_supabase()
        
        user = await Database.get_or_create_user(wa_id, customer_phone)
        user_id = user.get("id")
        
        # Extract and calculate order details properly
        items = order_data.get("product_items", [])
        
        # Calculate totals correctly
        subtotal = 0
        processed_items = []
        
        for item in items:
            item_price = int(item.get("item_price", 0))  # Price in paisa
            quantity = int(item.get("quantity", 1))
            item_total = item_price * quantity
            subtotal += item_total
            
            processed_items.append({
                "product_retailer_id": item.get("product_retailer_id"),
                "name": item.get("name", "Unknown Item"),
                "quantity": quantity,
                "item_price": item_price,
                "currency": item.get("currency", "INR"),
                "item_total": item_total
            })
        
        # Calculate tax and total (if applicable)
        tax_rate = 0.0  # Adjust if you have tax
        tax_amount = int(subtotal * tax_rate)
        total_amount = subtotal + tax_amount
        
        item_display = f"{len(items)} item(s) from catalogue" if items else "Catalogue order"
        
        order_record = {
            "user_id": user_id,
            "wa_id": wa_id,
            "customer_phone": customer_phone,
            "item_id": "CAT_ORDER",
            "item_name": item_display,
            "items": processed_items,
            "subtotal": subtotal,
            "tax_amount": tax_amount,
            "total_amount": total_amount,
            "quantity": len(items) if items else 1,
            "status": "pending_payment",
            "order_source": "meta_catalogue",
            "meta_order_id": order_data.get("order_id"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        result = db.table("orders").insert(order_record).execute()
        
        order = result.data[0] if result.data else order_record
        order_number = order.get("order_number", "N/A")
        
        logger.info(f"Catalogue order created: {order_number} for {wa_id}")
        
        cache.delete(f"order_history:{wa_id}")
        cache.set(f"pending_order:{wa_id}", order, 1800)  # Cache for 30 minutes
        
        return order

    @staticmethod
    async def get_pending_order(wa_id: str) -> Optional[dict]:
        """Get pending order for payment."""
        cache_key = f"pending_order:{wa_id}"
        cached_order = cache.get(cache_key)
        
        if cached_order:
            return cached_order
        
        db = get_supabase()
        result = db.table("orders")\
            .select("*")\
            .eq("wa_id", wa_id)\
            .eq("status", "pending_payment")\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()
        
        if result.data:
            order = result.data[0]
            cache.set(cache_key, order, 1800)
            return order
        
        return None

    @staticmethod
    async def update_order_payment(order_id: str, payment_method: str, payment_status: str = "pending") -> dict:
        """Update order with payment information."""
        db = get_supabase()
        
        update_data = {
            "payment_method": payment_method,
            "payment_status": payment_status,
            "status": "placed" if payment_status == "confirmed" else "pending_payment",
            "payment_confirmed_at": datetime.now(timezone.utc).isoformat() if payment_status == "confirmed" else None,
        }
        
        result = db.table("orders").update(update_data).eq("id", order_id).execute()
        
        if result.data:
            order = result.data[0]
            wa_id = order.get("wa_id")
            cache.delete(f"pending_order:{wa_id}")
            cache.delete(f"order_history:{wa_id}")
            
        return result.data[0] if result.data else {}

    @staticmethod
    async def create_order(wa_id: str, customer_phone: str, item_id: str, item_name: str, item_price: int = None) -> dict:
        """Create a new order (legacy method)."""
        db = get_supabase()
        
        user = await Database.get_or_create_user(wa_id, customer_phone)
        user_id = user.get("id")
        
        subtotal = item_price or 0
        tax_amount = 0
        total_amount = subtotal + tax_amount
        
        order_data = {
            "user_id": user_id,
            "wa_id": wa_id,
            "customer_phone": customer_phone,
            "item_id": item_id,
            "item_name": item_name,
            "item_price": item_price,
            "subtotal": subtotal,
            "tax_amount": tax_amount,
            "total_amount": total_amount,
            "quantity": 1,
            "status": "pending_payment",
            "order_source": "bot_menu",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = db.table("orders").insert(order_data).execute()
        logger.info(f"Order created for {wa_id}: {item_name}")
        
        order = result.data[0] if result.data else order_data
        cache.delete(f"order_history:{wa_id}")
        cache.set(f"pending_order:{wa_id}", order, 1800)
        
        return order

    @staticmethod
    async def get_order_history(wa_id: str, limit: int = 10) -> list:
        """Get order history for a user (cached)."""
        cache_key = f"order_history:{wa_id}"
        cached_orders = cache.get(cache_key)
        
        if cached_orders:
            return cached_orders
        
        db = get_supabase()
        result = db.table("orders")\
            .select("order_number, item_name, items, total_amount, status, payment_method, payment_status, created_at")\
            .eq("wa_id", wa_id)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        
        orders = result.data
        cache.set(cache_key, orders, 60)
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
        cache.set(cache_key, menu_items, 600)
        return menu_items

    @staticmethod
    async def log_message(wa_id: str, direction: str, message_type: str, content: dict, status: str = "success", error: str = None):
        """Log message (only if enabled, async in background)."""
        if not config.ENABLE_MESSAGE_LOGGING:
            return
        
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
            logger.error(f"Failed to log message: {type(e).__name__}")


# ============================================================
# RATE LIMITING (OPTIMIZED WITH IN-MEMORY)
# ============================================================
class RateLimiter:
    """Optimized rate limiter using in-memory cache + Supabase backup."""
    
    _rate_limits: Dict[str, tuple[int, float]] = {}
    _last_cleanup = datetime.now()
    
    @staticmethod
    async def check_rate_limit(wa_id: str) -> tuple[bool, int]:
        """Check if user is within rate limit."""
        await RateLimiter._cleanup_old_entries()
        
        now = datetime.now(timezone.utc)
        window_start = now.replace(second=0, microsecond=0)
        window_timestamp = window_start.timestamp()
        
        if wa_id in RateLimiter._rate_limits:
            count, stored_window = RateLimiter._rate_limits[wa_id]
            
            if stored_window < window_timestamp:
                RateLimiter._rate_limits[wa_id] = (1, window_timestamp)
                return True, config.RATE_LIMIT_REQUESTS - 1
            
            if count >= config.RATE_LIMIT_REQUESTS:
                return False, 0
            
            RateLimiter._rate_limits[wa_id] = (count + 1, window_timestamp)
            return True, config.RATE_LIMIT_REQUESTS - count - 1
        
        RateLimiter._rate_limits[wa_id] = (1, window_timestamp)
        asyncio.create_task(RateLimiter._sync_to_db(wa_id, window_start.isoformat(), 1))
        
        return True, config.RATE_LIMIT_REQUESTS - 1
    
    @staticmethod
    async def _cleanup_old_entries():
        """Remove expired entries every hour."""
        now = datetime.now()
        if (now - RateLimiter._last_cleanup).seconds > 3600:
            current_window = now.replace(second=0, microsecond=0).timestamp()
            RateLimiter._rate_limits = {
                k: v for k, v in RateLimiter._rate_limits.items() 
                if v[1] >= current_window - 3600
            }
            RateLimiter._last_cleanup = now
            logger.info(f"Rate limiter cleanup completed. Active entries: {len(RateLimiter._rate_limits)}")
    
    @staticmethod
    async def _sync_to_db(wa_id: str, window_start: str, count: int):
        """Sync rate limit to database in background."""
        try:
            db = get_supabase()
            db.table("rate_limits").upsert({
                "wa_id": wa_id,
                "window_start": window_start,
                "request_count": count,
            }).execute()
        except Exception:
            pass


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
        
        if config.ENABLE_MESSAGE_LOGGING:
            asyncio.create_task(Database.log_message(to, "outbound", "list", payload["interactive"]))
        
        return result
    
    @classmethod
    async def send_catalogue_message(cls, to: str, body_text: str, catalogue_id: str = None) -> dict:
        """Send product catalogue message."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "catalog_message",
                "body": {
                    "text": body_text
                },
                "action": {
                    "name": "catalog_message",
                }
            }
        }
        
        result = await cls.send(payload)
        
        if config.ENABLE_MESSAGE_LOGGING:
            asyncio.create_task(Database.log_message(to, "outbound", "catalogue", payload["interactive"]))
        
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
# BILLING HELPER
# ============================================================
class BillingHelper:
    """Helper class for generating bills and receipts."""
    
    @staticmethod
    def format_currency(amount_paisa: int) -> str:
        """Format amount in paisa to rupees string."""
        rupees = amount_paisa / 100
        return f"Rs {rupees:,.2f}"
    
    @staticmethod
    def generate_bill(order: dict) -> str:
        """Generate a formatted bill for an order."""
        order_number = order.get("order_number", "N/A")
        items = order.get("items", [])
        subtotal = order.get("subtotal", 0)
        tax_amount = order.get("tax_amount", 0)
        total_amount = order.get("total_amount", 0)
        
        bill_lines = [
            "╔════════════════════════════════╗",
            "║        📋 ORDER BILL           ║",
            "╚════════════════════════════════╝",
            "",
            f"Order #: {order_number}",
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "─" * 34,
            ""
        ]
        
        # Handle both catalogue orders and legacy orders
        if items and isinstance(items, list) and len(items) > 0:
            # Catalogue order with multiple items
            bill_lines.append("Items:")
            for item in items:
                name = item.get("name", "Unknown Item")
                qty = item.get("quantity", 1)
                price = item.get("item_price", 0)
                item_total = item.get("item_total", price * qty)
                
                bill_lines.append(f"  • {name}")
                bill_lines.append(f"    {qty} x {BillingHelper.format_currency(price)} = {BillingHelper.format_currency(item_total)}")
        else:
            # Legacy order with single item
            item_name = order.get("item_name", "Unknown Item")
            quantity = order.get("quantity", 1)
            item_price = order.get("item_price", 0)
            item_total = item_price * quantity
            
            bill_lines.append("Items:")
            bill_lines.append(f"  • {item_name}")
            bill_lines.append(f"    {quantity} x {BillingHelper.format_currency(item_price)} = {BillingHelper.format_currency(item_total)}")
        
        bill_lines.extend([
            "",
            "─" * 34,
            f"Subtotal:        {BillingHelper.format_currency(subtotal)}",
        ])
        
        if tax_amount > 0:
            bill_lines.append(f"Tax:             {BillingHelper.format_currency(tax_amount)}")
        
        bill_lines.extend([
            "─" * 34,
            f"*Total:          {BillingHelper.format_currency(total_amount)}*",
            "─" * 34,
            "",
            "Thank you for shopping with CPC! 🛍️"
        ])
        
        return "\n".join(bill_lines)
    
    @staticmethod
    def generate_payment_receipt(order: dict, payment_method: str) -> str:
        """Generate payment receipt after successful payment."""
        order_number = order.get("order_number", "N/A")
        total_amount = order.get("total_amount", 0)
        
        receipt_lines = [
            "╔════════════════════════════════╗",
            "║      ✅ PAYMENT CONFIRMED      ║",
            "╚════════════════════════════════╝",
            "",
            f"Order #: {order_number}",
            f"Amount Paid: {BillingHelper.format_currency(total_amount)}",
            f"Payment Method: {payment_method}",
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "─" * 34,
            "",
            "Your order has been confirmed! 🎉",
            "We'll start preparing it right away.",
            "",
            "Track your order status by visiting",
            "📦 Order History",
            "",
            "Thank you for your business! 🙏"
        ]
        
        return "\n".join(receipt_lines)


# ============================================================
# UI FLOWS
# ============================================================
class BotFlows:
    """Bot conversation flows."""
    
    @staticmethod
    async def show_home(to: str):
        """Show home menu with new structure."""
        await WhatsAppAPI.send_buttons(
            to,
            "Welcome to CPC! 🛍️\n\nWhat would you like to do?",
            [
                {"id": BTN_VIEW_STORE, "title": "🛍️ View Store"},
                {"id": BTN_HISTORY, "title": "📦 Order History"},
                {"id": BTN_FAQ, "title": "❓ FAQ"},
            ],
        )
    
    @staticmethod
    async def show_store(to: str):
        """Show Meta product catalogue."""
        await WhatsAppAPI.send_catalogue_message(
            to,
            "🛍️ *Browse Our Store*\n\nCheck out our complete product catalogue below. Tap on any item to view details and add to cart!"
        )
        
        await asyncio.sleep(1)
        await WhatsAppAPI.send_buttons(
            to,
            "After adding items to cart, proceed to checkout:",
            [
                {"id": BTN_CHECKOUT, "title": "🛒 Checkout"},
                {"id": BTN_BACK_HOME, "title": "🏠 Main Menu"},
            ],
        )
    
    @staticmethod
    async def show_checkout(to: str, wa_id: str):
        """Show checkout with bill and payment options."""
        # Get pending order
        order = await Database.get_pending_order(wa_id)
        
        if not order:
            await WhatsAppAPI.send_text(
                to,
                "❌ No pending order found.\n\nPlease add items to your cart first by browsing our store!"
            )
            await BotFlows.show_store(to)
            return
        
        # Generate and send bill
        bill = BillingHelper.generate_bill(order)
        await WhatsAppAPI.send_text(to, bill)
        
        # Show payment options
        await asyncio.sleep(1)
        await WhatsAppAPI.send_buttons(
            to,
            "💳 *Select Payment Method*\n\nHow would you like to pay?",
            [
                {"id": BTN_PAY_BANK, "title": "🏦 Bank Transfer"},
                {"id": BTN_PAY_CARD, "title": "💳 Card Payment"},
                {"id": BTN_BACK_HOME, "title": "🔙 Cancel"},
            ],
        )
    
    @staticmethod
    async def show_bank_transfer_details(to: str, wa_id: str):
        """Show bank transfer details and instructions."""
        order = await Database.get_pending_order(wa_id)
        
        if not order:
            await WhatsAppAPI.send_text(to, "❌ Order not found. Please try again.")
            await BotFlows.show_home(to)
            return
        
        total_amount = order.get("total_amount", 0)
        order_number = order.get("order_number", "N/A")
        
        bank_details = (
            "🏦 *Bank Transfer Details*\n"
            "─" * 34 + "\n\n"
            f"*Bank Name:* {config.BANK_NAME}\n"
            f"*Account Title:* {config.BANK_ACCOUNT_TITLE}\n"
            f"*Account Number:* {config.BANK_ACCOUNT_NUMBER}\n"
            f"*IBAN:* {config.BANK_IBAN}\n\n"
            f"*Amount to Transfer:* {BillingHelper.format_currency(total_amount)}\n"
            f"*Order Reference:* #{order_number}\n\n"
            "─" * 34 + "\n\n"
            "📝 *Instructions:*\n"
            "1. Transfer the exact amount to the account above\n"
            "2. Use Order Reference #{} in the description\n"
            "3. After transfer, click 'Confirm Payment' below\n"
            "4. Send us the payment screenshot for verification\n\n"
            "⚠️ *Important:*\n"
            "• Include order reference in transfer description\n"
            "• Keep your transaction receipt\n"
            "• Payment verification may take 5-10 minutes"
        ).format(order_number)
        
        await WhatsAppAPI.send_text(to, bank_details)
        
        # Update order with payment method
        await Database.update_order_payment(order["id"], "bank_transfer", "pending")
        
        await asyncio.sleep(1)
        await WhatsAppAPI.send_buttons(
            to,
            "Have you completed the transfer?",
            [
                {"id": BTN_CONFIRM_PAYMENT, "title": "✅ Confirm Payment"},
                {"id": BTN_BACK_HOME, "title": "🔙 Cancel"},
            ],
        )
    
    @staticmethod
    async def show_card_payment(to: str):
        """Show card payment message (coming soon)."""
        await WhatsAppAPI.send_text(
            to,
            "💳 *Card Payment*\n\n"
            "⚠️ Card payment integration is coming soon!\n\n"
            "For now, please use Bank Transfer as your payment method.\n\n"
            "We're working hard to bring you secure card payment options soon. "
            "Thank you for your patience! 🙏"
        )
        
        await asyncio.sleep(1)
        await WhatsAppAPI.send_buttons(
            to,
            "Choose another payment method:",
            [
                {"id": BTN_PAY_BANK, "title": "🏦 Bank Transfer"},
                {"id": BTN_BACK_HOME, "title": "🔙 Back to Menu"},
            ],
        )
    
    @staticmethod
    async def confirm_payment(to: str, wa_id: str):
        """Confirm payment and complete order."""
        order = await Database.get_pending_order(wa_id)
        
        if not order:
            await WhatsAppAPI.send_text(to, "❌ Order not found. Please try again.")
            await BotFlows.show_home(to)
            return
        
        # Update order status to confirmed
        await Database.update_order_payment(order["id"], order.get("payment_method", "bank_transfer"), "confirmed")
        
        # Generate receipt
        receipt = BillingHelper.generate_payment_receipt(order, "Bank Transfer")
        await WhatsAppAPI.send_text(to, receipt)
        
        await BotFlows.show_home(to)
    
    @staticmethod
    async def show_history(to: str, wa_id: str):
        """Show order history."""
        orders = await Database.get_order_history(wa_id, limit=10)
        
        if not orders:
            await WhatsAppAPI.send_text(
                to,
                "📦 *Order History*\n\nYou haven't placed any orders yet.\n\nTap *View Store* to browse our products! 🛍️"
            )
            await BotFlows.show_home(to)
            return
        
        lines = ["📦 *Your Recent Orders*\n"]
        for order in orders:
            order_num = order.get("order_number", "N/A")
            status = order.get("status", "unknown")
            payment_status = order.get("payment_status", "N/A")
            created = order.get("created_at", "")[:10]
            
            if order.get("items"):
                items = order.get("items", [])
                item_names = ", ".join([item.get("name", "Item") for item in items[:2]])
                if len(items) > 2:
                    item_names += f" (+{len(items)-2} more)"
                total = order.get("total_amount", 0)
                item_display = f"{item_names} - {BillingHelper.format_currency(total)}"
            else:
                item_display = order.get("item_name", "Unknown")
                total = order.get("total_amount", 0)
                item_display += f" - {BillingHelper.format_currency(total)}"
            
            status_emoji = {
                "pending_payment": "⏳",
                "placed": "🆕",
                "confirmed": "✅",
                "preparing": "👨‍🍳",
                "ready": "📦",
                "delivered": "✔️",
                "cancelled": "❌",
            }.get(status, "❓")
            
            payment_emoji = {
                "pending": "⏳",
                "confirmed": "✅",
                "failed": "❌",
            }.get(payment_status, "❓")
            
            lines.append(f"{status_emoji} #{order_num}\n   {item_display}\n   {created} • {status.replace('_', ' ').title()} {payment_emoji}\n")
        
        await WhatsAppAPI.send_text(to, "\n".join(lines))
        await BotFlows.show_home(to)
    
    @staticmethod
    async def show_faq(to: str):
        """Show FAQ menu."""
        await WhatsAppAPI.send_buttons(
            to,
            "❓ *Frequently Asked Questions*\n\nHow can we help you?",
            [
                {"id": BTN_ABOUT_US, "title": "ℹ️ About Us"},
                {"id": BTN_CONTACT, "title": "📞 Contact"},
                {"id": BTN_BACK_HOME, "title": "🔙 Back"},
            ],
        )
    
    @staticmethod
    async def show_about_us(to: str):
        """Show about us information."""
        await WhatsAppAPI.send_text(
            to,
            "ℹ️ *About CPC*\n\n"
            "Welcome to CPC - Your trusted partner for quality products and exceptional service.\n\n"
            "🏢 *Our Mission*\n"
            "To provide customers with the best shopping experience through our curated product selection and seamless ordering process.\n\n"
            "⭐ *Why Choose Us?*\n"
            "• Quality products\n"
            "• Fast delivery\n"
            "• 24/7 customer support\n"
            "• Secure payment options\n"
            "• Easy returns & refunds\n\n"
            "Thank you for choosing CPC! 🙏"
        )
        await BotFlows.show_faq(to)
    
    @staticmethod
    async def show_contact(to: str):
        """Show contact information."""
        await WhatsAppAPI.send_text(
            to,
            "📞 *Contact Us*\n\n"
            "We're here to help!\n\n"
            "📱 WhatsApp: This number\n"
            "📧 Email: support@cpc.com\n"
            "🌐 Website: www.cpc.com\n"
            "⏰ Hours: 9 AM - 9 PM (Mon-Sat)\n\n"
            "For payment queries, order tracking, or any other assistance,\n"
            "feel free to reach out anytime!"
        )
        await BotFlows.show_faq(to)
    
    @staticmethod
    async def show_rate_limited(to: str):
        """Show rate limit message."""
        await WhatsAppAPI.send_text(
            to,
            "⏳ You're sending messages too quickly. Please wait a moment and try again."
        )
    
    # Legacy flows
    @staticmethod
    async def show_menu(to: str):
        """Show menu with items (legacy)."""
        items = await Database.get_menu_items()
        
        if items:
            lines = ["🧾 *Menu*\n"]
            for item in items:
                price_display = BillingHelper.format_currency(item['price'])
                lines.append(f"• {item['name']} — {price_display}")
            lines.append("\nTap *Order* to place an order.")
            menu_text = "\n".join(lines)
        else:
            menu_text = (
                "🧾 *Menu*\n"
                "• Zinger Burger — Rs 450.00\n"
                "• Pizza Slice — Rs 350.00\n"
                "• Fries — Rs 200.00\n\n"
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
        """Show order selection list (legacy)."""
        items = await Database.get_menu_items()
        
        if items:
            rows = []
            for item in items:
                price_display = BillingHelper.format_currency(item['price'])
                rows.append({
                    "id": item["item_id"],
                    "title": item["name"],
                    "description": price_display,
                })
        else:
            rows = [
                {"id": ITEM_ZINGER, "title": "Zinger Burger", "description": "Rs 450.00"},
                {"id": ITEM_PIZZA, "title": "Pizza Slice", "description": "Rs 350.00"},
                {"id": ITEM_FRIES, "title": "Fries", "description": "Rs 200.00"},
            ]
        
        sections = [{"title": "Available items", "rows": rows}]
        
        await WhatsAppAPI.send_list(
            to,
            "Select an item to add to cart:",
            "View items",
            sections,
        )
    
    @staticmethod
    async def show_more(to: str):
        """Show more options (legacy)."""
        await WhatsAppAPI.send_buttons(
            to,
            "More options:",
            [
                {"id": BTN_HISTORY, "title": "📦 Order history"},
                {"id": BTN_CONTACT, "title": "📞 Contact us"},
                {"id": BTN_BACK_HOME, "title": "🔙 Back"},
            ],
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
            orders = value.get("orders", [])
            if orders:
                return {
                    "kind": "order",
                    "from": orders[0].get("wa_id"),
                    "id": f"order_{orders[0].get('catalog_id')}_{datetime.now().timestamp()}",
                    "order_data": orders[0]
                }
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
        
        if msg_type == "order":
            result["kind"] = "order"
            result["order_data"] = msg.get("order", {})
            return result
        
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
            elif itype == "nfm_reply":
                result["kind"] = "order"
                result["order_data"] = interactive.get("nfm_reply", {})
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
    logger.info("Starting WhatsApp Bot (Billing & Payment Edition)...")
    
    missing = config.validate()
    if missing:
        logger.warning(f"⚠️ Missing configuration: {', '.join(missing)}")
    
    if is_supabase_configured():
        try:
            db = get_supabase()
            db.table("users").select("id").limit(1).execute()
            logger.info("✅ Supabase connection established")
        except Exception as e:
            logger.warning(f"⚠️ Supabase connection test failed: {type(e).__name__}")
    
    logger.info(f"🚀 WhatsApp Bot started in {config.ENVIRONMENT} mode")
    logger.info(f"⚡ Features: Catalogue, Billing, Payments (Bank Transfer, Card Coming Soon)")
    
    yield
    
    logger.info("WhatsApp Bot shutting down")
    if _http_client:
        await _http_client.aclose()


app = FastAPI(
    title="WhatsApp Button Bot with Billing & Payment",
    description="Production-ready WhatsApp bot with Meta Catalogue + Billing + Payment Integration",
    version="2.3.0",
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
        "name": "WhatsApp Button Bot with Billing & Payment",
        "version": "2.3.0",
        "status": "running",
        "features": ["meta_catalogue", "billing_system", "payment_options", "order_history"]
    }


@app.get("/health")
async def health():
    """Health check endpoint for Railway."""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": config.ENVIRONMENT,
        "version": "2.3.0",
        "checks": {}
    }
    
    try:
        db = get_supabase()
        db.table("users").select("id").limit(1).execute()
        health_status["checks"]["database"] = "ok"
    except Exception as e:
        health_status["checks"]["database"] = f"error: {type(e).__name__}"
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
    """Handle incoming WhatsApp messages with billing and payment."""
    start_time = datetime.now()
    
    body = await request.body()
    
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
    
    if await Database.already_processed(msg_id):
        logger.debug(f"Duplicate message ignored: {msg_id}")
        return JSONResponse({"status": "duplicate"}, status_code=200)
    
    await Database.mark_processed(msg_id, wa_id, msg.get("kind"))
    
    if await Database.is_user_blocked(wa_id):
        logger.info(f"Blocked user attempted contact: {wa_id}")
        return JSONResponse({"status": "blocked"}, status_code=200)
    
    is_allowed, remaining = await RateLimiter.check_rate_limit(wa_id)
    if not is_allowed:
        logger.warning(f"Rate limit exceeded for {wa_id}")
        await BotFlows.show_rate_limited(to)
        return JSONResponse({"status": "rate_limited"}, status_code=200)
    
    await Database.get_or_create_user(wa_id, to)
    
    if config.ENABLE_MESSAGE_LOGGING:
        background_tasks.add_task(Database.log_message, wa_id, "inbound", msg.get("kind", "unknown"), msg)
    
    try:
        # Handle catalogue orders
        if msg["kind"] == "order":
            order_data = msg.get("order_data", {})
            
            order = await Database.create_order_from_catalogue(
                wa_id=wa_id,
                customer_phone=to,
                order_data=order_data
            )
            
            # Send bill and ask for checkout
            bill = BillingHelper.generate_bill(order)
            await WhatsAppAPI.send_text(to, bill)
            
            await asyncio.sleep(1)
            await WhatsAppAPI.send_buttons(
                to,
                "Your cart is ready! Proceed to checkout to complete your order:",
                [
                    {"id": BTN_CHECKOUT, "title": "🛒 Checkout"},
                    {"id": BTN_BACK_HOME, "title": "🏠 Main Menu"},
                ],
            )
            
            processing_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"Order webhook processed in {processing_time:.3f}s")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Handle text messages
        if msg["kind"] == "text":
            text = (msg.get("text") or "").strip().lower()
            
            text_handlers = {
                ("hi", "hello", "start", "hey", "hola", "menu", "home"): lambda: BotFlows.show_home(to),
                ("store", "shop", "catalogue", "catalog"): lambda: BotFlows.show_store(to),
                ("checkout", "cart", "pay"): lambda: BotFlows.show_checkout(to, wa_id),
                ("history", "orders", "my orders"): lambda: BotFlows.show_history(to, wa_id),
                ("faq", "help", "info"): lambda: BotFlows.show_faq(to),
                ("about", "about us"): lambda: BotFlows.show_about_us(to),
                ("contact", "support"): lambda: BotFlows.show_contact(to),
            }
            
            for keywords, handler in text_handlers.items():
                if text in keywords:
                    await handler()
                    break
            else:
                await BotFlows.show_home(to)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"Text webhook processed in {processing_time:.3f}s")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Handle button clicks
        if msg["kind"] == "button":
            rid = msg["reply_id"]
            
            handlers = {
                BTN_VIEW_STORE: lambda: BotFlows.show_store(to),
                BTN_CHECKOUT: lambda: BotFlows.show_checkout(to, wa_id),
                BTN_HISTORY: lambda: BotFlows.show_history(to, wa_id),
                BTN_FAQ: lambda: BotFlows.show_faq(to),
                BTN_ABOUT_US: lambda: BotFlows.show_about_us(to),
                BTN_CONTACT: lambda: BotFlows.show_contact(to),
                BTN_BACK_HOME: lambda: BotFlows.show_home(to),
                BTN_PAY_BANK: lambda: BotFlows.show_bank_transfer_details(to, wa_id),
                BTN_PAY_CARD: lambda: BotFlows.show_card_payment(to),
                BTN_CONFIRM_PAYMENT: lambda: BotFlows.confirm_payment(to, wa_id),
                # Legacy
                BTN_MENU: lambda: BotFlows.show_menu(to),
                BTN_ORDER: lambda: BotFlows.show_order_list(to),
                BTN_MORE: lambda: BotFlows.show_more(to),
            }
            
            handler = handlers.get(rid)
            if handler:
                await handler()
            else:
                await BotFlows.show_home(to)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"Button webhook processed in {processing_time:.3f}s")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Handle list selection (legacy ordering)
        if msg["kind"] == "list":
            rid = msg["reply_id"]
            title = msg.get("title", "Item")
            
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
                
                order = await Database.create_order(
                    wa_id=wa_id,
                    customer_phone=to,
                    item_id=rid,
                    item_name=item['name'],
                    item_price=item['price'],
                )
                
                # Send bill and checkout option
                bill = BillingHelper.generate_bill(order)
                await WhatsAppAPI.send_text(to, bill)
                
                await asyncio.sleep(1)
                await WhatsAppAPI.send_buttons(
                    to,
                    "Proceed to checkout?",
                    [
                        {"id": BTN_CHECKOUT, "title": "🛒 Checkout"},
                        {"id": BTN_BACK_HOME, "title": "🏠 Main Menu"},
                    ],
                )
            else:
                await WhatsAppAPI.send_text(to, "❓ I didn't recognize that item. Please try again.")
                await BotFlows.show_order_list(to)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"List webhook processed in {processing_time:.3f}s")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Handle other message types
        await WhatsAppAPI.send_text(
            to,
            "I can only process text messages and button selections right now. "
            "Please use the menu options below! 👇"
        )
        await BotFlows.show_home(to)
        
        processing_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"Other webhook processed in {processing_time:.3f}s")
        return JSONResponse({"status": "ok"}, status_code=200)
    
    except Exception as e:
        logger.exception(f"Error handling message from {wa_id}: {e}")
        if config.ENABLE_MESSAGE_LOGGING:
            background_tasks.add_task(Database.log_message, wa_id, "inbound", msg.get("kind"), msg, status="error", error=str(e))
        
        processing_time = (datetime.now() - start_time).total_seconds()
        logger.error(f"Error webhook processed in {processing_time:.3f}s")
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
        
        catalogue_orders = db.table("orders")\
            .select("id", count="exact")\
            .eq("order_source", "meta_catalogue")\
            .execute()
        
        pending_payments = db.table("orders")\
            .select("id", count="exact")\
            .eq("status", "pending_payment")\
            .execute()
        
        confirmed_payments = db.table("orders")\
            .select("id", count="exact")\
            .eq("payment_status", "confirmed")\
            .execute()
        
        return {
            "total_users": users_result.count or 0,
            "total_orders": orders_result.count or 0,
            "orders_today": orders_today.count or 0,
            "catalogue_orders": catalogue_orders.count or 0,
            "bot_menu_orders": (orders_result.count or 0) - (catalogue_orders.count or 0),
            "pending_payments": pending_payments.count or 0,
            "confirmed_payments": confirmed_payments.count or 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "features": ["billing", "bank_transfer", "card_coming_soon"],
        }
    except Exception as e:
        logger.error(f"Error getting stats: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Failed to fetch statistics")


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
