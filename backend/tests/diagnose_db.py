import sys
import os
import subprocess
from dotenv import load_dotenv

print("\n" + "=" * 70)
print("TrendLabs Database Diagnostic")
print("=" * 70)

# ============================================================================
# 1. Check environment variables
# ============================================================================
print("\n[1] Checking environment variables...")
load_dotenv()

db_url = os.getenv("DATABASE_URL")
db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")
db_host = os.getenv("DB_HOST")
db_port = os.getenv("DB_PORT")
db_name = os.getenv("DB_NAME")

print(f"✓ DATABASE_URL: {'SET ✅' if db_url else 'NOT SET ❌'}")
if db_url:
    masked = db_url.replace(db_password, "***") if db_password else db_url
    print(f"  → {masked}")

print(f"✓ DB_USER: {db_user if db_user else 'NOT SET ❌'}")
print(f"✓ DB_PASSWORD: {'SET ✅' if db_password else 'NOT SET ❌'}")
print(f"✓ DB_HOST: {db_host if db_host else 'NOT SET ❌'}")
print(f"✓ DB_PORT: {db_port if db_port else 'NOT SET ❌'}")
print(f"✓ DB_NAME: {db_name if db_name else 'NOT SET ❌'}")

# ============================================================================
# 2. Check Docker MySQL
# ============================================================================
print("\n[2] Checking Docker MySQL container...")
try:
    result = subprocess.run(
        ["docker", "ps", "--filter", "name=mysql"],
        capture_output=True,
        text=True,
        timeout=5
    )
    if "mysql" in result.stdout:
        print("✅ Docker MySQL container is running")
        print(result.stdout)
    else:
        print("❌ Docker MySQL container is NOT running")
        print("   Run: docker-compose up -d")
except Exception as e:
    print(f"⚠️  Could not check Docker: {e}")

# ============================================================================
# 3. Test MySQL connection with mysql-connector
# ============================================================================
print("\n[3] Testing direct MySQL connection...")
try:
    import pymysql

    # Parse DATABASE_URL or use individual variables
    if db_url and "@" in db_url:
        # Parse mysql+pymysql://user:password@host:port/database
        auth_and_host = db_url.split("://")[1]
        auth, host_db = auth_and_host.split("@")
        user, password = auth.split(":")
        host, port_db = host_db.split(":")
        host_port, db = port_db.split("/")
        port = int(host_port)
    else:
        user = db_user or "root"
        password = db_password or "password"
        host = db_host or "localhost"
        port = int(db_port or 3306)
        db = db_name or "trendlabs"

    print(f"   Attempting: {user}@{host}:{port}/{db}")

    connection = pymysql.connect(
        host=host,
        user=user,
        password=password,
        database=db,
        port=port,
        connect_timeout=5
    )
    print("✅ Direct MySQL connection successful!")

    # Check if schema is loaded
    cursor = connection.cursor()
    cursor.execute("SHOW TABLES")
    tables = cursor.fetchall()
    print(f"   Tables in database: {len(tables)}")
    for (table_name,) in tables:
        print(f"     - {table_name}")
    cursor.close()
    connection.close()

except Exception as e:
    print(f"❌ Direct MySQL connection failed: {e}")
    print(f"   Troubleshooting:")
    print(f"   - Check if MySQL is running: docker-compose logs mysql")
    print(f"   - Verify credentials in .env")
    print(f"   - For Docker: use host 'mysql' (not localhost)")

# ============================================================================
# 4. Test SQLAlchemy connection
# ============================================================================
print("\n[4] Testing SQLAlchemy connection...")
try:
    from sqlalchemy import create_engine, text

    # Use DATABASE_URL if set
    url = db_url if db_url else f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    engine = create_engine(url, echo=False, pool_pre_ping=True)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        print("✅ SQLAlchemy connection successful!")
        print(f"   Connection string: {url.replace(db_password or 'password', '***')}")

except Exception as e:
    print(f"❌ SQLAlchemy connection failed: {e}")
    print(f"   Make sure:")
    print(f"   - DATABASE_URL is correct in .env")
    print(f"   - For Docker: use host 'mysql' (not localhost)")
    print(f"   - MySQL is running and healthy: docker-compose ps")
