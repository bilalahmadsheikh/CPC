-- CPC MVP Database Schema for Supabase
-- Run this in the Supabase SQL Editor to set up all required tables

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- BUSINESSES TABLE (for onboarding submissions)
-- ============================================================
CREATE TABLE IF NOT EXISTS businesses (
    id BIGSERIAL PRIMARY KEY,
    business_name VARCHAR(255) NOT NULL,
    business_type VARCHAR(100) NOT NULL,
    owner_name VARCHAR(255) NOT NULL,
    whatsapp VARCHAR(50) NOT NULL,
    email VARCHAR(255) NOT NULL,
    automations TEXT[] NOT NULL DEFAULT '{}',
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    CONSTRAINT valid_status CHECK (status IN ('pending', 'contacted', 'onboarded', 'active'))
);

CREATE INDEX IF NOT EXISTS idx_businesses_email ON businesses(email);
CREATE INDEX IF NOT EXISTS idx_businesses_whatsapp ON businesses(whatsapp);
CREATE INDEX IF NOT EXISTS idx_businesses_status ON businesses(status);
CREATE INDEX IF NOT EXISTS idx_businesses_created_at ON businesses(created_at DESC);

-- ============================================================
-- USERS TABLE (WhatsApp users interacting with bot)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    wa_id VARCHAR(50) NOT NULL UNIQUE,
    phone VARCHAR(50),
    name VARCHAR(255),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_users_wa_id ON users(wa_id);
CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active_at DESC);

-- ============================================================
-- ORDERS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    order_number VARCHAR(50) UNIQUE DEFAULT 'ORD-' || LPAD(FLOOR(RANDOM() * 100000)::TEXT, 5, '0'),
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    wa_id VARCHAR(50) NOT NULL,
    customer_phone VARCHAR(50) NOT NULL,
    item_id VARCHAR(100) NOT NULL,
    item_name VARCHAR(255) NOT NULL,
    item_price INTEGER, -- Price in smallest currency unit (e.g., paisa)
    quantity INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(50) NOT NULL DEFAULT 'placed',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    CONSTRAINT valid_order_status CHECK (status IN ('placed', 'confirmed', 'preparing', 'ready', 'delivered', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_orders_wa_id ON orders(wa_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at DESC);

-- ============================================================
-- MENU ITEMS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS menu_items (
    id BIGSERIAL PRIMARY KEY,
    item_id VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price INTEGER NOT NULL, -- Price in smallest currency unit
    category VARCHAR(100),
    image_url TEXT,
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_menu_items_available ON menu_items(is_available);
CREATE INDEX IF NOT EXISTS idx_menu_items_category ON menu_items(category);

-- Insert sample menu items
INSERT INTO menu_items (item_id, name, description, price, category, sort_order) VALUES
    ('ITEM_ZINGER', 'Zinger Burger', 'Crispy chicken burger with special sauce', 45000, 'Burgers', 1),
    ('ITEM_PIZZA', 'Pizza Slice', 'Fresh baked pizza slice with cheese', 35000, 'Pizza', 2),
    ('ITEM_FRIES', 'Fries', 'Golden crispy french fries', 20000, 'Sides', 3)
ON CONFLICT (item_id) DO NOTHING;

-- ============================================================
-- LEADS TABLE (captured from WhatsApp interactions)
-- ============================================================
CREATE TABLE IF NOT EXISTS leads (
    id BIGSERIAL PRIMARY KEY,
    wa_id VARCHAR(50) NOT NULL,
    phone VARCHAR(50) NOT NULL,
    name VARCHAR(255),
    source VARCHAR(100) NOT NULL DEFAULT 'whatsapp',
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_interaction TIMESTAMPTZ,
    status VARCHAR(50) NOT NULL DEFAULT 'new',
    notes TEXT,
    metadata JSONB DEFAULT '{}',
    CONSTRAINT valid_lead_status CHECK (status IN ('new', 'contacted', 'qualified', 'converted', 'lost'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_wa_id ON leads(wa_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_captured_at ON leads(captured_at DESC);

-- ============================================================
-- MESSAGE LOGS TABLE (for debugging and analytics)
-- ============================================================
CREATE TABLE IF NOT EXISTS message_logs (
    id BIGSERIAL PRIMARY KEY,
    wa_id VARCHAR(50) NOT NULL,
    direction VARCHAR(20) NOT NULL, -- 'inbound' or 'outbound'
    message_type VARCHAR(50) NOT NULL,
    content JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(50) NOT NULL DEFAULT 'success',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_message_logs_wa_id ON message_logs(wa_id);
CREATE INDEX IF NOT EXISTS idx_message_logs_direction ON message_logs(direction);
CREATE INDEX IF NOT EXISTS idx_message_logs_created_at ON message_logs(created_at DESC);

-- Partition by date for better performance (optional for high-volume)
-- Consider adding partitioning if message volume is very high

-- ============================================================
-- PROCESSED MESSAGES TABLE (for deduplication)
-- ============================================================
CREATE TABLE IF NOT EXISTS processed_messages (
    id BIGSERIAL PRIMARY KEY,
    message_id VARCHAR(255) NOT NULL UNIQUE,
    wa_id VARCHAR(50) NOT NULL,
    message_type VARCHAR(50),
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_processed_messages_message_id ON processed_messages(message_id);

-- Clean up old processed messages (older than 7 days) - run periodically
-- DELETE FROM processed_messages WHERE processed_at < NOW() - INTERVAL '7 days';

-- ============================================================
-- RATE LIMITS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS rate_limits (
    id BIGSERIAL PRIMARY KEY,
    wa_id VARCHAR(50) NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(wa_id, window_start)
);

CREATE INDEX IF NOT EXISTS idx_rate_limits_wa_id ON rate_limits(wa_id);
CREATE INDEX IF NOT EXISTS idx_rate_limits_window ON rate_limits(window_start);

-- Clean up old rate limit records (older than 1 hour) - run periodically
-- DELETE FROM rate_limits WHERE window_start < NOW() - INTERVAL '1 hour';

-- ============================================================
-- ROW LEVEL SECURITY (Optional but recommended for production)
-- ============================================================
-- Enable RLS on sensitive tables
ALTER TABLE businesses ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;

-- Create policies for service role (full access)
CREATE POLICY "Service role has full access to businesses" ON businesses
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Service role has full access to users" ON users
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Service role has full access to orders" ON orders
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Service role has full access to leads" ON leads
    FOR ALL USING (true) WITH CHECK (true);

-- ============================================================
-- FUNCTIONS (for auto-generating order numbers)
-- ============================================================
CREATE OR REPLACE FUNCTION generate_order_number()
RETURNS TRIGGER AS $$
BEGIN
    NEW.order_number := 'ORD-' || LPAD(nextval('orders_id_seq')::TEXT, 6, '0');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for order number generation
DROP TRIGGER IF EXISTS set_order_number ON orders;
CREATE TRIGGER set_order_number
    BEFORE INSERT ON orders
    FOR EACH ROW
    WHEN (NEW.order_number IS NULL)
    EXECUTE FUNCTION generate_order_number();

-- ============================================================
-- VIEWS (for analytics)
-- ============================================================
CREATE OR REPLACE VIEW business_stats AS
SELECT 
    business_type,
    status,
    COUNT(*) as count,
    DATE_TRUNC('day', created_at) as date
FROM businesses
GROUP BY business_type, status, DATE_TRUNC('day', created_at);

CREATE OR REPLACE VIEW daily_orders AS
SELECT 
    DATE_TRUNC('day', created_at) as date,
    status,
    COUNT(*) as count,
    SUM(item_price) as total_revenue
FROM orders
GROUP BY DATE_TRUNC('day', created_at), status;

CREATE OR REPLACE VIEW lead_funnel AS
SELECT 
    source,
    status,
    COUNT(*) as count,
    DATE_TRUNC('day', captured_at) as date
FROM leads
GROUP BY source, status, DATE_TRUNC('day', captured_at);

-- ============================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================
-- Add any additional composite indexes based on query patterns

-- Example: For querying recent orders by status
CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders(status, created_at DESC);

-- Example: For querying businesses by type and status
CREATE INDEX IF NOT EXISTS idx_businesses_type_status ON businesses(business_type, status);

COMMENT ON TABLE businesses IS 'Stores business registration submissions from the landing page';
COMMENT ON TABLE users IS 'WhatsApp users who have interacted with the bot';
COMMENT ON TABLE orders IS 'Orders placed through the WhatsApp bot';
COMMENT ON TABLE menu_items IS 'Product catalog items available for ordering';
COMMENT ON TABLE leads IS 'Leads captured from WhatsApp interactions';
COMMENT ON TABLE message_logs IS 'Log of all WhatsApp messages for debugging and analytics';
COMMENT ON TABLE processed_messages IS 'Deduplication table for WhatsApp webhook messages';
COMMENT ON TABLE rate_limits IS 'Rate limiting tracking for WhatsApp users';

CREATE TABLE IF NOT EXISTS analytics_events (
    id BIGSERIAL PRIMARY KEY,
    event VARCHAR(100) NOT NULL,
    page VARCHAR(100),
    automation_type VARCHAR(255),
    source VARCHAR(100),
    session_id VARCHAR(100),
    business_id BIGINT REFERENCES businesses(id) ON DELETE SET NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analytics_event ON analytics_events(event);
CREATE INDEX IF NOT EXISTS idx_analytics_session ON analytics_events(session_id);
CREATE INDEX IF NOT EXISTS idx_analytics_created_at ON analytics_events(created_at DESC);

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    business_id BIGINT REFERENCES businesses(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL DEFAULT 0,
    screenshot_data TEXT,
    screenshot_filename VARCHAR(255),
    screenshot_size INTEGER,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    verified_at TIMESTAMPTZ,
    verified_by VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    CONSTRAINT valid_payment_status CHECK (status IN ('pending', 'verified', 'rejected', 'refunded'))
);

ALTER TABLE businesses ADD COLUMN IF NOT EXISTS payment_status VARCHAR(50) DEFAULT 'unpaid';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS payment_amount INTEGER DEFAULT 0;


-- Add missing columns to businesses table
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS total_amount INTEGER DEFAULT 0;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS payment_status VARCHAR(50) DEFAULT 'unpaid';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS payment_amount INTEGER DEFAULT 0;

-- Create analytics_events table if not exists
CREATE TABLE IF NOT EXISTS analytics_events (
    id BIGSERIAL PRIMARY KEY,
    event VARCHAR(100) NOT NULL,
    page VARCHAR(100),
    automation_type VARCHAR(255),
    source VARCHAR(100),
    session_id VARCHAR(100),
    business_id BIGINT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create payments table if not exists
CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    business_id BIGINT,
    amount INTEGER NOT NULL DEFAULT 0,
    screenshot_data TEXT,
    screenshot_filename VARCHAR(255),
    screenshot_size INTEGER,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- ============================================================
-- ADMIN AUTHENTICATION TABLES
-- Add this to your existing supabase-schema.sql or run separately
-- ============================================================

-- Admin config table (stores encrypted passwords)
CREATE TABLE IF NOT EXISTS admin_config (
    id BIGSERIAL PRIMARY KEY,
    key VARCHAR(100) NOT NULL UNIQUE,
    value JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_admin_config_key ON admin_config(key);

-- Admin sessions table (for session management)
CREATE TABLE IF NOT EXISTS admin_sessions (
    id BIGSERIAL PRIMARY KEY,
    token VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_sessions_token ON admin_sessions(token);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires ON admin_sessions(expires_at);

-- Admin login attempts table (security logging)
CREATE TABLE IF NOT EXISTS admin_login_attempts (
    id BIGSERIAL PRIMARY KEY,
    success BOOLEAN NOT NULL,
    ip_address VARCHAR(100),
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_login_attempts_created ON admin_login_attempts(created_at DESC);

-- Enable RLS on admin tables
ALTER TABLE admin_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_login_attempts ENABLE ROW LEVEL SECURITY;

-- Create policies for service role (full access)
CREATE POLICY "Service role has full access to admin_config" ON admin_config
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Service role has full access to admin_sessions" ON admin_sessions
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Service role has full access to admin_login_attempts" ON admin_login_attempts
    FOR ALL USING (true) WITH CHECK (true);

-- Clean up expired sessions (run periodically or set up a cron job)
-- DELETE FROM admin_sessions WHERE expires_at < NOW();
-- ============================================================
-- CPC WhatsApp Bot - Meta Catalogue Integration Migration
-- Version: 2.2.0
-- Safe to run on existing database - will not break anything
-- ============================================================

-- Add new columns to orders table for catalogue support
ALTER TABLE orders 
ADD COLUMN IF NOT EXISTS items JSONB,
ADD COLUMN IF NOT EXISTS total_amount INTEGER,
ADD COLUMN IF NOT EXISTS order_source VARCHAR(20) DEFAULT 'bot_menu',
ADD COLUMN IF NOT EXISTS meta_order_id VARCHAR(100);

-- Add helpful comments
COMMENT ON COLUMN orders.items IS 'Array of ordered items from Meta catalogue (JSONB format)';
COMMENT ON COLUMN orders.total_amount IS 'Total order amount in paisa (e.g., 45000 = Rs 450)';
COMMENT ON COLUMN orders.order_source IS 'Order source: meta_catalogue or bot_menu';
COMMENT ON COLUMN orders.meta_order_id IS 'Order ID from Meta if order came from catalogue';

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_orders_order_number ON orders(order_number);
CREATE INDEX IF NOT EXISTS idx_orders_meta_order_id ON orders(meta_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_source ON orders(order_source);
CREATE INDEX IF NOT EXISTS idx_orders_source_status_created ON orders(order_source, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_wa_id_created ON orders(wa_id, created_at DESC);

-- Drop and recreate the order number generation function with better logic
DROP TRIGGER IF EXISTS set_order_number ON orders;
DROP FUNCTION IF EXISTS generate_order_number() CASCADE;

-- New function that generates unique order numbers with date prefix
CREATE OR REPLACE FUNCTION generate_order_number()
RETURNS TRIGGER AS $$
DECLARE
    new_order_num VARCHAR(50);
    date_part VARCHAR(8);
    random_part VARCHAR(8);
    counter INTEGER := 0;
BEGIN
    -- Only generate if order_number is not already set
    IF NEW.order_number IS NULL OR NEW.order_number = '' OR NEW.order_number LIKE 'ORD-%' AND LENGTH(NEW.order_number) < 15 THEN
        date_part := TO_CHAR(NOW(), 'YYYYMMDD');
        
        -- Try to generate a unique order number (max 10 attempts)
        LOOP
            random_part := UPPER(SUBSTRING(MD5(RANDOM()::TEXT || RANDOM()::TEXT) FROM 1 FOR 8));
            new_order_num := 'ORD-' || date_part || '-' || random_part;
            
            -- Check if this order number already exists
            IF NOT EXISTS (SELECT 1 FROM orders WHERE order_number = new_order_num) THEN
                EXIT;
            END IF;
            
            counter := counter + 1;
            IF counter > 10 THEN
                RAISE EXCEPTION 'Could not generate unique order number after 10 attempts';
            END IF;
        END LOOP;
        
        NEW.order_number := new_order_num;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for order number generation
CREATE TRIGGER set_order_number
    BEFORE INSERT ON orders
    FOR EACH ROW
    EXECUTE FUNCTION generate_order_number();

-- ============================================================
-- Enhanced Analytics Views
-- ============================================================

-- Drop old daily_orders view if exists
DROP VIEW IF EXISTS daily_orders CASCADE;

-- Create enhanced daily orders view with source tracking
CREATE OR REPLACE VIEW daily_orders AS
SELECT 
    DATE_TRUNC('day', created_at) as date,
    status,
    COALESCE(order_source, 'bot_menu') as order_source,
    COUNT(*) as count,
    SUM(COALESCE(total_amount, item_price * quantity)) as total_revenue,
    AVG(COALESCE(total_amount, item_price * quantity)) as avg_order_value
FROM orders
GROUP BY DATE_TRUNC('day', created_at), status, order_source
ORDER BY date DESC, order_source;

-- Order statistics by source
CREATE OR REPLACE VIEW order_source_stats AS
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

-- Top products from catalogue orders
CREATE OR REPLACE VIEW top_catalogue_products AS
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

-- Customer lifetime value with order source breakdown
CREATE OR REPLACE VIEW customer_lifetime_value AS
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

-- ============================================================
-- Helper Functions
-- ============================================================

-- Function to get formatted order details
CREATE OR REPLACE FUNCTION get_order_display(order_id BIGINT)
RETURNS TABLE (
    order_number VARCHAR(50),
    customer_info TEXT,
    items_display TEXT,
    total_display TEXT,
    status VARCHAR(50),
    source VARCHAR(20),
    created_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        o.order_number,
        o.customer_phone || ' (' || o.wa_id || ')' as customer_info,
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
    WHERE o.id = order_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Cleanup Functions
-- ============================================================

-- Clean up old processed messages (7+ days)
CREATE OR REPLACE FUNCTION cleanup_old_processed_messages()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM processed_messages 
    WHERE processed_at < NOW() - INTERVAL '7 days';
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Clean up old rate limits (2+ hours)
CREATE OR REPLACE FUNCTION cleanup_old_rate_limits()
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
-- Verify Migration Success
-- ============================================================
DO $$
DECLARE
    column_count INTEGER;
BEGIN
    -- Check if all new columns were added
    SELECT COUNT(*) INTO column_count
    FROM information_schema.columns 
    WHERE table_name = 'orders' 
      AND column_name IN ('items', 'total_amount', 'order_source', 'meta_order_id');
    
    IF column_count = 4 THEN
        RAISE NOTICE '✅ Meta Catalogue integration migration completed successfully!';
        RAISE NOTICE '📊 New columns added: items, total_amount, order_source, meta_order_id';
        RAISE NOTICE '🔍 New views created: order_source_stats, top_catalogue_products, customer_lifetime_value';
        RAISE NOTICE '🎯 Your database is ready for Meta catalogue orders!';
    ELSE
        RAISE WARNING '⚠ Migration partially completed. Only % of 4 columns were added.', column_count;
    END IF;
END $$;

-- ============================================================
-- Sample Queries (for testing - commented out)
-- ============================================================

-- Verify new columns exist
-- SELECT column_name, data_type 
-- FROM information_schema.columns 
-- WHERE table_name = 'orders' 
--   AND column_name IN ('items', 'total_amount', 'order_source', 'meta_order_id');

-- Check order statistics
-- SELECT * FROM order_source_stats;

-- View recent orders
-- SELECT order_number, customer_phone, 
--        COALESCE(order_source, 'bot_menu') as source,
--        COALESCE(total_amount, item_price)/100 as total_rs,
--        status, created_at
-- FROM orders 
-- ORDER BY created_at DESC 
-- LIMIT 10;