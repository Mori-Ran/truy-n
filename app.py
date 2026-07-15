from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session, jsonify
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from urllib.parse import urlencode
from datetime import timedelta, datetime, timezone
import io
import os
import json
import copy
import tempfile
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

os.environ.setdefault(
    'OAUTHLIB_INSECURE_TRANSPORT',
    '1' if os.getenv('FLASK_ENV') == 'development' or os.getenv('RUNNING_LOCAL') == '1' else '0'
)

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'rose-boss-secret')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

instance_dir = os.path.join(os.path.dirname(__file__), 'instance')
os.makedirs(instance_dir, exist_ok=True)

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '3,141592653589793')
stories = []
BACKUP_DIRECTORY_NAME = 'system'
BACKUP_SUBFOLDER_NAMES = {
    'database': 'database',
    'logs': 'logs',
    'metadata': 'metadata',
    'version': 'version',
}
BACKUP_THREAD = None
BACKUP_INTERVAL_SECONDS = 12 * 60
BACKUP_RETENTION_LIMIT = 5
BACKUP_COUNT_LIMIT = 5
BACKUP_THREAD_STOP_EVENT = threading.Event()
BACKUP_THREAD_STARTED = False
AUTO_BACKUP_PENDING = False
BACKUP_TRIGGER_EVENT = threading.Event()
BACKUP_STATUS = {
    'status': 'idle',
    'message': 'Chưa có hoạt động backup nào.',
    'timestamp': None,
}
RESTORE_STATUS = {
    'status': 'idle',
    'message': 'Chưa có hoạt động restore nào.',
    'timestamp': None,
}
STATUS_HISTORY = []

GOOGLE_OAUTH_CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID', '').strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET', '').strip()
GOOGLE_OAUTH_REFRESH_TOKEN = os.getenv('GOOGLE_OAUTH_REFRESH_TOKEN', '').strip()
GOOGLE_OAUTH_TOKEN_FILE = os.getenv('GOOGLE_OAUTH_TOKEN_FILE', os.path.join(instance_dir, 'google_drive_oauth.json')).strip()
GOOGLE_DRIVE_COVERPICTURE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_COVERPICTURE_FOLDER_ID', '').strip()
GOOGLE_DRIVE_NOVELCONTENT_FOLDER_ID = os.getenv('GOOGLE_DRIVE_NOVELCONTENT_FOLDER_ID', '').strip()

SCOPES = ['https://www.googleapis.com/auth/drive.file']


def _read_drive_folder_setting(*candidate_names):
    for env_name in candidate_names:
        value = (os.getenv(env_name, '') or '').strip()
        if value:
            return value
    return ''


def get_google_drive_folder_ids():
    cover_folder_id = _read_drive_folder_setting(
        'GOOGLE_DRIVE_COVERPICTURE_FOLDER_ID',
        'GOOGLE_DRIVE_COVER_PICTURE_FOLDER_ID',
        'GOOGLE_DRIVE_COVER_IMAGES_FOLDER_ID',
        'GOOGLE_DRIVE_COVER_IMAGE_FOLDER_ID',
        'GOOGLE_DRIVE_COVER-IMAGES_FOLDER_ID',
        'GOOGLE_DRIVE_COVER-IMAGE_FOLDER_ID',
        'GOOGLE_DRIVE_COVER_FOLDER_ID',
        'GOOGLE_DRIVE_COVER_FOLDER',
        'GOOGLE_DRIVE_COVERPICTURE_FOLDER',
    )
    novel_folder_id = _read_drive_folder_setting(
        'GOOGLE_DRIVE_NOVELCONTENT_FOLDER_ID',
        'GOOGLE_DRIVE_NOVEL_CONTENT_FOLDER_ID',
        'GOOGLE_DRIVE_NOVEL-CONTENT_FOLDER_ID',
        'GOOGLE_DRIVE_NOVEL_CONTENT_FOLDER_ID',
        'GOOGLE_DRIVE_NOVEL_FOLDER_ID',
        'GOOGLE_DRIVE_NOVEL_FOLDER',
        'GOOGLE_DRIVE_NOVELCONTENT_FOLDER',
    )
    return cover_folder_id, novel_folder_id


def get_system_drive_folder_ids():
    system_root_folder_id = (os.getenv('GOOGLE_DRIVE_SYSTEM_FOLDER_ID', '') or '').strip()
    if not system_root_folder_id:
        return {}
    credentials = get_google_drive_credentials()
    if not credentials:
        return {}
    try:
        service = build('drive', 'v3', credentials=credentials)
    except Exception as exc:
        app.logger.warning('Google Drive service unavailable for system folder discovery: %s', exc)
        return {}
    folder_ids = {}
    for folder_name in BACKUP_SUBFOLDER_NAMES.values():
        try:
            query = "mimeType='application/vnd.google-apps.folder' and trashed=false and name='{}' and '{}' in parents".format(folder_name, system_root_folder_id)
            result = service.files().list(q=query, fields='files(id,name)', pageSize=10).execute()
            existing = result.get('files') or []
            if existing:
                folder_ids[folder_name] = existing[0].get('id')
                continue
            created = service.files().create(
                body={
                    'name': folder_name,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [system_root_folder_id],
                },
                fields='id,name',
            ).execute()
            folder_ids[folder_name] = created.get('id')
        except Exception as exc:
            app.logger.warning('Failed to prepare Google Drive folder %s: %s', folder_name, exc)
    return folder_ids


def get_google_drive_credentials():
    refresh_token = load_refresh_token()
    if not refresh_token:
        app.logger.warning('Google Drive auth skipped because refresh token is empty.')
        return None
    if not GOOGLE_OAUTH_CLIENT_ID or not GOOGLE_OAUTH_CLIENT_SECRET:
        app.logger.warning('Google Drive auth skipped because OAuth client ID/secret is empty.')
        return None
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=GOOGLE_OAUTH_CLIENT_ID,
        client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=SCOPES,
    )


def get_google_drive_redirect_uri():
    host = request.host or '127.0.0.1:5000'
    if 'localhost' in host or '127.0.0.1' in host or '0.0.0.0' in host:
        return 'http://127.0.0.1:5000/oauth/callback'
    if request.is_secure:
        return f'https://{host}/oauth/callback'
    return f'http://{host}/oauth/callback'


def get_google_drive_flow():
    redirect_uri = get_google_drive_redirect_uri()
    return Flow.from_client_config(
        {
            'web': {
                'client_id': GOOGLE_OAUTH_CLIENT_ID,
                'client_secret': GOOGLE_OAUTH_CLIENT_SECRET,
                'redirect_uris': [redirect_uri],
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        },
        scopes=SCOPES,
        state=session.get('oauth_state'),
        redirect_uri=redirect_uri,
    )


def get_google_drive_authorization_url():
    state = session.get('oauth_state', '')
    params = {
        'client_id': GOOGLE_OAUTH_CLIENT_ID,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'redirect_uri': get_google_drive_redirect_uri(),
        'access_type': 'offline',
        'prompt': 'consent',
        'include_granted_scopes': 'true',
        'state': state,
    }
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params)


def save_refresh_token(refresh_token):
    global GOOGLE_OAUTH_REFRESH_TOKEN
    token_data = {
        'refresh_token': refresh_token,
        'client_id': GOOGLE_OAUTH_CLIENT_ID,
        'client_secret': GOOGLE_OAUTH_CLIENT_SECRET,
    }
    token_path = GOOGLE_OAUTH_TOKEN_FILE
    if not os.path.isabs(token_path):
        token_path = os.path.join(os.path.dirname(__file__), token_path)
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, 'w', encoding='utf-8') as fh:
        json.dump(token_data, fh, indent=2)
    GOOGLE_OAUTH_REFRESH_TOKEN = refresh_token
    os.environ['GOOGLE_OAUTH_REFRESH_TOKEN'] = refresh_token
    return token_path


def load_refresh_token():
    env_refresh = os.getenv('GOOGLE_OAUTH_REFRESH_TOKEN', '')
    if env_refresh and env_refresh not in {'your_google_oauth_refresh_token', 'your_refresh_token', 'placeholder'}:
        return env_refresh
    token_path = GOOGLE_OAUTH_TOKEN_FILE
    if not os.path.isabs(token_path):
        token_path = os.path.join(os.path.dirname(__file__), token_path)
    if os.path.exists(token_path):
        with open(token_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
            if data.get('refresh_token'):
                return data.get('refresh_token')
    if GOOGLE_OAUTH_REFRESH_TOKEN and GOOGLE_OAUTH_REFRESH_TOKEN not in {'your_google_oauth_refresh_token', 'your_refresh_token', 'placeholder'}:
        return GOOGLE_OAUTH_REFRESH_TOKEN
    return ''


def upload_file_to_google_drive(file_bytes, filename, mime_type, parent_folder_id=''):
    credentials = get_google_drive_credentials()
    if not credentials:
        raise RuntimeError('Google Drive credentials are not available yet. Please authorize the Drive flow first.')

    service = build('drive', 'v3', credentials=credentials)
    file_metadata = {'name': filename}
    parent_folder_id = (parent_folder_id or '').strip()
    if parent_folder_id:
        file_metadata['parents'] = [parent_folder_id]

    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype=mime_type or 'application/octet-stream',
        resumable=False,
    )
    try:
        uploaded_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,name,webViewLink,webContentLink,mimeType',
        ).execute()
        return uploaded_file
    except Exception as exc:
        app.logger.exception('Google Drive upload failed for %s', filename)
        raise RuntimeError(f'Google Drive upload failed for {filename}: {exc}') from exc


def upload_text_to_google_drive(text, filename, parent_folder_id=''):
    return upload_file_to_google_drive(text.encode('utf-8'), filename, 'text/plain', parent_folder_id)


def update_text_file_in_google_drive(file_id, text, filename, parent_folder_id=''):
    if not file_id:
        return None
    credentials = get_google_drive_credentials()
    if not credentials:
        raise RuntimeError('Google Drive credentials are not available yet. Please authorize the Drive flow first.')
    service = build('drive', 'v3', credentials=credentials)
    media = MediaIoBaseUpload(
        io.BytesIO(text.encode('utf-8')),
        mimetype='text/plain',
        resumable=False,
    )
    try:
        updated_file = service.files().update(
            fileId=file_id,
            body={'name': filename},
            media_body=media,
            fields='id,name,webViewLink,webContentLink,mimeType',
        ).execute()
        return updated_file
    except Exception as exc:
        app.logger.exception('Google Drive text update failed for %s', file_id)
        raise RuntimeError(f'Google Drive text update failed for {file_id}: {exc}') from exc


def delete_file_from_google_drive(file_id):
    if not file_id:
        return None
    credentials = get_google_drive_credentials()
    if not credentials:
        raise RuntimeError('Google Drive credentials are not available yet. Please authorize the Drive flow first.')
    service = build('drive', 'v3', credentials=credentials)
    try:
        return service.files().delete(fileId=file_id).execute()
    except Exception as exc:
        app.logger.exception('Google Drive delete failed for %s', file_id)
        raise RuntimeError(f'Google Drive delete failed for {file_id}: {exc}') from exc


def prune_old_drive_backups(service, folder_id, prefix):
    if not folder_id or not service:
        return
    try:
        result = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false and name contains '{prefix}'",
            fields='files(id,name,modifiedTime)',
            orderBy='modifiedTime desc',
            pageSize=100,
        ).execute()
    except Exception as exc:
        app.logger.warning('Failed to list Drive backups under folder %s: %s', folder_id, exc)
        return

    files = result.get('files') or []
    if len(files) <= BACKUP_RETENTION_LIMIT:
        return

    files_to_delete = files[BACKUP_RETENTION_LIMIT:]
    if not files_to_delete:
        return

    for old_file in files_to_delete:
        file_id = old_file.get('id')
        if not file_id:
            continue
        try:
            service.files().delete(fileId=file_id).execute()
            app.logger.info('Deleted old Drive backup %s from folder %s', old_file.get('name'), folder_id)
        except Exception as exc:
            app.logger.warning('Failed to delete old Drive backup %s: %s', file_id, exc)


def enforce_backup_count_limit(service, folder_id, prefix):
    if not folder_id or not service:
        return []
    try:
        result = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false and name contains '{prefix}'",
            fields='files(id,name,modifiedTime)',
            orderBy='modifiedTime desc',
            pageSize=100,
        ).execute()
    except Exception as exc:
        app.logger.warning('Failed to inspect backup count for folder %s: %s', folder_id, exc)
        return []

    files = result.get('files') or []
    if len(files) <= BACKUP_COUNT_LIMIT:
        return []

    files_to_delete = files[BACKUP_COUNT_LIMIT:]
    deleted = []
    for old_file in files_to_delete:
        file_id = old_file.get('id')
        if not file_id:
            continue
        try:
            service.files().delete(fileId=file_id).execute()
            deleted.append(old_file.get('name'))
            app.logger.info('Auto-pruned oldest backup %s from folder %s', old_file.get('name'), folder_id)
        except Exception as exc:
            app.logger.warning('Failed to auto-delete old backup %s: %s', old_file.get('name'), exc)
    return deleted


def create_backup_snapshot():
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    created_at = datetime.now(timezone.utc).isoformat()
    backup_payload = {
        'created_at': created_at,
        'version': 1,
        'story_count': len(stories),
        'chapter_count': sum(len(story.get('chapters', [])) for story in stories),
        'stories': copy.deepcopy(stories),
    }
    metadata_payload = {
        'created_at': created_at,
        'story_count': backup_payload['story_count'],
        'chapter_count': backup_payload['chapter_count'],
        'stories': [
            {
                'id': story.get('id'),
                'title': story.get('title'),
                'author': story.get('author'),
                'genre': story.get('genre'),
                'tags': story.get('tags', ''),
                'description': story.get('description'),
                'content': story.get('content', ''),
                'cover': story.get('cover'),
                'cover_drive_id': story.get('cover_drive_id'),
                'chapters': [
                    {
                        'title': chapter.get('title'),
                        'content': chapter.get('content', ''),
                        'cover': chapter.get('cover'),
                        'chapter_drive_id': chapter.get('chapter_drive_id'),
                        'chapter_drive_name': chapter.get('chapter_drive_name'),
                        'chapter_cover_drive_id': chapter.get('chapter_cover_drive_id'),
                    }
                    for chapter in story.get('chapters', [])
                ],
            }
            for story in stories
        ],
    }
    version_payload = {
        'backup_version': 1,
        'timestamp': created_at,
        'story_count': backup_payload['story_count'],
        'chapter_count': backup_payload['chapter_count'],
    }
    logs_payload = {
        'timestamp': created_at,
        'event': 'backup_created',
        'story_count': backup_payload['story_count'],
        'chapter_count': backup_payload['chapter_count'],
    }

    drive_folder_ids = get_system_drive_folder_ids() or {}
    credentials = get_google_drive_credentials()
    service = None
    if credentials:
        try:
            service = build('drive', 'v3', credentials=credentials)
        except Exception as exc:
            app.logger.warning('Google Drive service unavailable for backup upload: %s', exc)
            service = None

    uploaded_database = None
    uploaded_metadata = None
    uploaded_version = None
    uploaded_logs = None

    try:
        uploaded_database = upload_text_to_google_drive(
            json.dumps(backup_payload, ensure_ascii=False, indent=2),
            f'database_{timestamp}.json',
            drive_folder_ids.get('database', ''),
        )
        if service:
            prune_old_drive_backups(service, drive_folder_ids.get('database', ''), 'database_')
            enforce_backup_count_limit(service, drive_folder_ids.get('database', ''), 'database_')
    except Exception as exc:
        app.logger.warning('Database backup upload failed: %s', exc)
    try:
        uploaded_metadata = upload_text_to_google_drive(
            json.dumps(metadata_payload, ensure_ascii=False, indent=2),
            f'metadata_{timestamp}.json',
            drive_folder_ids.get('metadata', ''),
        )
        if service:
            prune_old_drive_backups(service, drive_folder_ids.get('metadata', ''), 'metadata_')
            enforce_backup_count_limit(service, drive_folder_ids.get('metadata', ''), 'metadata_')
    except Exception as exc:
        app.logger.warning('Metadata backup upload failed: %s', exc)
    try:
        uploaded_version = upload_text_to_google_drive(
            json.dumps(version_payload, ensure_ascii=False, indent=2),
            f'version_{timestamp}.json',
            drive_folder_ids.get('version', ''),
        )
        if service:
            prune_old_drive_backups(service, drive_folder_ids.get('version', ''), 'version_')
            enforce_backup_count_limit(service, drive_folder_ids.get('version', ''), 'version_')
    except Exception as exc:
        app.logger.warning('Version backup upload failed: %s', exc)
    try:
        uploaded_logs = upload_text_to_google_drive(
            json.dumps(logs_payload, ensure_ascii=False, indent=2),
            f'logs_{timestamp}.json',
            drive_folder_ids.get('logs', ''),
        )
        if service:
            prune_old_drive_backups(service, drive_folder_ids.get('logs', ''), 'logs_')
            enforce_backup_count_limit(service, drive_folder_ids.get('logs', ''), 'logs_')
    except Exception as exc:
        app.logger.warning('Logs backup upload failed: %s', exc)

    latest_payload = {
        'database_uuid': None,
        'database_generation': 1,
        'backup_file_id': (uploaded_database or {}).get('id') if uploaded_database else None,
        'backup_filename': (uploaded_database or {}).get('name') if uploaded_database else None,
        'sha256': None,
        'schema_version': 1,
        'application_version': '1.0.0',
        'backup_created_at': created_at,
        'device_id': None,
        'environment_type': 'development' if os.getenv('FLASK_ENV') == 'development' or os.getenv('RUNNING_LOCAL') == '1' else 'production',
        'database_size': None,
    }
    try:
        uploaded_latest = upload_text_to_google_drive(
            json.dumps(latest_payload, ensure_ascii=False, indent=2),
            'latest.json',
            drive_folder_ids.get('metadata', ''),
        )
    except Exception as exc:
        app.logger.warning('Latest metadata pointer upload failed: %s', exc)
        uploaded_latest = None

    return {
        'created_at': created_at,
        'database_file_id': (uploaded_database or {}).get('id') if uploaded_database else None,
        'metadata_file_id': (uploaded_metadata or {}).get('id') if uploaded_metadata else None,
        'version_file_id': (uploaded_version or {}).get('id') if uploaded_version else None,
        'log_file_id': (uploaded_logs or {}).get('id') if uploaded_logs else None,
        'story_count': backup_payload['story_count'],
        'chapter_count': backup_payload['chapter_count'],
        'folders': {
            'database': {'id': (uploaded_database or {}).get('id') if uploaded_database else None, 'name': 'database'},
            'metadata': {'id': (uploaded_metadata or {}).get('id') if uploaded_metadata else None, 'name': 'metadata'},
            'version': {'id': (uploaded_version or {}).get('id') if uploaded_version else None, 'name': 'version'},
            'logs': {'id': (uploaded_logs or {}).get('id') if uploaded_logs else None, 'name': 'logs'},
            'latest': {'id': (uploaded_latest or {}).get('id') if uploaded_latest else None, 'name': 'latest.json'},
        },
    }


def _load_local_backup_payload(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if _is_meaningful_backup_payload(payload):
        return payload
    return None


def restore_latest_local_backup():
    legacy_manifest_path = os.path.join(instance_dir, 'backup_manifest.json')
    manifest_payload = _load_local_backup_payload(legacy_manifest_path)
    if manifest_payload and _is_meaningful_backup_payload(manifest_payload):
        stories[:] = copy.deepcopy(manifest_payload.get('stories', []))
        return manifest_payload

    backup_root = os.path.join(instance_dir, 'backups')
    if not os.path.isdir(backup_root):
        return None

    candidate_payloads = []
    for subfolder_name in BACKUP_SUBFOLDER_NAMES.values():
        folder_path = os.path.join(backup_root, subfolder_name)
        if not os.path.isdir(folder_path):
            continue
        for filename in sorted(os.listdir(folder_path)):
            if not filename.endswith('.json'):
                continue
            payload = _load_local_backup_payload(os.path.join(folder_path, filename))
            if _is_meaningful_backup_payload(payload):
                candidate_payloads.append((os.path.join(folder_path, filename), payload))
    if not candidate_payloads:
        return None

    _, payload = candidate_payloads[-1]
    stories[:] = copy.deepcopy(payload.get('stories', []))
    return payload


def _is_meaningful_backup_payload(payload):
    if not isinstance(payload, dict):
        return False
    stories = payload.get('stories')
    if isinstance(stories, list):
        if len(stories) > 0:
            return True
        if payload.get('story_count', 0) > 0 or payload.get('chapter_count', 0) > 0:
            return True
    return False


def restore_latest_backup():
    drive_restored = restore_latest_backup_from_drive()
    if drive_restored:
        return drive_restored
    return restore_latest_local_backup()


def restore_latest_backup_from_drive():
    credentials = get_google_drive_credentials()
    if not credentials:
        return None
    try:
        service = build('drive', 'v3', credentials=credentials)
    except Exception as exc:
        app.logger.warning('Drive service unavailable during restore: %s', exc)
        return None

    folder_ids = get_system_drive_folder_ids() or {}
    database_folder_id = folder_ids.get('database', '')
    metadata_folder_id = folder_ids.get('metadata', '')
    if not database_folder_id or not metadata_folder_id:
        return None

    def _latest_valid_file(folder_id, prefix):
        if not folder_id:
            return None
        query = f"'{folder_id}' in parents and trashed=false and name contains '{prefix}'"
        result = service.files().list(q=query, fields='files(id,name,modifiedTime,size)', orderBy='modifiedTime desc', pageSize=20).execute()
        files = result.get('files') or []
        if not files:
            return None
        for file_info in files:
            size = int(file_info.get('size') or 0)
            if size <= 0:
                continue
            try:
                payload_bytes = service.files().get_media(fileId=file_info['id']).execute()
            except Exception as exc:
                app.logger.warning('Drive backup download failed for %s: %s', file_info.get('name'), exc)
                continue
            if not payload_bytes:
                continue
            try:
                payload = json.loads(payload_bytes.decode('utf-8'))
            except Exception:
                continue
            if _is_meaningful_backup_payload(payload):
                return payload, file_info
        return None

    database_result = _latest_valid_file(database_folder_id, 'database_')
    metadata_result = _latest_valid_file(metadata_folder_id, 'metadata_')
    if not database_result and not metadata_result:
        return None

    database_payload = None
    metadata_payload = None
    if database_result:
        database_payload, _ = database_result
    if metadata_result:
        metadata_payload, _ = metadata_result
    if _is_meaningful_backup_payload(database_payload):
        stories[:] = copy.deepcopy(database_payload.get('stories', []))
        return database_payload
    if _is_meaningful_backup_payload(metadata_payload):
        stories[:] = copy.deepcopy(metadata_payload.get('stories', []))
        return metadata_payload
    return None


def request_auto_backup():
    global AUTO_BACKUP_PENDING
    AUTO_BACKUP_PENDING = True
    BACKUP_TRIGGER_EVENT.set()


def start_periodic_backup_thread():
    global BACKUP_THREAD, BACKUP_THREAD_STARTED
    if BACKUP_THREAD_STARTED:
        return
    BACKUP_THREAD_STARTED = True

    def _backup_loop():
        global AUTO_BACKUP_PENDING
        while not BACKUP_THREAD_STOP_EVENT.is_set():
            if AUTO_BACKUP_PENDING:
                AUTO_BACKUP_PENDING = False
                BACKUP_TRIGGER_EVENT.clear()
                try:
                    create_backup_snapshot()
                    update_operation_status('backup', True, 'Backup tự động thành công vào Google Drive.')
                except Exception as exc:
                    update_operation_status('backup', False, f'Backup tự động thất bại: {exc}')
                    app.logger.warning('Periodic backup failed: %s', exc)
            if BACKUP_THREAD_STOP_EVENT.wait(BACKUP_INTERVAL_SECONDS):
                break
            if BACKUP_TRIGGER_EVENT.is_set():
                continue

    BACKUP_THREAD = threading.Thread(target=_backup_loop, daemon=True, name='backup-loop')
    BACKUP_THREAD.start()


def is_admin():
    return session.get('is_admin', False) is True


def require_admin():
    if not is_admin():
        flash('Để tạo bất cứ thứ gì, hãy về trang chủ bấm vào nút đăng nhập để có quyền.', 'error')
        return False
    return True


def update_operation_status(operation, success, message):
    global BACKUP_STATUS, RESTORE_STATUS, STATUS_HISTORY
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = {
        'operation': operation,
        'success': bool(success),
        'status': 'success' if success else 'fail',
        'message': message,
        'timestamp': timestamp,
    }
    STATUS_HISTORY.append(entry)
    if len(STATUS_HISTORY) > 10:
        STATUS_HISTORY = STATUS_HISTORY[-10:]
    if operation == 'backup':
        BACKUP_STATUS = entry
    else:
        RESTORE_STATUS = entry
    return entry


@app.route('/auth', methods=['POST'])
def authenticate():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict()
    password = (payload or {}).get('password', '').strip()
    if password == ADMIN_PASSWORD:
        session['is_admin'] = True
        session.permanent = True
        session.modified = True
        return jsonify({'admin': True, 'redirect': url_for('index')})

    session.pop('is_admin', None)
    session.modified = True
    return jsonify({'admin': False, 'redirect': url_for('index')})


@app.route('/logout', methods=['POST'])
def logout():
    session.pop('is_admin', None)
    session.modified = True
    flash('Đã đăng xuất khỏi chế độ admin.', 'info')
    return redirect(url_for('index'))


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {
        'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tif', 'tiff', 'avif', 'heic', 'heif'
    }


@app.route('/system/backup', methods=['POST'])
def backup_system():
    if not require_admin():
        return redirect(url_for('index'))
    try:
        create_backup_snapshot()
        update_operation_status('backup', True, 'Đã tạo bản backup mới và lưu vào Google Drive.')
        flash('Đã tạo bản backup mới và lưu vào thư mục backup trên Google Drive.', 'success')
    except Exception as exc:
        update_operation_status('backup', False, f'Không thể tạo backup: {exc}')
        flash(f'Không thể tạo backup: {exc}', 'error')
    return redirect(url_for('index'))


@app.route('/system/restore', methods=['POST'])
def restore_system():
    if not require_admin():
        return redirect(url_for('index'))
    try:
        restored = restore_latest_backup()
        if restored:
            update_operation_status('restore', True, 'Đã khôi phục bản backup gần nhất từ Google Drive vào trạng thái hiện tại.')
            flash('Đã khôi phục bản backup gần nhất từ Google Drive vào trạng thái hiện tại.', 'success')
        else:
            update_operation_status('restore', False, 'Không tìm thấy bản backup gần nhất trên Google Drive để khôi phục.')
            flash('Không tìm thấy bản backup gần nhất trên Google Drive để khôi phục.', 'error')
    except Exception as exc:
        update_operation_status('restore', False, f'Không thể khôi phục backup: {exc}')
        flash(f'Không thể khôi phục backup: {exc}', 'error')
    return redirect(url_for('index'))


@app.route('/status')
def status_page():
    if not require_admin():
        return redirect(url_for('index'))
    return render_template('status.html', stories=stories, is_admin=is_admin(), backup_status=BACKUP_STATUS, restore_status=RESTORE_STATUS, status_history=STATUS_HISTORY)


@app.route('/dashboard/backup-status')
def dashboard_backup_status():
    if not require_admin():
        return redirect(url_for('index'))
    return render_template('dashboard/backup_status.html', stories=stories, is_admin=is_admin(), backup_status=BACKUP_STATUS, restore_status=RESTORE_STATUS, status_history=STATUS_HISTORY)


@app.route('/dashboard/backup-status/trigger', methods=['POST'])
def dashboard_backup_status_trigger():
    if not require_admin():
        return redirect(url_for('index'))
    try:
        create_backup_snapshot()
        update_operation_status('backup', True, 'Backup thủ công hoàn tất và lưu vào Google Drive.')
        flash('Backup hoàn tất.', 'success')
    except Exception as exc:
        update_operation_status('backup', False, f'Backup thủ công thất bại: {exc}')
        flash(f'Backup thủ công thất bại: {exc}', 'error')
    return redirect(url_for('dashboard_backup_status'))


@app.route('/')
def index():
    return render_template('index.html', stories=stories, is_admin=is_admin())


@app.route('/google-drive/authorize')
def google_drive_authorize():
    if not GOOGLE_OAUTH_CLIENT_ID or not GOOGLE_OAUTH_CLIENT_SECRET:
        flash('Google Drive OAuth chưa được cấu hình đầy đủ.', 'error')
        return redirect(url_for('index'))

    saved_refresh_token = load_refresh_token()
    if saved_refresh_token:
        flash('Đã tìm thấy refresh token đã lưu. Đang mở lại quy trình OAuth để lấy token mới nếu cần.', 'info')

    import secrets
    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state
    authorization_url = get_google_drive_authorization_url()
    return redirect(authorization_url)


@app.route('/oauth/callback')
def google_drive_callback():
    state = request.args.get('state')
    if state != session.get('oauth_state'):
        flash('State không hợp lệ.', 'error')
        return redirect(url_for('index'))

    try:
        flow = get_google_drive_flow()
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials
        refresh_token = credentials.refresh_token
        if refresh_token:
            save_refresh_token(refresh_token)
            flash('Đã lưu refresh token Google Drive vào thư mục instance.', 'success')
        else:
            flash('Không nhận được refresh token. Hãy thử lại và cho phép quyền.', 'error')
    except Exception as exc:
        if 'insecure_transport' in str(exc).lower() or 'https' in str(exc).lower() and 'must utilize' in str(exc).lower():
            flash('Xử lý OAuth bị dừng vì callback đang chạy qua HTTP không an toàn. Hãy mở site bằng HTTPS hoặc cấu hình proxy/ngrok để callback dùng https.', 'error')
        else:
            flash(f'Xử lý OAuth thất bại: {exc}', 'error')
        session.pop('oauth_state', None)
        return redirect(url_for('index'))

    session.pop('oauth_state', None)
    return redirect(url_for('index'))


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/post')
def post_page():
    if not require_admin():
        return redirect(url_for('index'))
    return render_template('post.html', stories=stories, is_admin=is_admin())


@app.route('/stories/<int:story_id>')
def story_view(story_id):
    story = next((s for s in stories if s['id'] == story_id), None)
    if not story:
        flash('Không tìm thấy truyện.', 'error')
        return redirect(url_for('index'))
    return render_template('story_view.html', story=story, is_admin=is_admin())


@app.route('/stories/<int:story_id>/edit', methods=['GET', 'POST'])
def edit_story(story_id):
    if not require_admin():
        return redirect(url_for('index'))
    story = next((s for s in stories if s['id'] == story_id), None)
    if not story:
        flash('Không tìm thấy truyện.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        story['title'] = request.form.get('title', story['title']).strip() or story['title']
        story['author'] = request.form.get('author', story['author']).strip() or story['author']
        story['genre'] = request.form.get('genre', story['genre']).strip() or story['genre']
        story['tags'] = request.form.get('tags', story.get('tags', '')).strip()
        story['description'] = request.form.get('description', story['description']).strip() or story['description']
        request_auto_backup()
        try:
            create_backup_snapshot()
        except Exception as exc:
            app.logger.warning('Backup snapshot skipped after story edit: %s', exc)
        flash('Đã cập nhật truyện.', 'success')
        return redirect(url_for('story_view', story_id=story_id))

    return render_template('edit_story.html', story=story, is_admin=is_admin())


@app.route('/stories/<int:story_id>/delete', methods=['POST'])
def delete_story(story_id):
    if not require_admin():
        return redirect(url_for('index'))
    story = next((s for s in stories if s['id'] == story_id), None)
    if not story:
        flash('Không tìm thấy truyện.', 'error')
        return redirect(url_for('index'))

    for chapter in story.get('chapters', []):
        if chapter.get('chapter_drive_id'):
            try:
                delete_file_from_google_drive(chapter['chapter_drive_id'])
            except Exception as exc:
                flash(f'Không thể xóa nội dung chapter trên Drive: {exc}', 'error')
        if chapter.get('chapter_cover_drive_id'):
            try:
                delete_file_from_google_drive(chapter['chapter_cover_drive_id'])
            except Exception as exc:
                flash(f'Không thể xóa ảnh chapter trên Drive: {exc}', 'error')

    if story.get('cover_drive_id'):
        try:
            delete_file_from_google_drive(story['cover_drive_id'])
        except Exception as exc:
            flash(f'Không thể xóa ảnh bìa truyện trên Drive: {exc}', 'error')

    stories[:] = [s for s in stories if s['id'] != story_id]
    request_auto_backup()
    try:
        create_backup_snapshot()
    except Exception as exc:
        app.logger.warning('Backup snapshot skipped after story deletion: %s', exc)
    flash('Đã xóa truyện và các file liên quan trên Drive.', 'success')
    return redirect(url_for('index'))


@app.route('/stories/<int:story_id>/chapters/<int:chapter_index>')
def read_chapter(story_id, chapter_index):
    story = next((s for s in stories if s['id'] == story_id), None)
    if not story:
        flash('Không tìm thấy truyện.', 'error')
        return redirect(url_for('index'))
    if chapter_index < 0 or chapter_index >= len(story['chapters']):
        flash('Không tìm thấy chapter.', 'error')
        return redirect(url_for('story_view', story_id=story_id))
    return render_template('chapter_read.html', story=story, chapter=story['chapters'][chapter_index], is_admin=is_admin())


@app.route('/stories/<int:story_id>/chapters/<int:chapter_index>/edit', methods=['GET', 'POST'])
def edit_chapter(story_id, chapter_index):
    if not require_admin():
        return redirect(url_for('index'))
    story = next((s for s in stories if s['id'] == story_id), None)
    if not story or chapter_index < 0 or chapter_index >= len(story.get('chapters', [])):
        flash('Không tìm thấy chapter.', 'error')
        return redirect(url_for('index'))

    chapter = story['chapters'][chapter_index]
    if request.method == 'POST':
        old_title = chapter.get('title', '')
        old_content = chapter.get('content', '')
        new_title = request.form.get('chapter_title', old_title).strip() or old_title
        new_content = request.form.get('chapter_content', old_content).strip() or old_content
        old_filename = f"{secure_filename(story['title'])}-{secure_filename(old_title)}.txt"
        new_filename = f"{secure_filename(story['title'])}-{secure_filename(new_title)}.txt"
        chapter['title'] = new_title
        chapter['content'] = new_content

        _, novel_folder_id = get_google_drive_folder_ids()
        if new_title == old_title:
            if chapter.get('chapter_drive_id') and new_content != old_content:
                try:
                    update_text_file_in_google_drive(chapter['chapter_drive_id'], new_content, old_filename, novel_folder_id)
                except Exception as exc:
                    flash(f'Không thể cập nhật nội dung chapter trên Drive: {exc}', 'error')
        elif chapter.get('chapter_drive_id'):
            try:
                delete_file_from_google_drive(chapter['chapter_drive_id'])
            except Exception as exc:
                flash(f'Không thể xóa file chapter cũ trên Drive: {exc}', 'error')
            chapter['chapter_drive_id'] = None
            chapter['chapter_drive_name'] = None
            if novel_folder_id:
                try:
                    uploaded = upload_text_to_google_drive(new_content, new_filename, novel_folder_id)
                    if uploaded:
                        chapter['chapter_drive_id'] = uploaded.get('id')
                        chapter['chapter_drive_name'] = uploaded.get('name')
                except Exception as exc:
                    flash(f'Không thể tạo file chapter mới trên Drive: {exc}', 'error')
        request_auto_backup()
        try:
            create_backup_snapshot()
        except Exception as exc:
            app.logger.warning('Backup snapshot skipped after chapter edit: %s', exc)
        flash('Đã cập nhật chapter.', 'success')
        return redirect(url_for('story_view', story_id=story_id))

    return render_template('edit_chapter.html', story=story, chapter=chapter, chapter_index=chapter_index, is_admin=is_admin())


@app.route('/stories/<int:story_id>/chapters/<int:chapter_index>/delete', methods=['POST'])
def delete_chapter(story_id, chapter_index):
    if not require_admin():
        return redirect(url_for('index'))
    story = next((s for s in stories if s['id'] == story_id), None)
    if not story or chapter_index < 0 or chapter_index >= len(story.get('chapters', [])):
        flash('Không tìm thấy chapter.', 'error')
        return redirect(url_for('index'))

    chapter = story['chapters'][chapter_index]
    if chapter.get('chapter_drive_id'):
        try:
            delete_file_from_google_drive(chapter['chapter_drive_id'])
        except Exception as exc:
            flash(f'Không thể xóa nội dung chapter trên Drive: {exc}', 'error')
    if chapter.get('chapter_cover_drive_id'):
        try:
            delete_file_from_google_drive(chapter['chapter_cover_drive_id'])
        except Exception as exc:
            flash(f'Không thể xóa ảnh chapter trên Drive: {exc}', 'error')

    del story['chapters'][chapter_index]
    request_auto_backup()
    try:
        create_backup_snapshot()
    except Exception as exc:
        app.logger.warning('Backup snapshot skipped after chapter deletion: %s', exc)
    flash('Đã xóa chapter và các file liên quan trên Drive.', 'success')
    return redirect(url_for('index'))


@app.route('/stories', methods=['POST'])
def create_story():
    if not require_admin():
        return redirect(url_for('index'))

    title = request.form.get('title', '').strip()
    author = request.form.get('author', '').strip()
    genre = request.form.get('genre', '').strip()
    tags = request.form.get('tags', '').strip()
    description = request.form.get('description', '').strip()

    cover_file = request.files.get('cover')
    cover_name = None
    cover_drive_id = None
    _, novel_folder_id = get_google_drive_folder_ids()

    if cover_file and cover_file.filename:
        if not allowed_image(cover_file.filename):
            flash('Định dạng ảnh không được hỗ trợ. Vui lòng chọn ảnh hợp lệ.', 'error')
            return redirect(url_for('post_page'))
        filename = secure_filename(cover_file.filename)
        cover_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        cover_file.save(cover_path)
        cover_name = filename
        with open(cover_path, 'rb') as fh:
            cover_bytes = fh.read()
        cover_folder_id, _ = get_google_drive_folder_ids()
        app.logger.info('Story create cover upload: folder_id=%s filename=%s mime=%s', cover_folder_id, filename, cover_file.mimetype or 'application/octet-stream')
        if cover_folder_id:
            try:
                uploaded = upload_file_to_google_drive(cover_bytes, filename, cover_file.mimetype or 'application/octet-stream', cover_folder_id)
                if uploaded:
                    cover_drive_id = uploaded.get('id')
                    app.logger.info('Story cover uploaded to Drive with file id=%s', cover_drive_id)
                else:
                    app.logger.warning('Story cover upload returned empty result for %s', filename)
            except Exception as exc:
                app.logger.exception('Story cover upload failed for %s', filename)
                flash(f'Không thể upload ảnh bìa lên Google Drive: {exc}', 'error')
        else:
            app.logger.warning('Story cover upload skipped because no valid Drive cover folder ID was resolved.')

    if not title or not author or not genre or not description:
        flash('Vui lòng điền đầy đủ thông tin truyện.', 'error')
        return redirect(url_for('post_page'))

    story = {
        'id': len(stories) + 1,
        'title': title,
        'author': author,
        'genre': genre,
        'tags': tags,
        'description': description,
        'content': '',
        'cover': cover_name,
        'cover_drive_id': cover_drive_id,
        'chapters': []
    }
    stories.append(story)
    request_auto_backup()
    try:
        create_backup_snapshot()
    except Exception as exc:
        app.logger.warning('Backup snapshot skipped after story create: %s', exc)

    flash('Thank you for sharing your story with the world 🌷', 'success')
    return redirect(url_for('index'))


@app.route('/chapters/new')
def new_chapter_shortcut():
    if not is_admin():
        return redirect(url_for('index'))
    if not stories:
        flash('Tạo một truyện trước khi thêm chapter.', 'error')
        return redirect(url_for('post_page'))
    return render_template('choose_story.html', stories=stories, is_admin=is_admin())


@app.route('/stories/<int:story_id>/chapters/new')
def new_chapter_page(story_id):
    if not is_admin():
        return redirect(url_for('index'))
    story = next((s for s in stories if s['id'] == story_id), None)
    if not story:
        flash('Không tìm thấy truyện.', 'error')
        return redirect(url_for('index'))
    return render_template('chapter.html', story=story, is_admin=is_admin())


@app.route('/stories/<int:story_id>/chapters', methods=['POST'])
def create_chapter(story_id):
    if not is_admin():
        return redirect(url_for('index'))

    story = next((s for s in stories if s['id'] == story_id), None)
    if not story:
        flash('Không tìm thấy truyện.', 'error')
        return redirect(url_for('index'))

    chapter_title = request.form.get('chapter_title', '').strip()
    chapter_content = request.form.get('chapter_content', '').strip()
    chapter_cover = request.files.get('chapter_cover')
    chapter_cover_name = None
    chapter_cover_drive_id = None
    cover_folder_id, _ = get_google_drive_folder_ids()

    if chapter_cover and chapter_cover.filename:
        if not allowed_image(chapter_cover.filename):
            flash('Định dạng ảnh chapter không được hỗ trợ.', 'error')
            return redirect(url_for('index'))
        filename = secure_filename(chapter_cover.filename)
        chapter_cover_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        chapter_cover.save(chapter_cover_path)
        chapter_cover_name = filename
        with open(chapter_cover_path, 'rb') as fh:
            chapter_cover_bytes = fh.read()
        app.logger.info('Chapter create cover upload: folder_id=%s filename=%s mime=%s', cover_folder_id, filename, chapter_cover.mimetype or 'application/octet-stream')
        if cover_folder_id:
            try:
                uploaded = upload_file_to_google_drive(chapter_cover_bytes, filename, chapter_cover.mimetype or 'application/octet-stream', cover_folder_id)
                if uploaded:
                    chapter_cover_drive_id = uploaded.get('id')
                    app.logger.info('Chapter cover uploaded to Drive with file id=%s', chapter_cover_drive_id)
                else:
                    app.logger.warning('Chapter cover upload returned empty result for %s', filename)
            except Exception as exc:
                app.logger.exception('Chapter cover upload failed for %s', filename)
                flash(f'Không thể upload ảnh chapter lên Google Drive: {exc}', 'error')
        else:
            app.logger.warning('Chapter cover upload skipped because no valid Drive cover folder ID was resolved.')

    if not chapter_title or not chapter_content:
        flash('Vui lòng nhập tiêu đề và nội dung chapter.', 'error')
        return redirect(url_for('new_chapter_page', story_id=story_id))

    chapter_filename = f"{secure_filename(story['title'])}-{secure_filename(chapter_title)}.txt"
    chapter_drive_file = None
    _, novel_folder_id = get_google_drive_folder_ids()
    if novel_folder_id:
        try:
            chapter_drive_file = upload_text_to_google_drive(chapter_content, chapter_filename, novel_folder_id)
        except Exception as exc:
            flash(f'Không thể upload nội dung chapter lên Google Drive: {exc}', 'error')

    story['chapters'].append({
        'title': chapter_title,
        'content': chapter_content,
        'cover': chapter_cover_name,
        'chapter_drive_id': chapter_drive_file.get('id') if chapter_drive_file else None,
        'chapter_drive_name': chapter_drive_file.get('name') if chapter_drive_file else None,
        'chapter_cover_drive_id': chapter_cover_drive_id,
    })
    request_auto_backup()
    try:
        create_backup_snapshot()
    except Exception as exc:
        app.logger.warning('Backup snapshot skipped after chapter create: %s', exc)
    flash('Tạo chapter thành công ✨', 'success')
    return redirect(url_for('index'))


def initialize_backup_system():
    try:
        restored = restore_latest_backup()
        if restored:
            app.logger.info('Restored latest backup snapshot at startup')
    except Exception as exc:
        app.logger.warning('Backup restore skipped at startup: %s', exc)

    try:
        start_periodic_backup_thread()
    except Exception as exc:
        app.logger.warning('Periodic backup thread could not be started: %s', exc)


initialize_backup_system()


if __name__ == '__main__':
    host = os.getenv('FLASK_RUN_HOST', '127.0.0.1' if os.getenv('RAILWAY_ENVIRONMENT') is None else '0.0.0.0')
    port = int(os.getenv('PORT', os.getenv('FLASK_RUN_PORT', '5000')))
    debug = os.getenv('FLASK_ENV') == 'development' or os.getenv('RUNNING_LOCAL') == '1'
    app.run(host=host, port=port, debug=debug, use_reloader=False)
