import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
import config

DATABASE_URL = config.DATABASE_URL


def get_connection():
    """Establishes and returns a connection to PostgreSQL."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set!")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    """Initializes schema and tables in the PostgreSQL database."""
    with get_connection() as conn:
        with conn.cursor() as c:
            # Core Driver and Fleet Tables
            c.execute(
                """CREATE TABLE IF NOT EXISTS drivers (
                telegram_chat_id BIGINT PRIMARY KEY,
                samsara_driver_id TEXT,
                vehicle_id TEXT,
                truck_number TEXT,
                truck_license TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS truck_mapping (
                truck_number TEXT PRIMARY KEY,
                samsara_driver_id TEXT,
                vehicle_id TEXT,
                truck_license TEXT
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS dispatch (
                driver_id TEXT PRIMARY KEY,
                stop1_address TEXT,
                stop2_address TEXT,
                stop1_time TEXT,
                stop2_time TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS truck_specs (
                vehicle_id TEXT PRIMARY KEY,
                tank_capacity_gallons REAL DEFAULT 100,
                avg_mpg REAL DEFAULT 6.0
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS truck_stops (
                id SERIAL PRIMARY KEY,
                name TEXT,
                brand TEXT,
                address TEXT,
                city TEXT,
                state TEXT,
                lat REAL,
                lng REAL,
                price REAL,
                user_id TEXT,
                is_active INTEGER DEFAULT 1,
                UNIQUE(lat, lng)
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS points (
                id SERIAL PRIMARY KEY,
                driver_id TEXT,
                amount INTEGER,
                type TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS pti_submissions (
                id SERIAL PRIMARY KEY,
                driver_id TEXT,
                truck_number TEXT,
                trailer_number TEXT,
                pti_number TEXT,
                photo_count INTEGER,
                status TEXT DEFAULT 'pending',
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS fuel_station_usage (
                id SERIAL PRIMARY KEY,
                driver_id TEXT,
                station_name TEXT,
                station_address TEXT,
                station_lat REAL,
                station_lng REAL,
                price REAL,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_confirmed INTEGER DEFAULT 0
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS driver_verification (
                telegram_user_id BIGINT PRIMARY KEY,
                driver_name TEXT,
                truck_number TEXT,
                truck_photo_path TEXT,
                verified INTEGER DEFAULT 0,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS cashouts (
                id SERIAL PRIMARY KEY,
                driver_id TEXT,
                points_used INTEGER,
                amount_usd REAL,
                month TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS mini_app_users (
                telegram_user_id BIGINT PRIMARY KEY,
                driver_name TEXT,
                truck_number TEXT,
                last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS driver_credentials (
                truck_number TEXT PRIMARY KEY,
                password TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS bot_groups (
                chat_id BIGINT PRIMARY KEY,
                group_title TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            # Fault Codes, Harsh Driving Events, and Maintenance Alerts
            c.execute(
                """CREATE TABLE IF NOT EXISTS fault_codes (
                id SERIAL PRIMARY KEY,
                vehicle_id TEXT,
                truck_number TEXT,
                code TEXT,
                description TEXT,
                severity TEXT,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS harsh_events (
                id SERIAL PRIMARY KEY,
                vehicle_id TEXT,
                truck_number TEXT,
                event_type TEXT,
                location TEXT,
                video_url TEXT,
                happened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

            c.execute(
                """CREATE TABLE IF NOT EXISTS maintenance_alerts (
                id SERIAL PRIMARY KEY,
                vehicle_id TEXT,
                truck_number TEXT,
                maintenance_type TEXT,
                due_mileage INTEGER,
                location TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
            )

    print("✅ Database initialized successfully!")


# ==================== GROUP TRACKING ====================


def save_bot_group(chat_id: int, group_title: str):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO bot_groups (chat_id, group_title)
                VALUES (%s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET group_title = EXCLUDED.group_title""",
                (chat_id, group_title),
            )


def get_all_bot_groups():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT chat_id, group_title, added_at FROM bot_groups ORDER BY added_at DESC"
            )
            return c.fetchall()


# ==================== FAULT CODES ====================


def save_fault_code(vehicle_id, truck_number, code, description, severity):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO fault_codes (vehicle_id, truck_number, code, description, severity)
                VALUES (%s, %s, %s, %s, %s)""",
                (vehicle_id, truck_number, code, description, severity),
            )


def get_all_fault_codes():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT vehicle_id, truck_number, code, description, severity, recorded_at FROM fault_codes ORDER BY recorded_at DESC"
            )
            return c.fetchall()


# ==================== HARSH EVENTS ====================


def save_harsh_event(
    vehicle_id, truck_number, event_type, location, video_url
):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO harsh_events (vehicle_id, truck_number, event_type, location, video_url)
                VALUES (%s, %s, %s, %s, %s)""",
                (vehicle_id, truck_number, event_type, location, video_url),
            )


def get_all_harsh_events():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT vehicle_id, truck_number, event_type, location, video_url, happened_at FROM harsh_events ORDER BY happened_at DESC"
            )
            return c.fetchall()


# ==================== MAINTENANCE ALERTS ====================


def save_maintenance_alert(
    vehicle_id, truck_number, maintenance_type, due_mileage, location
):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO maintenance_alerts (vehicle_id, truck_number, maintenance_type, due_mileage, location)
                VALUES (%s, %s, %s, %s, %s)""",
                (
                    vehicle_id,
                    truck_number,
                    maintenance_type,
                    due_mileage,
                    location,
                ),
            )


def get_all_maintenance_alerts():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT vehicle_id, truck_number, maintenance_type, due_mileage, location, created_at FROM maintenance_alerts ORDER BY created_at DESC"
            )
            return c.fetchall()


# ==================== TRUCK MAPPING ====================


def get_truck_number_from_group(group_name: str):
    if not group_name:
        return None
    match = re.search(r"^(\d+)", group_name.strip())
    return match.group(1) if match else None


def register_truck_mapping(
    truck_number, driver_id, vehicle_id, truck_license=""
):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO truck_mapping (truck_number, samsara_driver_id, vehicle_id, truck_license)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (truck_number) DO UPDATE SET
                samsara_driver_id = EXCLUDED.samsara_driver_id,
                vehicle_id = EXCLUDED.vehicle_id,
                truck_license = EXCLUDED.truck_license""",
                (truck_number, driver_id, vehicle_id, truck_license),
            )


def get_mapping_by_truck_number(truck_number):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT samsara_driver_id, vehicle_id, truck_license FROM truck_mapping WHERE truck_number = %s",
                (truck_number,),
            )
            r = c.fetchone()
            if r:
                return {
                    "driver_id": r["samsara_driver_id"],
                    "vehicle_id": r["vehicle_id"],
                    "truck_license": r["truck_license"] or "",
                }
            return None


# ==================== DRIVER REGISTRATION ====================


def register_driver_auto(
    chat_id, truck_number, driver_id, vehicle_id, truck_license=""
):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO drivers (telegram_chat_id, samsara_driver_id, vehicle_id, truck_number, truck_license)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (telegram_chat_id) DO UPDATE SET
                samsara_driver_id = EXCLUDED.samsara_driver_id,
                vehicle_id = EXCLUDED.vehicle_id,
                truck_number = EXCLUDED.truck_number,
                truck_license = EXCLUDED.truck_license""",
                (chat_id, driver_id, vehicle_id, truck_number, truck_license),
            )


def register_driver(chat_id, driver_id, vehicle_id=None, truck_plate=None):
    truck_number = None
    if vehicle_id:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute(
                    "SELECT truck_number FROM truck_mapping WHERE vehicle_id = %s",
                    (vehicle_id,),
                )
                r = c.fetchone()
                truck_number = r[0] if r else None

    register_driver_auto(
        chat_id, truck_number, driver_id, vehicle_id, truck_plate or ""
    )


def get_driver_by_chat(chat_id):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT samsara_driver_id, vehicle_id, truck_number, truck_license FROM drivers WHERE telegram_chat_id = %s",
                (chat_id,),
            )
            r = c.fetchone()
            if r:
                return {
                    "driver_id": r["samsara_driver_id"],
                    "vehicle_id": r["vehicle_id"],
                    "truck_number": r["truck_number"],
                    "truck_license": r["truck_license"] or "",
                }
            return None


def get_all_drivers():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT telegram_chat_id, samsara_driver_id, vehicle_id, truck_number, truck_license FROM drivers"
            )
            return c.fetchall()


# ==================== ADMIN DRIVER FUNCTIONS ====================


def register_driver_admin(
    truck_number,
    chat_id,
    samsara_driver_id,
    vehicle_id,
    password,
    truck_license="",
):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO drivers (telegram_chat_id, samsara_driver_id, vehicle_id, truck_number, truck_license)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (telegram_chat_id) DO UPDATE SET
                samsara_driver_id = EXCLUDED.samsara_driver_id,
                vehicle_id = EXCLUDED.vehicle_id,
                truck_number = EXCLUDED.truck_number,
                truck_license = EXCLUDED.truck_license""",
                (
                    chat_id,
                    samsara_driver_id,
                    vehicle_id,
                    truck_number,
                    truck_license,
                ),
            )

            c.execute(
                """INSERT INTO truck_mapping (truck_number, samsara_driver_id, vehicle_id, truck_license)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (truck_number) DO UPDATE SET
                samsara_driver_id = EXCLUDED.samsara_driver_id,
                vehicle_id = EXCLUDED.vehicle_id,
                truck_license = EXCLUDED.truck_license""",
                (truck_number, samsara_driver_id, vehicle_id, truck_license),
            )

            c.execute(
                """INSERT INTO driver_credentials (truck_number, password)
                VALUES (%s, %s)
                ON CONFLICT (truck_number) DO UPDATE SET password = EXCLUDED.password""",
                (truck_number, password),
            )
    return True


def get_driver_by_truck(truck_number):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """SELECT d.telegram_chat_id, d.samsara_driver_id, d.vehicle_id, d.truck_number, d.truck_license,
                        dc.password
                 FROM drivers d
                 LEFT JOIN driver_credentials dc ON d.truck_number = dc.truck_number
                 WHERE d.truck_number = %s""",
                (truck_number,),
            )
            r = c.fetchone()
            if r:
                return {
                    "chat_id": r["telegram_chat_id"],
                    "driver_id": r["samsara_driver_id"],
                    "vehicle_id": r["vehicle_id"],
                    "truck_number": r["truck_number"],
                    "truck_license": r["truck_license"] or "",
                    "password": r["password"] or "",
                }
            return None


def list_all_drivers_admin():
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """SELECT d.telegram_chat_id, d.samsara_driver_id, d.vehicle_id, d.truck_number, d.truck_license,
                        dc.password
                 FROM drivers d
                 LEFT JOIN driver_credentials dc ON d.truck_number = dc.truck_number"""
            )
            rows = c.fetchall()
            return [dict(row) for row in rows]


# ==================== DISPATCH ====================


def set_dispatch(driver_id, stop1, stop2, time1="", time2=""):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO dispatch (driver_id, stop1_address, stop2_address, stop1_time, stop2_time)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (driver_id) DO UPDATE SET
                stop1_address = EXCLUDED.stop1_address,
                stop2_address = EXCLUDED.stop2_address,
                stop1_time = EXCLUDED.stop1_time,
                stop2_time = EXCLUDED.stop2_time""",
                (driver_id, stop1, stop2, time1, time2),
            )


def set_dispatch_route(driver_id, pickups, deliveries, destination):
    stop1 = pickups[0] if pickups else destination
    stop2 = destination
    set_dispatch(driver_id, stop1, stop2, "", "")


def get_dispatch(driver_id):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT stop1_address, stop2_address, stop1_time, stop2_time FROM dispatch WHERE driver_id = %s",
                (driver_id,),
            )
            r = c.fetchone()
            if r:
                return {
                    "stop1": r["stop1_address"],
                    "stop2": r["stop2_address"],
                    "time1": r["stop1_time"],
                    "time2": r["stop2_time"],
                }
            return None


# ==================== TRUCK SPECS ====================


def set_truck_specs(vehicle_id, tank, mpg):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO truck_specs (vehicle_id, tank_capacity_gallons, avg_mpg)
                VALUES (%s, %s, %s)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                tank_capacity_gallons = EXCLUDED.tank_capacity_gallons,
                avg_mpg = EXCLUDED.avg_mpg""",
                (vehicle_id, tank, mpg),
            )


def get_truck_specs(vehicle_id):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT tank_capacity_gallons, avg_mpg FROM truck_specs WHERE vehicle_id = %s",
                (vehicle_id,),
            )
            r = c.fetchone()
            if r:
                return {"tank": r["tank_capacity_gallons"], "mpg": r["avg_mpg"]}
            return {"tank": 100, "mpg": 6.0}


# ==================== TRUCK STOPS ====================


def get_truck_stops_near(lat, lng, radius_miles=100, brands=None):
    delta = radius_miles / 69.0
    min_lat, max_lat = lat - delta, lat + delta
    min_lng, max_lng = lng - delta, lng + delta

    query = """SELECT name, brand, address, city, state, lat, lng, price
               FROM truck_stops
               WHERE lat BETWEEN %s AND %s AND lng BETWEEN %s AND %s AND is_active = 1"""
    params = [min_lat, max_lat, min_lng, max_lng]

    if brands:
        placeholders = ",".join(["%s"] * len(brands))
        query += f" AND brand IN ({placeholders})"
        params.extend(brands)

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(query, params)
            rows = c.fetchall()

    return [
        {
            "name": r[0],
            "brand": r[1],
            "address": r[2],
            "city": r[3],
            "state": r[4],
            "lat": r[5],
            "lng": r[6],
            "price": r[7],
        }
        for r in rows
    ]


def add_user_fuel_stop(
    name, brand, address, city, state, lat, lng, price, user_id=""
):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO truck_stops (name, brand, address, city, state, lat, lng, price, user_id, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)""",
                (name, brand, address, city, state, lat, lng, price, user_id),
            )


# ==================== MINI APP USERS ====================


def save_mini_app_user(telegram_user_id, driver_name, truck_number):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO mini_app_users (telegram_user_id, driver_name, truck_number)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_user_id) DO UPDATE SET
                driver_name = EXCLUDED.driver_name,
                truck_number = EXCLUDED.truck_number,
                last_used = CURRENT_TIMESTAMP""",
                (telegram_user_id, driver_name, truck_number),
            )


def get_mini_app_user(telegram_user_id):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT driver_name, truck_number FROM mini_app_users WHERE telegram_user_id = %s",
                (telegram_user_id,),
            )
            r = c.fetchone()
            if r:
                return {
                    "driver_name": r["driver_name"],
                    "truck_number": r["truck_number"],
                }
            return None


# ==================== DRIVER CREDENTIALS ====================


def set_driver_credentials(truck_number, password):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO driver_credentials (truck_number, password)
                VALUES (%s, %s)
                ON CONFLICT (truck_number) DO UPDATE SET password = EXCLUDED.password""",
                (truck_number, password),
            )


def verify_driver_login(truck_number, password):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT 1 FROM driver_credentials WHERE truck_number = %s AND password = %s",
                (truck_number, password),
            )
            return c.fetchone() is not None


# ==================== POINTS ====================


def add_points(driver_id, amount, type, description=""):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO points (driver_id, amount, type, description) VALUES (%s, %s, %s, %s)",
                (driver_id, amount, type, description),
            )


def get_points_balance(driver_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM points WHERE driver_id = %s",
                (driver_id,),
            )
            return c.fetchone()[0]


def get_points_history(driver_id, limit=20):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT amount, type, description, created_at FROM points WHERE driver_id = %s ORDER BY created_at DESC LIMIT %s",
                (driver_id, limit),
            )
            return c.fetchall()


# ==================== PTI ====================


def add_pti_submission(
    driver_id, truck_number, trailer_number, photo_count
):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO pti_submissions (driver_id, truck_number, trailer_number, photo_count, status)
                VALUES (%s, %s, %s, %s, 'submitted')""",
                (driver_id, truck_number, trailer_number, photo_count),
            )


def add_pti(driver_id, pti_number):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO pti_submissions (driver_id, pti_number) VALUES (%s, %s)",
                (driver_id, pti_number),
            )


def get_pti_history(driver_id, limit=20):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT pti_number, submitted_at, status FROM pti_submissions WHERE driver_id = %s ORDER BY submitted_at DESC LIMIT %s",
                (driver_id, limit),
            )
            return c.fetchall()


# ==================== FUEL STATION USAGE ====================


def add_fuel_station_usage(
    driver_id, station_name, station_address, station_lat, station_lng, price
):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO fuel_station_usage (driver_id, station_name, station_address, station_lat, station_lng, price)
                VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    driver_id,
                    station_name,
                    station_address,
                    station_lat,
                    station_lng,
                    price,
                ),
            )


def get_fuel_station_usage(driver_id, limit=20):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT station_name, station_address, station_lat, station_lng, price, used_at, is_confirmed FROM fuel_station_usage WHERE driver_id = %s ORDER BY used_at DESC LIMIT %s",
                (driver_id, limit),
            )
            return c.fetchall()


def confirm_fuel_station_usage(usage_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE fuel_station_usage SET is_confirmed = 1 WHERE id = %s",
                (usage_id,),
            )


# ==================== VERIFICATION ====================


def submit_verification(
    telegram_user_id, driver_name, truck_number, truck_photo_path
):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO driver_verification (telegram_user_id, driver_name, truck_number, truck_photo_path, verified)
                VALUES (%s, %s, %s, %s, 0)
                ON CONFLICT (telegram_user_id) DO UPDATE SET
                driver_name = EXCLUDED.driver_name,
                truck_number = EXCLUDED.truck_number,
                truck_photo_path = EXCLUDED.truck_photo_path,
                verified = 0""",
                (telegram_user_id, driver_name, truck_number, truck_photo_path),
            )


def get_verification(telegram_user_id):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT driver_name, truck_number, truck_photo_path, verified FROM driver_verification WHERE telegram_user_id = %s",
                (telegram_user_id,),
            )
            r = c.fetchone()
            if r:
                return {
                    "driver_name": r["driver_name"],
                    "truck_number": r["truck_number"],
                    "truck_photo_path": r["truck_photo_path"],
                    "verified": r["verified"],
                }
            return None


# ==================== CASHOUTS ====================


def add_cashout(driver_id, points_used, amount_usd, month):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO cashouts (driver_id, points_used, amount_usd, month) VALUES (%s, %s, %s, %s)",
                (driver_id, points_used, amount_usd, month),
            )


def get_cashouts(driver_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT points_used, amount_usd, month, created_at FROM cashouts WHERE driver_id = %s ORDER BY created_at DESC",
                (driver_id,),
            )
            return c.fetchall()


# ==================== ADMIN SUMMARY ====================


def get_all_points_summary():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT driver_id, SUM(amount) FROM points GROUP BY driver_id"
            )
            return c.fetchall()


def get_all_pti_summary():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT driver_id, COUNT(*) FROM pti_submissions GROUP BY driver_id"
            )
            return c.fetchall()


def get_all_fuel_usage():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT driver_id, station_name, used_at, is_confirmed FROM fuel_station_usage ORDER BY used_at DESC"
            )
            return c.fetchall()
