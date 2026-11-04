"""
EonOS - AI + Desktop + Tools 统一平台
一键启动入口
"""

import sys
import os

# 确保包路径正确 - 父目录需要在sys.path中
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)


def main():
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="EonOS - AI + Desktop + Tools")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    from eonos.server import run_server
    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
