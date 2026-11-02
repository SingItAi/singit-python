"""Small command dispatcher."""
import os
import sys


def main(argv=None, stdout=None, stderr=None, env=None):
    argv = sys.argv[1:] if argv is None else argv
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    env = os.environ if env is None else env
    if argv and argv[0] == "spend":
        from .spend_cli import main as spend_main
        return spend_main(argv[1:], stdout, stderr, env)
    print("Usage: singit spend summary|export ...", file=stderr)
    return 2
