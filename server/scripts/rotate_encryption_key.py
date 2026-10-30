"""主密钥轮换：默认只预览；--apply 前先停止 API/Worker，完成后更新持久环境并重启。"""
from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.config import DATA_DIR, ENCRYPTION_KEY
from app.db import SessionLocal
from app.models import Provider, Run, UserSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='实际修改数据库；默认只预览')
    parser.add_argument('--new-key-env', default='BOTLY_NEW_ENCRYPTION_KEY', help='保存新 Fernet 主密钥的环境变量名')
    parser.add_argument('--backup', type=Path, help='加密字段备份路径；文件不能已存在')
    args = parser.parse_args()
    new_key = os.getenv(args.new_key_env)
    if not new_key:
        parser.error(f'请先通过环境变量 {args.new_key_env} 提供新主密钥，不要在命令行参数中传递密钥')
    old_cipher = Fernet(ENCRYPTION_KEY)
    try:
        new_cipher = Fernet(new_key.encode())
    except (ValueError, TypeError):
        parser.error('新主密钥不是有效的 Fernet 密钥')

    def rotate(value):
        if not value:
            return value
        try:
            return new_cipher.encrypt(old_cipher.decrypt(value.encode())).decode()
        except InvalidToken:
            raise SystemExit('存在无法用当前主密钥解密的数据，未提交任何更改') from None

    with SessionLocal() as db:
        active = db.scalar(select(Run.id).where(Run.status.in_(['queued', 'running', 'waiting'])).limit(1))
        if args.apply and active:
            raise SystemExit('还有未结束的执行；请先处理这些执行并停止 API/Worker，再轮换主密钥')
        updates = []
        for model, attr in [(Provider, 'api_key_encrypted'), (UserSettings, 'search_api_key_encrypted')]:
            for row in db.scalars(select(model)):
                old = getattr(row, attr)
                if old:
                    identity = row.id if isinstance(row, Provider) else row.user_id
                    updates.append((row, attr, rotate(old), {'table': model.__tablename__, 'id': identity, 'field': attr, 'value': old}))
        for run in db.scalars(select(Run)):
            snapshot = copy.deepcopy(run.config_snapshot or {})
            changed = False
            for bot in snapshot.get('bot_configs', {}).values():
                provider = bot.get('provider')
                if provider and provider.get('api_key_encrypted'):
                    provider['api_key_encrypted'] = rotate(provider['api_key_encrypted'])
                    changed = True
            if changed:
                updates.append((run, 'config_snapshot', snapshot, {'table': 'runs', 'id': run.id, 'field': 'config_snapshot', 'value': run.config_snapshot}))
        print(f'可轮换 {len(updates)} 条配置记录（含执行快照）；不会输出密钥内容。')
        if not args.apply:
            print('当前为预览模式，数据库未修改。确认服务已停止、备份位置可用后，再使用 --apply。')
            return
        backup = args.backup or DATA_DIR / f'encryption-backup-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json'
        with backup.open('x', encoding='utf-8') as file:
            os.chmod(backup, 0o600)
            json.dump({'records': [item[3] for item in updates]}, file, ensure_ascii=False, indent=2)
        for row, attr, value, _ in updates:
            setattr(row, attr, value)
        db.commit()
        print(f'轮换完成。旧加密字段已备份到 {backup}。请把新密钥配置为 BOTLY_ENCRYPTION_KEY 后重启 API/Worker。')


if __name__ == '__main__':
    main()
