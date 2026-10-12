"""Run with: python tools/preview_dashboard.py [--seed-only] [--port 8001]."""
import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Preview the dashboard with a separate sample database")
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--seed-only', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    database = root / 'build' / 'dashboard-preview.db'
    database.parent.mkdir(exist_ok=True)
    # Override deployment settings before importing the application.
    os.environ.update(DB_FILE=str(database), PREVIEW_MODE='true', LOCAL_TEST_MODE='true',
                      ADMIN_EMAIL='admin@preview.local', ADMIN_PASSWORD='Preview123!',
                      COOKIE_SECURE='false', ALERT_EMAIL_ENABLED='false',
                      AUTO_ENROLLMENT_ENABLED='false', TTN_API_KEY='', TB_PASSWORD='',
                      TTN_BASE_URL='', THINGSBOARD_URL='')
    from app.db.connection import get_db_connection, run_migrations
    from app.services.preview import seed_preview
    run_migrations()
    with get_db_connection() as conn:
        seed_preview(conn)
    from app.main import ensure_default_admin_user
    ensure_default_admin_user()
    print(f'Preview ready: http://localhost:{args.port}/admin')
    print('Admin: admin@preview.local / Preview123!')
    print('Clients: client1@preview.local or client2@preview.local / Preview123!')
    if not args.seed_only:
        import uvicorn
        uvicorn.run('app.main:app', host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
