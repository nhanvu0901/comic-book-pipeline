# Sửa clip không có video — 2026-11-04

File `_oCf8qmnL0k_sec_73.0_80.0.mp4` trên Windows có 116800 byte, duration 7 giây và chỉ AAC audio. Mốc 73–80 hợp lệ trong video nguồn 165 giây. Chưa chứng minh nguyên nhân bên trong yt-dlp/ffmpeg làm mất hình. Lỗi phục hồi đã xác định: normalize ném lỗi trước khi nhánh fallback tải whole video được chạy.

Terra sửa chung trong stages/clip_fetch.py: probe cache thay vì chỉ kiểm tra tồn tại; cách ly cache không dùng được bằng hậu tố .invalid-*; ưu tiên nguồn toàn bộ hợp lệ và đủ khoảng cần thiết; bắt lỗi normalize section để fallback whole video; giữ cả hai nguyên nhân nếu cả hai cách thất bại. Cache có hình nhưng quá ngắn được giữ lại, tải nguồn thay thế vào thư mục tạm. Không hardcode ID video.

Commit worktree: e2fa143 + db63a5a, branch codex/fix-clip-video-stream-2026-11-04 tại /tmp/comic-clip-stream-20261104. Reviewer chấp thuận sau khi bổ sung kiểm tra cache tải dở.

Đã áp dụng patch riêng lên server, giữ thay đổi có sẵn. Backup: D:\code\cbp-deploy-backups\clip_fetch_before_stream_fix_20261104.py. Windows: 15 focused tests passed. Nguồn đầy đủ đã phục hồi tại projects/spider-man_wolverine_2_2025/review/clips/src/_oCf8qmnL0k.mp4 (11.8 MB, 1280x718, 165.194s).

Smoke test thực tế trên Windows (cấm gọi mạng khi đã có nguồn cache): lấy đoạn bắt đầu73, end78 + margin2 tạo section7s 1280x718, dựng preview5s 1080x1920 thành công. Artifact kiểm tra nằm D:\code\cbp-deploy-backups\clip_stream_smoke_20261104. Không thay đổi manifest hoặc tự chọn clip thay người dùng.

Workspace chính: 15 tests passed, git diff --check sạch. Đã restart ComicUI, PID5488; API localhost8550 trả HTTP200, TTS completed=true, error=null.
