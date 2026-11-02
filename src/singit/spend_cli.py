"""Offline spend summary and CSV export."""
import argparse
import json
from pathlib import Path
from datetime import timedelta

from .spend import default_spend_path, read_spend, spend_summary, _payments, _now, _usd

USAGE = "Usage: singit spend summary [--days N] [--file PATH] [--json] | singit spend export --csv [--days N] [--file PATH]"


def _csv_cell(value):
    if value.startswith(("=", "+", "-", "@")):
        value = "'" + value
    if any(c in value for c in (',', '"', "\r", "\n")):
        return '"' + value.replace('"', '""') + '"'
    return value


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv, stdout, stderr, env):
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("command", choices=("summary", "export"))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--file")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--csv", action="store_true")
    try:
        args = parser.parse_args(argv)
        if args.days < 1 or (args.command == "summary" and args.csv) or (args.command == "export" and (not args.csv or args.json)):
            raise ValueError("invalid options")
    except ValueError:
        print(USAGE, file=stderr)
        return 2
    path = Path(args.file) if args.file is not None else default_spend_path(env)
    try:
        summary = spend_summary(path, args.days)
        if args.command == "export":
            print("day,time,id,network,payer,scheme,resource,status,ceilingAtomic,spentAtomic,tx", file=stdout)
            today = _now().date()
            first = (today - timedelta(days=min(args.days - 1, today.toordinal() - 1))).isoformat()
            for p in _payments(read_spend(path)["lines"]):
                if first <= p["day"] <= today.isoformat():
                    values = [p["day"], p["t"], p["id"], p["network"], p["payer"], p["scheme"], p["resource"], p["status"], p["amountAtomic"], p["spentAtomic"], p["tx"]]
                    print(",".join(_csv_cell(v) for v in values), file=stdout)
        elif args.json:
            print(json.dumps(summary, separators=(",", ":")), file=stdout)
        elif not path.exists():
            print(f"No spend log yet at {path}.", file=stdout)
        else:
            print(f"Spend log: {path}\nLocal client-side log: not on-chain enforcement.", file=stdout)
            print("day network payer payments spent (USDC) settled unresolved released", file=stdout)
            totals = [0, 0, 0, 0, 0]
            for row in summary["days"]:
                payer = row["payer"]
                short = payer[:6] + "..." + payer[-4:] if len(payer) > 10 else payer
                print(f'{row["day"]} {row["network"]} {short} {row["payments"]} {row["spentUsd"]} {row["settled"]} {row["unresolved"]} {row["released"]}', file=stdout)
                for i, key in enumerate(("payments", "spentAtomic", "settled", "unresolved", "released")):
                    totals[i] += int(row[key])
            print(f"Totals: {totals[0]} payments {_usd(totals[1])} USDC {totals[2]} settled {totals[3]} unresolved {totals[4]} released", file=stdout)
            print("Unresolved payments are counted at their ceiling.", file=stdout)
            if summary["skippedLines"]:
                print(f'Skipped unreadable lines: {summary["skippedLines"]}', file=stdout)
        return 0
    except OSError as error:
        print(f"Could not read spend log {path}: {error}", file=stderr)
        return 1
