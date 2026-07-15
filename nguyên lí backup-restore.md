# Backup/Restore Mechanism — Full Logic Summary

Tài liệu này ghi lại toàn bộ flow của cơ chế backup/restore trong ứng dụng, dựa trên việc đọc và phân tích các file liên quan: app.py, backup.py, config.py, models.py, google_drive.py, storage.py, routes/main.py, routes/posters.py, routes/media.py và các template liên quan.

## 1. Mục đích tổng thể

Hệ thống backup/restore có 2 nhiệm vụ chính:

1. Backup cơ sở dữ liệu SQLite lên Google Drive mỗi khi dữ liệu thay đổi.
2. Restore cơ sở dữ liệu từ Google Drive khi khởi động lại ứng dụng (đặc biệt quan trọng để khôi phục DB sau redeploy/restart).

Logic này được triển khai bằng lớp BackupManager ở file backup.py, kết hợp với Google Drive wrapper ở google_drive.py và hook vào khởi động Flask ở app.py.

---

## 2. Các file tham gia chính

- app.py
  - Tạo Flask app, khởi tạo storage, tạo BackupManager, gọi restore ở startup trước khi mở DB, khởi chạy backup thread, và ghi đè commit để đánh dấu DB dirty.
- backup.py
  - Chứa toàn bộ logic backup/restore: folder chuẩn bị, metadata, validate SQLite, upload/download, latest.json, pruning, state tracking, generation, thread worker.
- google_drive.py
  - Wrapper cho Google Drive API: upload/download text/binary, list/search files, create folder, delete file, metadata lookup.
- storage.py
  - Quản lý StorageAccount; account 0 dùng cho system backup/restore, các account khác dùng cho upload poster/video.
- config.py
  - Đọc cấu hình environment và cung cấp DATABASE_PATH, instance dir, Google Drive account config.
- models.py
  - Khởi tạo engine SQLite và bảng DB; backup mechanism dựa trên DB file SQLite và bảng backup_metadata trong file backup.
- routes/main.py
  - Cung cấp các route admin cho backup status và trigger backup thủ công.
- templates/dashboard/backup_status.html
  - UI hiển thị trạng thái backup/restore.

---

## 3. Khởi tạo BackupManager

Khi app khởi động, app.py tạo đối tượng BackupManager và gắn vào app:

- root_dir = thư mục gốc của project.
- metadata_dir = thư mục metadata/ ở root.
- version_dir = thư mục version/ ở root.
- logs_dir = thư mục logs/ ở root.
- state_path = metadata/database_state.json
- folders_cache_path = metadata/folders.json
- latest_path = metadata/latest.json
- log_path = logs/backup.log
- database_path = đường dẫn đến file SQLite chính (từ config DATABASE_PATH)
- backup_temp_dir = instance/backup_tmp

Trong __init__(), BackupManager thực hiện:

1. Tạo các thư mục metadata/, version/, logs/, backup_tmp/ nếu chưa có.
2. Gọi _initialize_version_file() để tạo version/schema.json nếu chưa tồn tại.
3. Gọi _load_or_initialize_state() để khởi tạo hoặc load state JSON.
4. Tạo database_uuid và device_id nếu chưa có.
5. Khởi tạo generation số lần thay đổi DB, generation_updated_at và các trạng thái last_backup_status/last_restore_status.

### 3.1 State file và generation

BackupManager dùng state JSON ở metadata/database_state.json để lưu:

- database_uuid
- device_id
- environment_type
- generation
- generation_updated_at

Generation được tăng mỗi lần database được marked dirty. Nói cách khác, generation là phiên bản dữ liệu hiện tại trong trạng thái local.

---

## 4. Vị trí backup trên Google Drive

BackupManager cần một Google Drive system folder ID để hoạt động. Nếu không có system folder ID thì backup/restore bị skip.

### 4.1 Các thư mục hệ thống tạo/đảm bảo

Trong system folder, BackupManager đảm bảo có các thư mục con sau:

- database: chứa các file backup SQLite.
- metadata: chứa latest.json và metadata phụ trợ.
- logs: chứa backup.log.
- version: chứa schema.json.

Cách hoạt động:

1. Đọc cache folders.json nếu tồn tại và hợp lệ.
2. Nếu chưa có hoặc root folder thay đổi, gọi _ensure_folder_ids() để kiểm tra từng folder con.
3. Nếu folder không tồn tại thì gọi _find_or_create_folder() để tạo folder mới trên Drive.
4. Cache lại ID của các folder vào metadata/folders.json.

### 4.2 Account backup

StorageManager đã cấu hình các account Google Drive. Trong app, account được dành riêng cho backup/restore hệ thống + dùng cho normal upload poster/video.

---

## 5. Flow restore khi app khởi động

Đây là phần quan trọng nhất. Trong app.py, trước khi gọi init_engine() và init_db(), ứng dụng gọi:

- backup_manager.perform_startup_restore()

Mục đích: đảm bảo DB file local được restore từ cloud trước khi SQLAlchemy mở DB engine, tránh trường hợp file đang bị mở/lock hoặc bị replace khi đang sử dụng.

### 5.1 Các bước perform_startup_restore()

1. Kiểm tra hệ thống có system folder ID hay không.
   - Nếu không có => skip restore và set status restore skipped.
2. Với lock operation, gọi _begin_operation("RESTORING").
3. Xác định các folder trên Drive bằng _ensure_system_folders().
4. Tải remote metadata từ metadata/latest.json trong Drive bằng _load_remote_latest().
5. Nếu latest.json không tồn tại hoặc lỗi parse, gọi _recover_latest_metadata() bằng cách scan các file SQLite backup trong database folder và cố gắng suy ra metadata từ nội dung của backup file.
6. Xác định local DB có hợp lệ không:
   - Nếu file tồn tại và _validate_sqlite(..., require_tables=True) trả về True thì local DB được xem là valid.
   - Nếu valid, đọc metadata trong DB bằng _read_sqlite_metadata(database_path) để lấy generation.
7. So sánh generation:
   - Nếu local DB valid và local_generation >= cloud_generation => skip restore.
   - Nếu local DB valid nhưng cũ hơn cloud => restore.
   - Nếu DB không tồn tại / không hợp lệ => restore.
8. Gọi _restore_backup(latest_metadata, ids) để tải file backup từ Drive và thay thế DB local.
9. Nếu restore bằng latest metadata thất bại vì invalid SQLite hoặc SHA mismatch, BackupManager sẽ thử các backup khác bằng _recover_latest_metadata() rồi gọi _restore_backup() lần nữa.
10. Cuối cùng set status restore success/fail/skipped.

### 5.2 Lý do phải restore trước khi init_db

Vì local SQLite database file là file vật lý trên disk và SQLAlchemy/engine sẽ mở file đó. Nếu restore xảy ra sau khi DB engine đã mở file, quá trình replace/overwrite file có thể dẫn tới lock hoặc failure. Vì vậy app.py gọi restore trước khi init_engine() và init_db().

---

## 6. Restore logic chi tiết

### 6.1 _restore_backup()

Khi restore được kích hoạt, BackupManager:

1. Lấy backup_file_id từ latest_metadata.
2. Tạo temp_restore_path trong instance/backup_tmp với tên dạng restore_<backup_filename>.sqlite3.
3. Gọi google_drive.download_file() để download backup file từ Drive vào temp path.
4. Nếu latest_metadata có sha256 thì hash lại file vừa download và compare với expected sha256.
   - Nếu mismatch => xóa file tạm và raise RuntimeError("Downloaded backup file SHA256 mismatch.").
5. Gọi _validate_sqlite(temp_restore_path, require_tables=True).
   - _validate_sqlite() dùng sqlite3 PRAGMA integrity_check; kiểm tra kết quả = "ok".
   - Nếu require_tables=True thì kiểm tra required tables: users, movies, posters, founders.
   - Nếu không hợp lệ => xóa file tạm và raise RuntimeError("Downloaded backup file is not a valid SQLite database.").
6. Gọi _validate_restore_metadata(latest_metadata).
   - Chỉ kiểm tra schema_version phải khớp với SCHEMA_VERSION.
   - Nếu local DB đang tồn tại và valid, so sánh UUID của backup với UUID hiện tại. Nếu khác nhau => raise RuntimeError.
7. Thay thế file DB local:
   - Nếu local DB tồn tại, rename file cũ thành <database_path>.old.
   - Dùng temp_restore_path.replace(actual_db_path) để đổi file backup vào vị trí file DB chính.
   - Xóa file .old nếu có.
8. Gọi _apply_restored_state(latest_metadata) để cập nhật local state generation, generation_updated_at, database_uuid, device_id, environment_type.

### 6.2 _validate_restore_metadata()

Logic validate metadata khi restore:

- schema_version trong backup phải đúng bằng SCHEMA_VERSION.
- Nếu DB cũ đã tồn tại và hợp lệ, UUID trong backup phải khớp với UUID của DB cũ; nếu không khớp thì restore bị từ chối để tránh vô tình thay DB bằng backup của một database khác.

---

## 7. Backup logic chi tiết

Backup được kích hoạt theo 2 cách:

1. Tự động sau mỗi commit thành công (dirty flag set).
2. Thủ công bằng route /dashboard/backup-status/trigger.

### 7.1 Trigger dirty flag

Trong app.py, db_session.commit bị ghi đè bằng hàm commit_with_backup():

- Gọi commit gốc.
- Nếu commit thành công, gọi backup_manager.mark_dirty().

mark_dirty() thực hiện:

1. Đánh dấu self.dirty = True.
2. Tăng generation lên 1.
3. Cập nhật generation_updated_at.
4. Ghi state json với generation mới.

### 7.2 Backup worker thread

BackupManager.start_backup_thread() khởi chạy một thread daemon chạy vòng lặp mỗi BACKUP_INTERVAL_SECONDS (600s):

- sleep 600s
- gọi create_backup_if_dirty()
- nếu lỗi, log lỗi vào backup.log

### 7.3 _perform_backup()

Khi backup được gọi, BackupManager kiểm tra:

1. Không có operation khác đang chạy (state != IDLE).
2. Có system folder ID hay không.
3. File DB local có tồn tại hay không.
4. Nếu không phải force, phải có dirty=True mới backup.

Sau đó:

1. Gọi _ensure_system_folders() để lấy id của database/metadata/logs/version folders.
2. Kiểm tra DB local hợp lệ bằng _validate_sqlite(database_path, require_tables=True).
3. Lấy cloud generation từ remote latest.json bằng _get_cloud_generation(metadata_folder_id).
   - Nếu cloud generation tồn tại và self.generation < cloud_generation thì backup bị hủy vì trên cloud đã có backup mới hơn. Đây là cách tránh overwrite backup mới hơn bằng local state cũ.
4. Tạo tên backup file theo mẫu:
   - movie_manager_{generation}_{timestamp}.sqlite3
   - timestamp bằng UTC format YYYYMMDD_HHMMSS
5. Tạo file backup tạm bằng _create_sqlite_backup(source, destination).
   - Đây không dùng raw file copy. BackupManager dùng sqlite3.connect(source) và sqlite3.connect(destination), rồi gọi source_conn.backup(dest_conn).
   - Cách này tạo một snapshot SQLite chuẩn, không bị ghi đè hoặc thiếu metadata.
6. Viết metadata vào backup file bằng _write_sqlite_metadata(temp_backup_path, sqlite_metadata).
   - Metadata này là table backup_metadata trong file SQLite.
   - Metadata gồm: database_uuid, generation, schema_version, application_version, backup_created_at.
7. Hash backup bằng SHA-256: _hash_file(temp_backup_path).
8. Upload file lên Google Drive bằng google_drive.upload_file(..., mime_type="application/x-sqlite3", ...).
9. Xác minh file upload không rỗng bằng get_file_metadata.
10. Tạo latest metadata bằng _build_latest_metadata(...).
11. Gọi _update_latest_metadata(latest_data, metadata_folder_id)
   - Ghi bản local metadata/latest.json ở máy.
   - Ghi bản remote latest.json lên Drive metadata folder.
12. Gọi _prune_old_backups(database_folder_id)
   - Nếu số file backup > MAX_BACKUPS (5), xóa các backup cũ nhất.
13. Set status backup success/fail, log kết quả, và reset dirty=False.

### 7.4 _write_sqlite_metadata()

BackupManager mở file SQLite backup và tạo bảng:

- backup_metadata (metadata_key TEXT PRIMARY KEY, metadata_value TEXT)

Sau đó chèn các metadata cần lưu dưới dạng key/value. Đây là cách backup file “nhúng” metadata vào chính bản sao DB để khi recover metadata từ file backup thì có thể đọc được generation/UUID/version.

### 7.5 _read_sqlite_metadata()

Khi cần đọc metadata của file backup đã download, BackupManager mở SQLite file và select từ bảng backup_metadata.

---

## 8. latest.json và remote metadata

latest.json là file metadata chính trên Drive, dùng để chỉ tới backup mới nhất.

### 8.1 Nội dung metadata

latest.json chứa các trường như:

- database_uuid
- database_generation
- backup_file_id
- backup_filename
- sha256
- schema_version
- application_version
- backup_created_at
- device_id
- environment_type
- database_size

### 8.2 Cập nhật latest.json

Quá trình backup sau khi upload xong gọi:

- _update_latest_metadata(latest_data, metadata_folder_id)

Trong hàm này:

1. Ghi local latest.json ở metadata/latest.json.
2. Gọi _write_remote_latest(latest_data, metadata_folder_id) để upload/update file latest.json trên Drive.
3. Nếu update remote latest.json thất bại, BackupManager cố gắng restore content cũ nếu có.

### 8.3 Khôi phục nếu latest.json bị mất

Nếu latest.json không tồn tại hoặc không parse được, perform_startup_restore sẽ không dừng. Thay vào đó, BackupManager sẽ:

- scan các file backup trong database folder,
- thử download từng file,
- validate SQLite file,
- đọc metadata bên trong file SQLite,
- thử khớp generation với tên file backup,
- chọn candidate ở generation lớn nhất hoặc newest để tạo metadata recovery.

Đây là fallback path để tránh mất dữ liệu chỉ vì latest.json bị thiếu hoặc corrupt.

---

## 9. Pruning backup files

Sau mỗi backup, BackupManager gọi _prune_old_backups(database_folder_id).

- Liệt kê toàn bộ file .sqlite3 trong database folder.
- Parse tên file theo mẫu movie_manager_{generation}_{timestamp}.sqlite3.
- Nếu số file > MAX_BACKUPS (5), xóa các bản cũ nhất theo generation và timestamp.
- Giữ lại tối đa 5 backup mới nhất.

Một điểm đáng chú ý: pruning chỉ áp dụng ở Drive folder database; metadata/latest.json vẫn được cập nhật để trỏ tới backup mới nhất không bị xóa.

---

## 10. Validate SQLite file

BackupManager có hàm _validate_sqlite(database_path, require_tables=False):

1. Kiểm tra file có tồn tại, không rỗng.
2. Mở sqlite3 connection.
3. Chạy PRAGMA integrity_check; nếu không trả về "ok" => invalid.
4. Nếu require_tables=True thì query sqlite_master để lấy list tables, check các table bắt buộc: users, movies, posters, founders.
5. Nếu thiếu table nào thì invalid.

Đây là cơ chế chặn backup/restore cho file bị hỏng hoặc không phải DB SQLite đúng chuẩn.

---

## 11. Flow từ UI

### 11.1 Trang backup status

Route /dashboard/backup-status (admin-only) render template backup_status.html.

Trang này hiển thị:

- last_backup_status
- last_backup_time
- last_backup_message
- last_restore_status
- last_restore_time
- last_restore_message

### 11.2 Trigger backup thủ công

Route /dashboard/backup-status/trigger (POST, admin-only) gọi:

- backup_manager.create_backup_now()

Nếu thành công => flash "Backup completed successfully.".
Nếu thất bại => flash "Backup failed. Check the backup status panel for details.".

---

## 12. Mongo/SQLite-specific detail: tại sao dùng backup API

BackupManager không dùng shutil.copyfile để copy DB file. Nó dùng:

- source_conn.backup(dest_conn)

Điều này cho phép tạo snapshot ổn định và đúng chuẩn SQLite.

Đặc biệt hữu ích khi ứng dụng có WAL mode và connection đang hoạt động. Backup API giúp tạo file sao chép mà vẫn giữ tính toàn vẹn dữ liệu, thay vì copy raw file đang có thể đang bị viết hoặc bị mở với trạng thái chưa flush.

---

## 13. Đồng bộ giữa backup và dữ liệu người dùng

Backup không được tạo ngay khi user thao tác DB trong route; thay vào đó hệ thống chỉ đánh dấu dirty sau commit thành công.

Ví dụ hành trình:

1. Một route tạo/sửa/xóa poster hoặc movie gọi db_session.commit().
2. Commit được thực hiện thành công.
3. Hàm commit_with_backup() được gọi.
4. BackupManager.mark_dirty() được gọi.
5. generation tăng.
6. Thread backup worker sau khoảng 600s sẽ có thể tạo backup nếu dirty=True.

Nếu database đã được backup rồi và dirty=False, hệ thống không backup lại ngay lập tức.

---

## 14. Điểm cần lưu ý khi đọc code

Mức logic backup/restore có một số điều rất quan trọng:

- Restore chỉ được thực hiện ở startup, trước khi engine mở DB.
- Backup/restore ghi trạng thái và generation vào local state file cũng như vào remote latest.json.
- latest.json là pointer nhanh; backup file thật sự chứa DB và metadata bên trong file.
- Nếu latest.json bị mất/invalid, backup still có thể được recover từ file backup trong Drive.
- Backup sử dụng account 0 cho hệ thống; uploads bình thường dùng account khác.
- UI backup status chỉ hiện trạng thái, không sửa DB trực tiếp.

---

## 15. Tóm lược hành trình end-to-end

### Backup end-to-end

1. Một thay đổi dữ liệu được commit vào SQLite thông qua SQLAlchemy.
2. Commit hook gọi mark_dirty().
3. BackupManager tăng generation và ghi state.
4. Backup thread phát hiện dirty=True sau interval.
5. BackupManager tạo snapshot DB bằng sqlite backup API.
6. BackupManager lưu metadata vào trong backup file.
7. File được upload lên Google Drive vào database folder.
8. latest.json trên Drive được cập nhật để trỏ tới backup vừa upload.
9. Các backup cũ bị prune nếu vượt quá 5 bản.

### Restore end-to-end

1. App khởi động, trước khi init_engine/init_db, BackupManager.perform_startup_restore() chạy.
2. BackupManager kiểm tra Google Drive system folder và metadata file latest.json.
3. Nếu latest.json không có, scan các file backup trong Drive và recover metadata từ backup file.
4. BackupManager download backup file, validate SHA-256 và SQLite integrity, kiểm tra required tables.
5. Nếu hợp lệ, file backup được đổi vào vị trí DB local để thay thế DB cũ.
6. State generation, UUID, timestamps được cập nhật từ metadata của backup.
7. App tiếp tục khởi tạo engine và mở DB từ file đã restore.

---

## 16. Kết luận

Cơ chế backup/restore trong project là một pipeline có 3 lớp:

1. Local state management
   - generation, UUID, timestamps, dirty flag, state JSON.
2. Remote backup storage
   - file backup SQLite trên Google Drive + latest.json metadata.
3. Startup restore orchestration
   - restore trước khi mở DB để tránh lỗi file lock/replace.

Đây là một hệ thống khá chặt chẽ: có validate SQLite, hash, metadata được lưu trong backup file, backup pruning, recovery fallback, UI status tracking và hook commit-based dirty marking.
