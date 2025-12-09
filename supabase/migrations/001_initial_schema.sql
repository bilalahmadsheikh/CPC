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
-- CPC WhatsApp Bot - Meta Catalogue Integration Migration
-- FOR UUID-BASED SCHEMA
-- Version: 2.2.0 - CORRECT VERSION
-- Safe to run on your existing database
-- ============================================================

-- Add new columns to orders table for catalogue support
-- ============================================================
-- CPC WhatsApp Bot - Meta Catalogue Integration Migration
-- FOR UUID-BASED SCHEMA - FIXED VERSION
-- Version: 2.2.0 - Fixed view replacement
-- Safe to run on your existing database
-- ============================================================

-- Add new columns to orders table for catalogue support
ALTER TABLE orders 
ADD COLUMN IF NOT EXISTS items JSONB,
ADD COLUMN IF NOT EXISTS total_amount INTEGER,
ADD COLUMN IF NOT EXISTS order_source TEXT DEFAULT 'bot_menu',
ADD COLUMN IF NOT EXISTS meta_order_id TEXT;

-- Add helpful comments
COMMENT ON COLUMN orders.items IS 'Array of ordered items from Meta catalogue (JSONB format)';
COMMENT ON COLUMN orders.total_amount IS 'Total order amount in paisa (e.g., 45000 = Rs 450)';
COMMENT ON COLUMN orders.order_source IS 'Order source: meta_catalogue or bot_menu';
COMMENT ON COLUMN orders.meta_order_id IS 'Order ID from Meta if order came from catalogue';

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_orders_order_source ON orders(order_source);
CREATE INDEX IF NOT EXISTS idx_orders_meta_order_id ON orders(meta_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_source_status ON orders(order_source, status);
CREATE INDEX IF NOT EXISTS idx_orders_source_created ON orders(order_source, created_at DESC);

-- ============================================================
-- Enhanced Analytics Views
-- ============================================================

-- Order statistics by source
DROP VIEW IF EXISTS order_source_stats CASCADE;
CREATE VIEW order_source_stats AS
SELECT 
    COALESCE(order_source, 'bot_menu') as order_source,
    COUNT(*) as total_orders,
    COUNT(CASE WHEN status = 'delivered' THEN 1 END) as delivered_orders,
    COUNT(CASE WHEN status = 'cancelled' THEN 1 END) as cancelled_orders,
    COUNT(CASE WHEN status = 'placed' THEN 1 END) as pending_orders,
    SUM(COALESCE(total_amount, item_price * quantity)) / 100 as total_revenue_rs,
    AVG(COALESCE(total_amount, item_price * quantity)) / 100 as avg_order_value_rs
FROM orders
GROUP BY order_source;

-- Daily orders with source tracking
DROP VIEW IF EXISTS daily_orders CASCADE;
CREATE VIEW daily_orders AS
SELECT 
    DATE_TRUNC('day', created_at) as date,
    COALESCE(order_source, 'bot_menu') as order_source,
    status,
    COUNT(*) as count,
    SUM(COALESCE(total_amount, item_price * quantity)) / 100 as total_revenue_rs,
    AVG(COALESCE(total_amount, item_price * quantity)) / 100 as avg_order_value_rs
FROM orders
GROUP BY DATE_TRUNC('day', created_at), order_source, status
ORDER BY date DESC, order_source;

-- Top products from catalogue orders
DROP VIEW IF EXISTS top_catalogue_products CASCADE;
CREATE VIEW top_catalogue_products AS
SELECT 
    item->>'product_id' as product_id,
    item->>'name' as product_name,
    COUNT(*) as times_ordered,
    SUM((item->>'quantity')::integer) as total_quantity,
    SUM((item->>'unit_price')::integer) / 100 as total_revenue_rs
FROM orders,
LATERAL jsonb_array_elements(items) as item
WHERE order_source = 'meta_catalogue'
  AND items IS NOT NULL
GROUP BY item->>'product_id', item->>'name'
ORDER BY times_ordered DESC;

-- Customer lifetime value with source breakdown
DROP VIEW IF EXISTS customer_lifetime_value CASCADE;
CREATE VIEW customer_lifetime_value AS
SELECT 
    wa_id,
    customer_phone,
    COUNT(*) as total_orders,
    COUNT(CASE WHEN COALESCE(order_source, 'bot_menu') = 'meta_catalogue' THEN 1 END) as catalogue_orders,
    COUNT(CASE WHEN COALESCE(order_source, 'bot_menu') = 'bot_menu' THEN 1 END) as bot_menu_orders,
    SUM(COALESCE(total_amount, item_price * quantity)) / 100 as lifetime_value_rs,
    MAX(created_at) as last_order_date,
    MIN(created_at) as first_order_date
FROM orders
GROUP BY wa_id, customer_phone
ORDER BY lifetime_value_rs DESC;

-- Update existing v_recent_orders view - DROP and recreate
DROP VIEW IF EXISTS v_recent_orders CASCADE;
CREATE VIEW v_recent_orders AS
SELECT 
    o.order_number,
    o.wa_id,
    u.name as customer_name,
    COALESCE(o.order_source, 'bot_menu') as order_source,
    CASE 
        WHEN o.items IS NOT NULL THEN 
            jsonb_array_length(o.items) || ' items from catalogue'
        ELSE 
            o.item_name
    END as order_description,
    COALESCE(o.total_amount, o.item_price * o.quantity) / 100 as total_rs,
    o.status,
    o.created_at
FROM orders o
LEFT JOIN users u ON o.user_id = u.id
ORDER BY o.created_at DESC
LIMIT 100;

-- ============================================================
-- Helper Functions
-- ============================================================

-- Function to get formatted order details
DROP FUNCTION IF EXISTS get_order_display(UUID);
CREATE FUNCTION get_order_display(order_uuid UUID)
RETURNS TABLE (
    order_number INTEGER,
    customer_info TEXT,
    items_display TEXT,
    total_display TEXT,
    status TEXT,
    source TEXT,
    created_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        o.order_number,
        COALESCE(o.customer_phone, o.wa_id) as customer_info,
        CASE 
            WHEN o.items IS NOT NULL THEN 
                (SELECT STRING_AGG(
                    (item->>'name') || ' x' || (item->>'quantity') || 
                    ' (Rs ' || ((item->>'unit_price')::integer / 100)::TEXT || ')',
                    ', '
                ) FROM jsonb_array_elements(o.items) as item)
            ELSE 
                o.item_name || ' x' || o.quantity
        END as items_display,
        'Rs ' || (COALESCE(o.total_amount, o.item_price * o.quantity) / 100)::TEXT as total_display,
        o.status,
        COALESCE(o.order_source, 'bot_menu') as source,
        o.created_at
    FROM orders o
    WHERE o.id = order_uuid;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Cleanup Functions
-- ============================================================

-- Cleanup old rate limits
DROP FUNCTION IF EXISTS cleanup_old_rate_limits();
CREATE FUNCTION cleanup_old_rate_limits()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM rate_limits 
    WHERE window_start < NOW() - INTERVAL '2 hours';
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Row Level Security Policies
-- ============================================================

-- Ensure RLS policies exist for service role access
-- (Your existing policies should cover this, but adding for safety)
DO $$
BEGIN
    -- Check if policies exist, if not create them
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'orders' 
        AND policyname = 'Service role full access on orders'
    ) THEN
        CREATE POLICY "Service role full access on orders" 
        ON orders FOR ALL 
        USING (true) 
        WITH CHECK (true);
    END IF;
END $$;

-- ============================================================
-- Verify Migration Success
-- ============================================================
DO $$
DECLARE
    column_count INTEGER;
    view_count INTEGER;
BEGIN
    -- Check if all new columns were added
    SELECT COUNT(*) INTO column_count
    FROM information_schema.columns 
    WHERE table_name = 'orders' 
      AND column_name IN ('items', 'total_amount', 'order_source', 'meta_order_id');
    
    -- Check if views were created
    SELECT COUNT(*) INTO view_count
    FROM information_schema.views 
    WHERE table_name IN (
        'order_source_stats', 
        'top_catalogue_products', 
        'customer_lifetime_value',
        'v_recent_orders',
        'daily_orders'
    );
    
    IF column_count = 4 AND view_count = 5 THEN
        RAISE NOTICE '✅ Meta Catalogue integration migration completed successfully!';
        RAISE NOTICE '📊 New columns added: items, total_amount, order_source, meta_order_id';
        RAISE NOTICE '🔍 New views created: order_source_stats, top_catalogue_products, customer_lifetime_value, daily_orders';
        RAISE NOTICE '📈 Updated view: v_recent_orders';
        RAISE NOTICE '🎯 Your database is ready for Meta catalogue orders!';
        RAISE NOTICE '';
        RAISE NOTICE '✨ Test queries you can run:';
        RAISE NOTICE '   SELECT * FROM order_source_stats;';
        RAISE NOTICE '   SELECT * FROM v_recent_orders LIMIT 5;';
    ELSE
        RAISE WARNING '⚠ Migration partially completed. Columns: %/4, Views: %/5', column_count, view_count;
        RAISE WARNING 'Please check the logs above for any errors.';
    END IF;
END $$;

-- ============================================================
-- Sample Queries (for immediate testing)
-- ============================================================

-- Quick verification query
SELECT 
    'Migration Status' as check_type,
    'Columns Added' as detail,
    (
        SELECT COUNT(*)::text 
        FROM information_schema.columns 
        WHERE table_name = 'orders' 
        AND column_name IN ('items', 'total_amount', 'order_source', 'meta_order_id')
    ) || '/4' as result
UNION ALL
SELECT 
    'Migration Status' as check_type,
    'Views Created' as detail,
    (
        SELECT COUNT(*)::text 
        FROM information_schema.views 
        WHERE table_name IN (
            'order_source_stats', 
            'top_catalogue_products', 
            'customer_lifetime_value',
            'v_recent_orders',
            'daily_orders'
        )
    ) || '/5' as result;

-- ============================================================
-- MIGRATION COMPLETE
-- Your UUID-based schema is now ready for Meta Catalogue!
-- ============================================================

-- ============================================================
-- CPC WhatsApp Bot - Meta Catalogue Integration Migration
-- FOR UUID-BASED SCHEMA - FIXED VERSION
-- Version: 2.2.0 - Fixed view replacement
-- Safe to run on your existing database
-- ============================================================

-- Add new columns to orders table for catalogue support
ALTER TABLE orders 
ADD COLUMN IF NOT EXISTS items JSONB,
ADD COLUMN IF NOT EXISTS total_amount INTEGER,
ADD COLUMN IF NOT EXISTS order_source TEXT DEFAULT 'bot_menu',
ADD COLUMN IF NOT EXISTS meta_order_id TEXT;

-- Add helpful comments
COMMENT ON COLUMN orders.items IS 'Array of ordered items from Meta catalogue (JSONB format)';
COMMENT ON COLUMN orders.total_amount IS 'Total order amount in paisa (e.g., 45000 = Rs 450)';
COMMENT ON COLUMN orders.order_source IS 'Order source: meta_catalogue or bot_menu';
COMMENT ON COLUMN orders.meta_order_id IS 'Order ID from Meta if order came from catalogue';

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_orders_order_source ON orders(order_source);
CREATE INDEX IF NOT EXISTS idx_orders_meta_order_id ON orders(meta_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_source_status ON orders(order_source, status);
CREATE INDEX IF NOT EXISTS idx_orders_source_created ON orders(order_source, created_at DESC);

-- ============================================================
-- Enhanced Analytics Views
-- ============================================================

-- Order statistics by source
DROP VIEW IF EXISTS order_source_stats CASCADE;
CREATE VIEW order_source_stats AS
SELECT 
    COALESCE(order_source, 'bot_menu') as order_source,
    COUNT(*) as total_orders,
    COUNT(CASE WHEN status = 'delivered' THEN 1 END) as delivered_orders,
    COUNT(CASE WHEN status = 'cancelled' THEN 1 END) as cancelled_orders,
    COUNT(CASE WHEN status = 'placed' THEN 1 END) as pending_orders,
    SUM(COALESCE(total_amount, item_price * quantity)) / 100 as total_revenue_rs,
    AVG(COALESCE(total_amount, item_price * quantity)) / 100 as avg_order_value_rs
FROM orders
GROUP BY order_source;

-- Daily orders with source tracking
DROP VIEW IF EXISTS daily_orders CASCADE;
CREATE VIEW daily_orders AS
SELECT 
    DATE_TRUNC('day', created_at) as date,
    COALESCE(order_source, 'bot_menu') as order_source,
    status,
    COUNT(*) as count,
    SUM(COALESCE(total_amount, item_price * quantity)) / 100 as total_revenue_rs,
    AVG(COALESCE(total_amount, item_price * quantity)) / 100 as avg_order_value_rs
FROM orders
GROUP BY DATE_TRUNC('day', created_at), order_source, status
ORDER BY date DESC, order_source;

-- Top products from catalogue orders
DROP VIEW IF EXISTS top_catalogue_products CASCADE;
CREATE VIEW top_catalogue_products AS
SELECT 
    item->>'product_id' as product_id,
    item->>'name' as product_name,
    COUNT(*) as times_ordered,
    SUM((item->>'quantity')::integer) as total_quantity,
    SUM((item->>'unit_price')::integer) / 100 as total_revenue_rs
FROM orders,
LATERAL jsonb_array_elements(items) as item
WHERE order_source = 'meta_catalogue'
  AND items IS NOT NULL
GROUP BY item->>'product_id', item->>'name'
ORDER BY times_ordered DESC;

-- Customer lifetime value with source breakdown
DROP VIEW IF EXISTS customer_lifetime_value CASCADE;
CREATE VIEW customer_lifetime_value AS
SELECT 
    wa_id,
    customer_phone,
    COUNT(*) as total_orders,
    COUNT(CASE WHEN COALESCE(order_source, 'bot_menu') = 'meta_catalogue' THEN 1 END) as catalogue_orders,
    COUNT(CASE WHEN COALESCE(order_source, 'bot_menu') = 'bot_menu' THEN 1 END) as bot_menu_orders,
    SUM(COALESCE(total_amount, item_price * quantity)) / 100 as lifetime_value_rs,
    MAX(created_at) as last_order_date,
    MIN(created_at) as first_order_date
FROM orders
GROUP BY wa_id, customer_phone
ORDER BY lifetime_value_rs DESC;

-- Update existing v_recent_orders view - DROP and recreate
DROP VIEW IF EXISTS v_recent_orders CASCADE;
CREATE VIEW v_recent_orders AS
SELECT 
    o.order_number,
    o.wa_id,
    u.name as customer_name,
    COALESCE(o.order_source, 'bot_menu') as order_source,
    CASE 
        WHEN o.items IS NOT NULL THEN 
            jsonb_array_length(o.items) || ' items from catalogue'
        ELSE 
            o.item_name
    END as order_description,
    COALESCE(o.total_amount, o.item_price * o.quantity) / 100 as total_rs,
    o.status,
    o.created_at
FROM orders o
LEFT JOIN users u ON o.user_id = u.id
ORDER BY o.created_at DESC
LIMIT 100;

-- ============================================================
-- Helper Functions
-- ============================================================

-- Function to get formatted order details
DROP FUNCTION IF EXISTS get_order_display(UUID);
CREATE FUNCTION get_order_display(order_uuid UUID)
RETURNS TABLE (
    order_number INTEGER,
    customer_info TEXT,
    items_display TEXT,
    total_display TEXT,
    status TEXT,
    source TEXT,
    created_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        o.order_number,
        COALESCE(o.customer_phone, o.wa_id) as customer_info,
        CASE 
            WHEN o.items IS NOT NULL THEN 
                (SELECT STRING_AGG(
                    (item->>'name') || ' x' || (item->>'quantity') || 
                    ' (Rs ' || ((item->>'unit_price')::integer / 100)::TEXT || ')',
                    ', '
                ) FROM jsonb_array_elements(o.items) as item)
            ELSE 
                o.item_name || ' x' || o.quantity
        END as items_display,
        'Rs ' || (COALESCE(o.total_amount, o.item_price * o.quantity) / 100)::TEXT as total_display,
        o.status,
        COALESCE(o.order_source, 'bot_menu') as source,
        o.created_at
    FROM orders o
    WHERE o.id = order_uuid;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Cleanup Functions
-- ============================================================

-- Cleanup old rate limits
DROP FUNCTION IF EXISTS cleanup_old_rate_limits();
CREATE FUNCTION cleanup_old_rate_limits()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM rate_limits 
    WHERE window_start < NOW() - INTERVAL '2 hours';
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Row Level Security Policies
-- ============================================================

-- Ensure RLS policies exist for service role access
-- (Your existing policies should cover this, but adding for safety)
DO $$
BEGIN
    -- Check if policies exist, if not create them
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'orders' 
        AND policyname = 'Service role full access on orders'
    ) THEN
        CREATE POLICY "Service role full access on orders" 
        ON orders FOR ALL 
        USING (true) 
        WITH CHECK (true);
    END IF;
END $$;

-- ============================================================
-- Verify Migration Success
-- ============================================================
DO $$
DECLARE
    column_count INTEGER;
    view_count INTEGER;
BEGIN
    -- Check if all new columns were added
    SELECT COUNT(*) INTO column_count
    FROM information_schema.columns 
    WHERE table_name = 'orders' 
      AND column_name IN ('items', 'total_amount', 'order_source', 'meta_order_id');
    
    -- Check if views were created
    SELECT COUNT(*) INTO view_count
    FROM information_schema.views 
    WHERE table_name IN (
        'order_source_stats', 
        'top_catalogue_products', 
        'customer_lifetime_value',
        'v_recent_orders',
        'daily_orders'
    );
    
    IF column_count = 4 AND view_count = 5 THEN
        RAISE NOTICE '✅ Meta Catalogue integration migration completed successfully!';
        RAISE NOTICE '📊 New columns added: items, total_amount, order_source, meta_order_id';
        RAISE NOTICE '🔍 New views created: order_source_stats, top_catalogue_products, customer_lifetime_value, daily_orders';
        RAISE NOTICE '📈 Updated view: v_recent_orders';
        RAISE NOTICE '🎯 Your database is ready for Meta catalogue orders!';
        RAISE NOTICE '';
        RAISE NOTICE '✨ Test queries you can run:';
        RAISE NOTICE '   SELECT * FROM order_source_stats;';
        RAISE NOTICE '   SELECT * FROM v_recent_orders LIMIT 5;';
    ELSE
        RAISE WARNING '⚠ Migration partially completed. Columns: %/4, Views: %/5', column_count, view_count;
        RAISE WARNING 'Please check the logs above for any errors.';
    END IF;
END $$;

-- ============================================================
-- Sample Queries (for immediate testing)
-- ============================================================

-- Quick verification query
SELECT 
    'Migration Status' as check_type,
    'Columns Added' as detail,
    (
        SELECT COUNT(*)::text 
        FROM information_schema.columns 
        WHERE table_name = 'orders' 
        AND column_name IN ('items', 'total_amount', 'order_source', 'meta_order_id')
    ) || '/4' as result
UNION ALL
SELECT 
    'Migration Status' as check_type,
    'Views Created' as detail,
    (
        SELECT COUNT(*)::text 
        FROM information_schema.views 
        WHERE table_name IN (
            'order_source_stats', 
            'top_catalogue_products', 
            'customer_lifetime_value',
            'v_recent_orders',
            'daily_orders'
        )
    ) || '/5' as result;

-- ============================================================
-- MIGRATION COMPLETE
-- Your UUID-based schema is now ready for Meta Catalogue!
-- ============================================================