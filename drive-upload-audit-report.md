# Drive upload audit report

Date: 2026-07-15
Project: web novel

## Overall verdict
The Drive integration is now structurally wired and the code path is much more robust than before, but it is not yet "fully production-perfect" or "fully bulletproof".

## What I verified
- I reviewed the Drive-related logic in [app.py](app.py), including:
  - the OAuth helper functions,
  - the refresh-token loading flow,
  - the upload helper for images and text,
  - the story creation and chapter creation routes.
- I also reviewed the regression tests in [tests/test_app.py](tests/test_app.py).
- VS Code diagnostics reported no syntax/editor errors in [app.py](app.py) and [tests/test_app.py](tests/test_app.py).

## What is working
- The app now tries to upload both story cover images and chapter text files to Google Drive when the target folder IDs and credentials are available.
- The upload helper uses the Google Drive API upload wrapper rather than a raw stream, which is the correct pattern for file upload.
- The implementation now supports both the underscore-based and hyphenated folder-variable names seen in the environment configuration, which avoids the prior silent folder-ID mismatch.
- The create-story and create-chapter routes still save a local copy first, so the app does not lose the file locally even if Drive upload fails.

## Remaining gaps / non-perfect areas
1. The upload path is still not guaranteed to succeed in real-world deployment.
   - The upload only works if:
     - the refresh token is valid,
     - the Google OAuth client ID/secret are valid,
     - the target Drive folder IDs are correct,
     - the folder is shared with the correct Google account,
     - the app can reach Google APIs from the deployment environment.

2. The app does not yet implement retry logic or backoff.
   - If the Drive API returns a transient error, the app will simply fail the upload and show a flash message.

3. The app does not persist Drive upload metadata in a durable store.
   - The file IDs are only stored in the in-memory `stories` list during the current runtime session.
   - Restarting the app or redeploying will lose that in-memory state.

4. The current flow still allows story/chapter creation to continue even if the Drive upload fails.
   - This is convenient for UX, but it can create a split-brain situation where the content exists locally but not in Drive.

5. The upload flow is still partly dependent on environment correctness.
   - In deployment, the environment variables must be set in the host platform itself, not only in a local `.env` file.

## Recommended next improvements
- Add explicit logging of the Drive upload result and the exact error text returned by Google.
- Persist Drive file IDs and uploaded paths into a durable store (database or JSON file), not only in memory.
- Make upload failure non-optional for critical content by either:
  - failing the story/chapter creation request when upload fails, or
  - implementing a retry queue and background task.
- Add a small health-check endpoint that verifies:
  - credentials can be created,
  - the Drive API can list files,
  - the configured folder IDs can be resolved.
- For deployment, verify that the Google Cloud OAuth redirect URI matches the deployed host exactly and that the folders are shared with the account behind the refresh token.

## Bottom line
The implementation is now better structured and the main code path is aligned with the correct Drive upload approach, but it is still not fully hardened for a production-grade, reliability-first deployment.
