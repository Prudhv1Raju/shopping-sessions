"""Compare public dev output to canonical digests of the supplied answer tables."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_fixtures import SCHEMAS, load_actual, rows, duplicate_keys

def main():
    expected = json.loads((Path(__file__).parent / "dev-digests.json").read_text())
    for table, schema in SCHEMAS.items():
        data = load_actual(Path(sys.argv[1]) / table, schema)
        if duplicate_keys(data, schema):
            raise ValueError(f"{table}: duplicate keys")
        digest = hashlib.sha256()
        for row in rows(data):
            digest.update((json.dumps(row, ensure_ascii=True, separators=(",", ":"), default=str) + "\n").encode())
        actual = {"rows": len(data), "sha256": digest.hexdigest()}
        if actual != expected[table]:
            raise ValueError(f"{table}: public dev answer mismatch: {actual}")
        print(f"{table}: {actual['rows']} rows match the public dev answer digest")

if __name__ == "__main__":
    main()
