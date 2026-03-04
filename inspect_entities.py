import sqlite3
from pprint import pprint

conn = sqlite3.connect("datebase/wwm.db")
cur = conn.cursor()

print("Entities:", cur.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
print("Sources:", cur.execute("SELECT source, COUNT(*) FROM entity_sources GROUP BY source").fetchall())

print("\nLast 10 entities:")
pprint(cur.execute("""
    SELECT entity_id, canonical_title
    FROM entities
    ORDER BY entity_id DESC
    LIMIT 10
""").fetchall())

conn.close()
