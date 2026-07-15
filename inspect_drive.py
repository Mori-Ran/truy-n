import os
import app
from google.auth.transport.requests import Request

print('client id present', bool(app.GOOGLE_OAUTH_CLIENT_ID))
print('client secret present', bool(app.GOOGLE_OAUTH_CLIENT_SECRET))
print('refresh token present', bool(app.GOOGLE_OAUTH_REFRESH_TOKEN))
print('folder ids', app.get_google_drive_folder_ids())
creds = app.get_google_drive_credentials()
print('credentials object present', creds is not None)
if creds:
    try:
        creds.refresh(Request())
        print('refreshed ok', bool(creds.token))
        service = app.build('drive', 'v3', credentials=creds)
        result = service.files().list(pageSize=3, fields='files(id,name)').execute()
        print('list ok', result)
    except Exception as exc:
        print('drive error', type(exc).__name__, exc)
