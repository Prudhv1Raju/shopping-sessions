#!/usr/bin/env python3
"""Run fixtures and compare exact rows and Parquet schemas using PyArrow.

Usage: python scripts/run_fixtures.py FIXTURES_DIR -- CMD...
CMD receives DATA_DIR, OUTPUT_DIR, SCRATCH_DIR, and trailing data/output args.
"""
from collections import Counter
import csv
import datetime as dt
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

SCHEMAS = {
    'user_daily': {
        'user_id': 'string', 'day': 'date', 'sessions': 'integer',
        'events': 'integer', 'revenue_cents': 'integer', 'top_category': 'string',
    },
    'campaign_daily': {
        'campaign_id': 'string', 'channel': 'string', 'day': 'date',
        'sessions': 'integer', 'purchasing_sessions': 'integer',
        'revenue_cents': 'integer', 'assisted_sessions': 'integer',
    },
}


def load_expected(path, schema):
    """Read a trusted expected CSV; only top_category may be null."""
    result = []
    with Path(path).open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        if len(columns) != len(schema) or set(columns) != set(schema):
            raise ValueError(f'{path}: expected columns {list(schema)}, got {columns}')
        for record in reader:
            values = []
            for column, kind in schema.items():
                value = record[column]
                if value is None:
                    raise ValueError(f'{path}: missing {column}')
                if column == 'top_category' and value == '':
                    value = None
                elif kind == 'integer':
                    value = int(value)
                elif kind == 'date':
                    value = dt.date.fromisoformat(value)
                values.append(value)
            result.append(tuple(values))
    return result


def is_string(kind):
    if pa.types.is_dictionary(kind):
        return is_string(kind.value_type)
    return pa.types.is_string(kind) or pa.types.is_large_string(kind)


def load_actual(folder, schema):
    """Load visible, direct Parquet files, checking schema before normalizing."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError(f'expected an output directory: {folder}')
    result = []
    found = False
    for path in sorted(folder.glob('*.parquet')):
        if path.name.startswith(('.', '_')) or path.is_symlink() or not path.is_file():
            continue
        found = True
        frame = pq.ParquetFile(path).read()
        if len(frame.column_names) != len(schema) or set(frame.column_names) != set(schema):
            raise ValueError(f'{path}: expected {list(schema)}, got {frame.column_names}')
        for column, expected in schema.items():
            actual = frame.schema.field(column).type
            valid = (
                (expected == 'integer' and pa.types.is_integer(actual))
                or (expected == 'string' and is_string(actual))
                or (expected == 'date' and (pa.types.is_date32(actual) or pa.types.is_date64(actual)))
            )
            if not valid:
                raise ValueError(f'{path}: {column} has type {actual}, expected {expected}')
            if column != 'top_category' and frame[column].null_count:
                raise ValueError(f'{path}: {column} contains null values')
        columns = [frame[column].to_pylist() for column in schema]
        result.extend(zip(*columns))
    if not found:
        raise ValueError(f'no visible Parquet files in {folder}')
    return result


def rows(records):
    return sorted(records, key=lambda row: tuple('' if value is None else str(value) for value in row))


def duplicate_keys(records, schema):
    indices = [i for i, column in enumerate(schema) if column in ('user_id', 'campaign_id', 'day')]
    return len(records) - len({tuple(row[i] for i in indices) for row in records})


def differences(left, right):
    a, b = Counter(left), Counter(right)
    return a-b, b-a


def compare_records(left, right, schema, label):
    only_left, only_right = differences(left, right)
    duplicate_left = duplicate_keys(left, schema)
    duplicate_right = duplicate_keys(right, schema)
    matched = not (only_left or only_right or duplicate_left or duplicate_right)
    if not matched:
        print(f'{label}: rows {len(left)} vs {len(right)}; '
              f'only in expected/A {sum(only_left.values())}; '
              f'only in actual/B {sum(only_right.values())}; '
              f'duplicate keys {duplicate_left} vs {duplicate_right}')
        print(f'  expected/A examples: {rows(list(only_left))[:5]!r}')
        print(f'  actual/B examples:   {rows(list(only_right))[:5]!r}')
    return matched


def main():
    if '--' not in sys.argv or len(sys.argv) < 4:
        raise SystemExit(__doc__)
    sep = sys.argv.index('--')
    fixture_dir = Path(sys.argv[1]).resolve()
    command = sys.argv[sep + 1:]
    if not command:
        raise SystemExit('a command is required after --')
    cases = sorted(path for path in fixture_dir.iterdir() if path.is_dir())
    if not cases:
        raise SystemExit(f'no fixtures in {fixture_dir}')
    failed = []
    for case in cases:
        data = case / 'data'
        if not data.is_dir():
            data = case / 'input'
        with tempfile.TemporaryDirectory(prefix='shopping-fixture-', ignore_cleanup_errors=True) as temp:
            output = Path(temp) / 'output'
            scratch = Path(temp) / 'scratch'
            output.mkdir()
            scratch.mkdir()
            env = dict(os.environ, DATA_DIR=str(data), OUTPUT_DIR=str(output), SCRATCH_DIR=str(scratch))
            env.setdefault('NCPU', '8')
            env.setdefault('MEM_GB', '24')
            process = subprocess.run(command + [str(data), str(output)], env=env,
                                     capture_output=True, text=True, encoding='utf-8', errors='replace')
            if process.returncode != 0:
                failed.append(case.name)
                print(f'FAIL {case.name}: exit {process.returncode}\n{process.stderr[-2000:]}')
                continue
            matched = True
            for table, schema in SCHEMAS.items():
                try:
                    expected = load_expected(case / 'expected' / f'{table}.csv', schema)
                    actual = load_actual(output / table, schema)
                    if not compare_records(expected, actual, schema, f'FAIL {case.name} {table}'):
                        matched = False
                except (ValueError, OSError, pa.ArrowException) as error:
                    matched = False
                    print(f'FAIL {case.name} {table}: {error}')
            if matched:
                print(f'ok   {case.name}')
            else:
                failed.append(case.name)
    print(f'\n{len(cases)-len(failed)}/{len(cases)} fixtures passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
