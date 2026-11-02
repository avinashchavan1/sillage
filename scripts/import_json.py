"""Import a Sillage export into the database (the same merge as POST /api/import).

    python -m scripts.import_json sillage-export.json
    SILLAGE_DB=/data/sillage.db python -m scripts.import_json sillage-export.json   # e.g. inside the container
"""

import json
import sys

from app import db, portability

if __name__ == "__main__":
    conn = db.connect()
    try:
        print(json.dumps(portability.import_all(conn, portability.load_file(sys.argv[1])), indent=1))
    finally:
        conn.close()
