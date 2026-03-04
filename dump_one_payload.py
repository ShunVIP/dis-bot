import sqlite3
import json

conn = sqlite3.connect("datebase/wwm.db")
cur = conn.cursor()

# Берём один Game8 payload
cur.execute("""
    SELECT entity_id, payload_json
    FROM entity_sources
    WHERE source = 'game8'
    ORDER BY fetched_at DESC
    LIMIT 1
""")

row = cur.fetchone()
conn.close()

if not row:
    print("No game8 records found")
else:
    entity_id, payload_json = row
    print("ENTITY_ID:", entity_id)
    obj = json.loads(payload_json)

    text = obj.get("text", "")
    print("\n--- payload_json['text'] (first 2000 chars) ---\n")
    print(text[:2000])
