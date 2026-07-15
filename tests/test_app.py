import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import app as app_module
from oauthlib.oauth2.rfc6749.errors import InsecureTransportError
from googleapiclient.http import MediaIoBaseUpload
from app import app, get_google_drive_redirect_uri, save_refresh_token, load_refresh_token, get_google_drive_credentials, upload_file_to_google_drive, create_backup_snapshot


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        app.config['TESTING'] = True
        app.config['UPLOAD_FOLDER'] = tempfile.mkdtemp()
        app.config['WTF_CSRF_ENABLED'] = False
        self.token_path = os.path.join(app.root_path, 'instance', 'google_drive_oauth.json')
        if os.path.exists(self.token_path):
            os.remove(self.token_path)
        with patch('app.GOOGLE_OAUTH_REFRESH_TOKEN', ''), patch.dict(os.environ, {'GOOGLE_OAUTH_REFRESH_TOKEN': ''}, clear=False):
            pass

    def test_home_page_renders(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Rose & Boss', response.data)
        self.assertIn(b'hero-carousel', response.data)

    def test_home_page_defaults_to_guest_mode_and_has_password_button(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'guest-active', response.data)
        self.assertIn(b'id="authToggle"', response.data)

    def test_google_drive_authorize_page_renders(self):
        with patch('app.GOOGLE_OAUTH_REFRESH_TOKEN', ''), patch.dict(os.environ, {'GOOGLE_OAUTH_REFRESH_TOKEN': ''}, clear=False):
            response = self.client.get('/google-drive/authorize')
        self.assertEqual(response.status_code, 302)
        self.assertIn('accounts.google.com', response.headers['Location'])

    def test_google_drive_authorization_url_includes_redirect_uri(self):
        with patch('app.GOOGLE_OAUTH_REFRESH_TOKEN', ''), patch.dict(os.environ, {'GOOGLE_OAUTH_REFRESH_TOKEN': ''}, clear=False):
            response = self.client.get('/google-drive/authorize')
        location = response.headers['Location']
        self.assertIn('redirect_uri=', location)
        self.assertIn('oauth%2Fcallback', location)

    def test_google_drive_authorize_ignores_placeholder_token(self):
        with patch.dict(os.environ, {'GOOGLE_OAUTH_REFRESH_TOKEN': 'your_google_oauth_refresh_token'}, clear=False):
            with patch('app.GOOGLE_OAUTH_REFRESH_TOKEN', 'your_google_oauth_refresh_token'):
                response = self.client.get('/google-drive/authorize')
        self.assertEqual(response.status_code, 302)
        self.assertIn('accounts.google.com', response.headers['Location'])

    def test_google_drive_authorize_starts_oauth_even_when_refresh_token_exists(self):
        with patch.dict(os.environ, {'GOOGLE_OAUTH_REFRESH_TOKEN': 'real-refresh-token'}, clear=False):
            with patch('app.GOOGLE_OAUTH_REFRESH_TOKEN', 'real-refresh-token'):
                response = self.client.get('/google-drive/authorize')
        self.assertEqual(response.status_code, 302)
        self.assertIn('accounts.google.com', response.headers['Location'])

    def test_google_drive_redirect_uri_uses_current_host(self):
        with app.test_request_context('/google-drive/authorize', base_url='http://127.0.0.1:5000'):
            self.assertEqual(get_google_drive_redirect_uri(), 'http://127.0.0.1:5000/oauth/callback')

    def test_google_drive_credentials_use_saved_refresh_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = os.path.join(tmpdir, 'google_drive_oauth.json')
            with patch('app.GOOGLE_OAUTH_TOKEN_FILE', token_file), patch('app.GOOGLE_OAUTH_CLIENT_ID', 'client-id'), patch('app.GOOGLE_OAUTH_CLIENT_SECRET', 'client-secret'):
                save_refresh_token('real-refresh-token')
                credentials = get_google_drive_credentials()
                self.assertIsNotNone(credentials)
                self.assertEqual(credentials.refresh_token, 'real-refresh-token')
                self.assertEqual(load_refresh_token(), 'real-refresh-token')

    def test_drive_folder_ids_support_hyphenated_env_names(self):
        with patch.dict(os.environ, {
            'GOOGLE_DRIVE_COVER-IMAGES_FOLDER_ID': 'cover-folder-id',
            'GOOGLE_DRIVE_NOVEL-CONTENT_FOLDER_ID': 'novel-folder-id',
        }, clear=False):
            cover_folder_id, novel_folder_id = app_module.get_google_drive_folder_ids()
        self.assertEqual(cover_folder_id, 'cover-folder-id')
        self.assertEqual(novel_folder_id, 'novel-folder-id')

    def test_upload_file_to_google_drive_uses_media_upload(self):
        class DummyCreateRequest:
            def __init__(self, result):
                self.result = result
                self.kwargs = None
            def execute(self):
                return self.result
        class DummyFiles:
            def __init__(self, result):
                self.result = result
                self.kwargs = None
            def create(self, **kwargs):
                self.kwargs = kwargs
                return DummyCreateRequest(self.result)
        class DummyService:
            def __init__(self, result):
                self._files = DummyFiles(result)
            def files(self):
                return self._files

        result = {'id': 'drive-file-id', 'name': 'cover.png'}
        service = DummyService(result)
        with patch('app.build', return_value=service), patch('app.get_google_drive_credentials', return_value=object()):
            uploaded = upload_file_to_google_drive(b'fake-bytes', 'cover.png', 'image/png', 'folder-id')

        self.assertEqual(uploaded['id'], 'drive-file-id')
        self.assertIsInstance(service._files.kwargs['media_body'], MediaIoBaseUpload)
        self.assertEqual(service._files.kwargs['body']['parents'], ['folder-id'])

    def test_google_drive_callback_handles_insecure_transport(self):
        with self.client.session_transaction() as session:
            session['oauth_state'] = 'test-state'

        class DummyFlow:
            def fetch_token(self, *args, **kwargs):
                raise InsecureTransportError()

        with patch('app.get_google_drive_flow', return_value=DummyFlow()):
            response = self.client.get('/oauth/callback?state=test-state', base_url='http://127.0.0.1:5000')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def test_create_backup_snapshot_writes_database_and_metadata_payloads(self):
        app_module.stories[:] = [{
            'id': 1,
            'title': 'Backup story',
            'author': 'Tester',
            'genre': 'Boylove',
            'description': 'Backup description',
            'cover': None,
            'cover_drive_id': None,
            'chapters': [{'title': 'Chapter 1', 'content': 'Hello', 'cover': None, 'chapter_drive_id': None, 'chapter_cover_drive_id': None}],
        }]

        with patch('app.get_google_drive_credentials', return_value=object()), patch('app.upload_text_to_google_drive', side_effect=[
            {'id': 'db-file-id', 'name': 'backup.json'},
            {'id': 'meta-file-id', 'name': 'meta.json'},
            {'id': 'version-file-id', 'name': 'version.json'},
            {'id': 'log-file-id', 'name': 'log.json'},
        ]) as upload_mock:
            result = create_backup_snapshot()

        self.assertEqual(result['database_file_id'], 'db-file-id')
        self.assertEqual(upload_mock.call_count, 4)
        self.assertEqual(result['story_count'], 1)
        self.assertEqual(result['chapter_count'], 1)
        self.assertEqual(result['folders']['database']['id'], 'db-file-id')
        self.assertEqual(result['folders']['metadata']['id'], 'meta-file-id')

    def test_create_backup_snapshot_keeps_only_ten_newest_files_per_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            instance_dir = os.path.join(tmpdir, 'instance')
            os.makedirs(instance_dir, exist_ok=True)
            backup_root = os.path.join(instance_dir, 'backups')
            for subfolder_name in app_module.BACKUP_SUBFOLDER_NAMES.values():
                folder_path = os.path.join(backup_root, subfolder_name)
                os.makedirs(folder_path, exist_ok=True)
                for index in range(11):
                    filename = f'{subfolder_name}_{index:02d}.json'
                    with open(os.path.join(folder_path, filename), 'w', encoding='utf-8') as handle:
                        handle.write('{}')

            with patch.object(app_module, 'instance_dir', instance_dir), patch.object(app_module, 'get_system_drive_folder_ids', return_value={}), patch('app.upload_text_to_google_drive', return_value={'id': 'drive-id', 'name': 'backup.json'}):
                create_backup_snapshot()

            for subfolder_name in app_module.BACKUP_SUBFOLDER_NAMES.values():
                folder_path = os.path.join(backup_root, subfolder_name)
                files = sorted([name for name in os.listdir(folder_path) if name.endswith('.json')])
                self.assertLessEqual(len(files), 10)
                self.assertTrue(any(name.startswith(f'{subfolder_name}_') for name in files))
                self.assertTrue(any(name.endswith('.json') for name in files))

    def test_auth_accepts_form_submission_for_admin_password(self):
        response = self.client.post('/auth', data={'password': '3,141592653589793'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"admin": true', response.data.lower())
        with self.client.session_transaction() as session:
            self.assertTrue(session.get('is_admin'))

    def test_post_page_renders(self):
        response = self.client.get('/post')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def test_post_page_uses_admin_session_for_template_state(self):
        with self.client.session_transaction() as session:
            session['is_admin'] = True

        response = self.client.get('/post')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'admin-active', response.data)

    def test_home_page_returns_guest_state_when_auth_cookie_is_removed(self):
        with self.client.session_transaction() as session:
            session['is_admin'] = True

        response = self.client.get('/', headers={'Cookie': 'is_admin=false'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'guest-active', response.data)

    def test_is_admin_uses_cookie_fallback(self):
        with app.test_request_context('/', headers={'Cookie': 'is_admin=true'}):
            self.assertTrue(app_module.is_admin())

    def test_create_story_with_text(self):
        response = self.client.post('/stories', data={
            'title': 'Test story',
            'author': 'Tester',
            'genre': 'Boylove',
            'tags': '#test',
            'description': 'A lovely story',
            'content': 'Some content'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Rose & Boss', response.data)
        self.assertIn('Bạn cần mật khẩu admin để truy cập tính năng này.'.encode('utf-8'), response.data)

    def test_create_story_uploads_cover_to_drive_when_configured(self):
        with self.client.session_transaction() as session:
            session['is_admin'] = True

        with patch('app.GOOGLE_DRIVE_COVERPICTURE_FOLDER_ID', 'cover-folder-id'), patch('app.upload_file_to_google_drive', return_value={'id': 'drive-cover-id'}) as upload_mock:
            response = self.client.post('/stories', data={
                'title': 'Drive story',
                'author': 'Tester',
                'genre': 'Boylove',
                'tags': '#drive',
                'description': 'A lovely story',
                'cover': (io.BytesIO(b'fake-image-data'), 'cover.png')
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')
        self.assertEqual(upload_mock.call_count, 1)
        self.assertEqual(upload_mock.call_args[0][3], 'cover-folder-id')

    def test_create_chapter_shortcut_shows_story_selection(self):
        self.client.post('/stories', data={
            'title': 'Shortcut story',
            'author': 'Tester',
            'genre': 'Boylove',
            'description': 'A lovely story',
            'content': 'Some content'
        }, follow_redirects=True)

        response = self.client.get('/chapters/new')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def test_delete_story_removes_story_and_drive_files(self):
        with self.client.session_transaction() as session:
            session['is_admin'] = True

        story = {
            'id': 999,
            'title': 'Delete Me',
            'author': 'Tester',
            'genre': 'Boylove',
            'description': 'A lovely story',
            'cover': 'cover.png',
            'cover_drive_id': 'story-cover-drive-id',
            'chapters': [{
                'title': 'Chapter 1',
                'content': 'Hello',
                'cover': 'chapter-cover.png',
                'chapter_drive_id': 'chapter-content-drive-id',
                'chapter_cover_drive_id': 'chapter-cover-drive-id',
            }],
        }
        app_module.stories.append(story)

        with patch('app.delete_file_from_google_drive') as delete_mock:
            response = self.client.post(f'/stories/{story["id"]}/delete')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')
        self.assertNotIn(story, app_module.stories)
        self.assertEqual(delete_mock.call_count, 2)

    def test_delete_chapter_removes_drive_files(self):
        with self.client.session_transaction() as session:
            session['is_admin'] = True

        story = {
            'id': 1000,
            'title': 'Delete Chapter',
            'author': 'Tester',
            'genre': 'Boylove',
            'description': 'A lovely story',
            'cover': None,
            'cover_drive_id': None,
            'chapters': [{
                'title': 'Chapter 1',
                'content': 'Hello',
                'cover': 'chapter-cover.png',
                'chapter_drive_id': 'chapter-content-drive-id',
                'chapter_cover_drive_id': 'chapter-cover-drive-id',
            }],
        }
        app_module.stories.append(story)

        with patch('app.delete_file_from_google_drive') as delete_mock:
            response = self.client.post('/stories/1000/chapters/0/delete')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')
        self.assertEqual(len(story['chapters']), 0)
        self.assertEqual(delete_mock.call_count, 2)


if __name__ == '__main__':
    unittest.main()
