-- ============================================================
-- WhatsApp Bot Database Schema for Supabase
-- Run this in: Supabase Dashboard → SQL Editor → New Query
-- ============================================================

-- Enable UUID extension (usually already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. USERS TABLE - Track customer information
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wa_id TEXT UNIQUE NOT NULL,                    -- WhatsApp ID (phone number)
    phone TEXT,                                     -- Formatted phone number
    name TEXT,                                      -- Customer name (if provided)
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_orders INTEGER NOT NULL DEFAULT 0,
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',                   -- Flexible field for extra data
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast lookups by WhatsApp ID
CREATE INDEX IF NOT EXISTS idx_users_wa_id ON users(wa_id);
CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active_at DESC);

-- ============================================================
-- 2. PROCESSED MESSAGES TABLE - Deduplication
-- ============================================================
CREATE TABLE IF NOT EXISTS processed_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id TEXT UNIQUE NOT NULL,               -- WhatsApp message ID
    wa_id TEXT NOT NULL,                           -- Sender's WhatsApp ID
    message_type TEXT,                             -- text, button, list, etc.
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast dedup checks
CREATE INDEX IF NOT EXISTS idx_processed_messages_message_id ON processed_messages(message_id);
-- Index for cleanup of old messages
CREATE INDEX IF NOT EXISTS idx_processed_messages_processed_at ON processed_messages(processed_at);

-- ============================================================
-- 3. ORDERS TABLE - Order tracking
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_number SERIAL,                           -- Human-readable order number
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    wa_id TEXT NOT NULL,                           -- WhatsApp ID (denormalized for quick access)
    customer_phone TEXT,
    item_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    item_price INTEGER,                            -- Price in smallest currency unit (paisa)
    quantity INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'placed',         -- placed, confirmed, preparing, ready, delivered, cancelled
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for orders
CREATE INDEX IF NOT EXISTS idx_orders_wa_id ON orders(wa_id);
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at DESC);

-- ============================================================
-- 4. RATE LIMITS TABLE - Track API usage per user
-- ============================================================
CREATE TABLE IF NOT EXISTS rate_limits (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wa_id TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(wa_id, window_start)
);

-- Index for rate limit checks
CREATE INDEX IF NOT EXISTS idx_rate_limits_wa_id_window ON rate_limits(wa_id, window_start);

-- ============================================================
-- 5. MESSAGE LOGS TABLE - For debugging and analytics
-- ============================================================
CREATE TABLE IF NOT EXISTS message_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wa_id TEXT NOT NULL,
    direction TEXT NOT NULL,                       -- 'inbound' or 'outbound'
    message_type TEXT,                             -- text, button, list, interactive
    content JSONB,                                 -- Full message content
    status TEXT DEFAULT 'success',                 -- success, error
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for logs
CREATE INDEX IF NOT EXISTS idx_message_logs_wa_id ON message_logs(wa_id);
CREATE INDEX IF NOT EXISTS idx_message_logs_created_at ON message_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_message_logs_direction ON message_logs(direction);

-- ============================================================
-- 6. MENU ITEMS TABLE - Dynamic menu (optional enhancement)
-- ============================================================
CREATE TABLE IF NOT EXISTS menu_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id TEXT UNIQUE NOT NULL,                  -- ITEM_ZINGER, etc.
    name TEXT NOT NULL,
    description TEXT,
    price INTEGER NOT NULL,                        -- Price in smallest unit
    currency TEXT NOT NULL DEFAULT 'PKR',
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    category TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Insert default menu items
INSERT INTO menu_items (item_id, name, description, price, category, sort_order) VALUES
    ('ITEM_ZINGER', 'Zinger Burger', 'Crispy chicken zinger burger', 45000, 'Burgers', 1),
    ('ITEM_PIZZA', 'Pizza Slice', 'Cheesy pizza slice', 35000, 'Pizza', 2),
    ('ITEM_FRIES', 'Fries', 'Crispy golden fries', 20000, 'Sides', 3)
ON CONFLICT (item_id) DO NOTHING;

-- ============================================================
-- 7. FUNCTIONS - Auto-update timestamps
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers for auto-updating updated_at
DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_orders_updated_at ON orders;
CREATE TRIGGER update_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_menu_items_updated_at ON menu_items;
CREATE TRIGGER update_menu_items_updated_at
    BEFORE UPDATE ON menu_items
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- 8. FUNCTION - Increment user order count
-- ============================================================
CREATE OR REPLACE FUNCTION increment_user_orders()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE users 
    SET total_orders = total_orders + 1,
        last_active_at = NOW()
    WHERE wa_id = NEW.wa_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS increment_orders_on_insert ON orders;
CREATE TRIGGER increment_orders_on_insert
    AFTER INSERT ON orders
    FOR EACH ROW
    EXECUTE FUNCTION increment_user_orders();

-- ============================================================
-- 9. FUNCTION - Clean up old processed messages (run periodically)
-- ============================================================
CREATE OR REPLACE FUNCTION cleanup_old_processed_messages(days_to_keep INTEGER DEFAULT 7)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM processed_messages
    WHERE processed_at < NOW() - (days_to_keep || ' days')::INTERVAL;
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 10. ROW LEVEL SECURITY (RLS) - Optional but recommended
-- ============================================================
-- Enable RLS on tables (service_role key bypasses these)
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE processed_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE message_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE menu_items ENABLE ROW LEVEL SECURITY;

-- Allow service role full access (your backend)
-- These policies allow the service_role to do everything
CREATE POLICY "Service role full access on users" ON users FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on orders" ON orders FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on processed_messages" ON processed_messages FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on rate_limits" ON rate_limits FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on message_logs" ON message_logs FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on menu_items" ON menu_items FOR ALL USING (true) WITH CHECK (true);

-- ============================================================
-- 11. VIEWS - Useful queries
-- ============================================================
CREATE OR REPLACE VIEW v_recent_orders AS
SELECT 
    o.order_number,
    o.wa_id,
    u.name as customer_name,
    o.item_name,
    o.item_price,
    o.quantity,
    o.status,
    o.created_at
FROM orders o
LEFT JOIN users u ON o.user_id = u.id
ORDER BY o.created_at DESC
LIMIT 100;

CREATE OR REPLACE VIEW v_user_stats AS
SELECT 
    wa_id,
    name,
    phone,
    total_orders,
    first_seen_at,
    last_active_at,
    EXTRACT(DAY FROM NOW() - first_seen_at) as days_since_first_order
FROM users
ORDER BY total_orders DESC;

-- ============================================================
-- DONE! Your database is ready.
-- ============================================================
