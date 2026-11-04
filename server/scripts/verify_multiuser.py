"""临时 Postgres 库、API、Worker 的多用户验收启动器。

python -m scripts.verify_multiuser --output /tmp/botly-multiuser-report.json
要求独立测试 Postgres 监听本机 55432；不会访问生产库。
"""
from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx
import psycopg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/private/tmp/botly-multiuser-report.json")
    args = parser.parse_args()
    database = "botly_acceptance_" + uuid4().hex[:8]
    with psycopg.connect("host=127.0.0.1 port=55432 dbname=postgres", autocommit=True) as conn:
        from psycopg import sql
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    env = dict(os.environ, DATABASE_URL=f"postgresql+psycopg://{getpass.getuser()}@127.0.0.1:55432/{database}",
        BOTLY_DATA_DIR=tempfile.mkdtemp(prefix="botly-multiuser-data-"),
        BOTLY_REDIS_URL="redis://127.0.0.1:6399/0", BOTLY_REDIS_PREFIX=database,
        BOTLY_SANDBOX_PROVIDER="none", BOTLY_SMTP_HOST="", BOTLY_MAIL_DEV="1",
        BOTLY_SECRET_KEY="isolated-multiuser-test-only", BOTLY_WORKER_CONCURRENCY="20",
        BOTLY_ENV="development",
        BOTLY_MAX_CONCURRENT_RUNS="20", BOTLY_LOG_LEVEL="WARNING", BOTLY_LOG_BODY="0")
    logs = open("/private/tmp/botly-multiuser-services.log", "w")
    processes = []
    try:
        processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", "18200", "--no-access-log"], env=env, stdout=logs, stderr=logs))
        for _ in range(100):
            if processes[0].poll() is not None:
                raise RuntimeError("测试 API 启动失败，见 /private/tmp/botly-multiuser-services.log")
            try:
                if httpx.get("http://127.0.0.1:18200/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError("测试 API 启动超时")
        processes.append(subprocess.Popen([sys.executable, "-m", "app.worker"], env=env, stdout=logs, stderr=logs))
        subprocess.run([sys.executable, "-m", "scripts.loadtest", "--base", "http://127.0.0.1:18200",
            "--users", "50", "--concurrency", "20", "--requests", "200", "--output", args.output], env=env, check=True)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        logs.close()
        print(f"测试数据库：{database}；测试日志：/private/tmp/botly-multiuser-services.log")


if __name__ == "__main__":
    main()
