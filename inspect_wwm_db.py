import sqlite3
from pprint import pprint

conn = sqlite3.connect("datebase/wwm.db")
cur = conn.cursor()

print("Всего записей:")
cur.execute("SELECT COUNT(*) FROM raw_records")
print(cur.fetchone()[0])

print("\nПо источникам:")
cur.execute("SELECT source, COUNT(*) FROM raw_records GROUP BY source")
pprint(cur.fetchall())

print("\nПоследние 5 записей:")
cur.execute("""
    SELECT title, url, fetched_at
    FROM raw_records
    ORDER BY fetched_at DESC
    LIMIT 5
""")
pprint(cur.fetchall())

conn.close()
