#!/usr/bin/env python3
"""Exact, order-insensitive diff of output folders. Usage: compare.py OUT_A OUT_B"""
import argparse
from pathlib import Path
import sys

import pyarrow as pa

from run_fixtures import SCHEMAS, compare_records, load_actual, load_expected


def load(folder, table):
    expected_csv = folder / f'{table}.csv'
    if expected_csv.is_file():
        return load_expected(expected_csv, SCHEMAS[table])
    return load_actual(folder / table, SCHEMAS[table])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out_a', type=Path)
    parser.add_argument('out_b', type=Path)
    args = parser.parse_args()
    matched = True
    for table, schema in SCHEMAS.items():
        try:
            a = load(args.out_a, table)
            b = load(args.out_b, table)
            if compare_records(a, b, schema, table):
                print(f'{table}: {len(a)} rows match; no duplicate keys; schema valid')
            else:
                matched = False
        except (ValueError, OSError, pa.ArrowException) as error:
            print(f'{table}: {error}')
            matched = False
    print('MATCH' if matched else 'DIFFER')
    return 0 if matched else 1


if __name__ == '__main__':
    sys.exit(main())
