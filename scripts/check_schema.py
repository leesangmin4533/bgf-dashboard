#!/usr/bin/env python3
"""DB 스키마 확인"""

import sqlite3

db_path = "C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/data/bgf_sales.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 테이블 목록
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cursor.fetchall()

print("전체 테이블 목록:")
for table in tables:
    print(f"  - {table[0]}")

print("\n" + "="*50)

# daily_sales 스키마
cursor.execute("PRAGMA table_info(daily_sales)")
columns = cursor.fetchall()

print("\ndaily_sales 테이블 컬럼:")
for col in columns:
    print(f"  {col[1]}: {col[2]}")

conn.close()
